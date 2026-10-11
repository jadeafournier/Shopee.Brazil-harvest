# -*- coding: utf-8 -*-
"""BR large-scale batch crawl — human-like browsing + account protection.

Proven path: headed Patchright + login cookies + passive SPA-signed get_pc intercept.
Never curl / page.fetch — those always return 90309999 on BR.

Account protection:
  * Human warmup (home scroll/mouse) before first PDP each browser session.
  * Human read behavior on PDP while waiting for get_pc.
  * Occasional homepage visits between products.
  * Slower, jittered gaps — no burst loading.
  * STOP immediately on captcha or traffic_error (never proxy-retry account blocks).
  * Soft shift quota (default 60 OK) — stop before heat so cookies stay usable.
  * Rotate subscriber proxy only on proxy/no_data failures — not on traffic_error.
  * Refresh browser context periodically (same cookies) like closing/reopening Chrome.
  * Persist harvested cookies to session file only — never BR TEST.txt.
  * Never forge X-Sap-Sec / Af-Ac-Enc-* / Sz-Token / AC_CERT_D — browser mints only.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_probe import BR_TEST, parse_br_test, parse_url_ids, summarize_data
from br_cookies import (
    br_context_kwargs,
    enforce_br_locale,
    normalize_br_cookies,
    pw_cookie_list,
)
from br_pdp_methods import (
    classify_api_url,
    is_ok_payload,
    match_item_id,
    parse_html_partial,
)
import br_human as human
import captcha_mint as mint
import proxy_pool

URL_FILE = Path(__file__).resolve().parent / "br_active_product_urls_1000.txt"
OUT = Path(__file__).resolve().parent / "br_batch_output_1000"
SESSION_LIVE = ROOT / "shopee_getpc_crawler" / "sessions" / "BR" / "br_client4_live.json"
DOM = "shopee.com.br"
VIEW_W, VIEW_H = mint.VIEW_W, mint.VIEW_H

GETPC_WAIT_S = 30.0
VERIFY_POLL_S = 20.0
STRIP_CERT_KEYS = ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A")

# Human-paced safety (override with --fast for debugging only)
SESSION_CHUNK = 5            # URLs per subscriber before proxy rotate
BROWSER_REFRESH_URLS = 45    # new browser context, same cookies (like reopen Chrome)
HOME_EVERY_N = 10             # brief homepage visit every N successful URLs
FAIL_ROTATE = 1
MAX_PROXY_RETRIES = 1
CHUNK_COOLDOWN_S = (12.0, 24.0)
URL_GAP_S = (6.0, 16.0)
PRE_URL_JITTER_S = (0.4, 1.8)
ACCOUNT_CIRCUIT = 1          # stop on first account-risk signal
CAPTCHA_STOP = True
TRAFFIC_STOP = True          # stop on first traffic_error — do not burn account
# Soft quota: stop BEFORE heat (~90–210) so AC_CERT / session stay usable.
DEFAULT_SHIFT_QUOTA = 40


def _log(msg: str) -> None:
    print(msg, flush=True)


def _jitter(lo: float, hi: float) -> float:
    return random.uniform(lo, hi)


def _load_urls(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _out_path(shop_id: int, item_id: int) -> Path:
    return OUT / f"output_{shop_id}_{item_id}.json"


def _save(url: str, data: dict, *, method: str | None = None) -> None:
    sid, iid = parse_url_ids(url)
    OUT.mkdir(parents=True, exist_ok=True)
    _out_path(sid, iid).write_text(json.dumps({
        "task_link": url, "token": "br_batch_crawl", "api_key": "br_batch_crawl",
        "bff_meta": {"extract_method": method or "browser_get_pc"},
        "error": None, "error_msg": None, "data": data,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _persist_cookies(cookies: dict) -> None:
    SESSION_LIVE.parent.mkdir(parents=True, exist_ok=True)
    safe = enforce_br_locale(cookies)
    payload = {
        "account_name": "br_client4_live",
        "region": "BR",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "cookies": safe,
    }
    SESSION_LIVE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_summary(stats: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stats["proxy_pool"] = proxy_pool.stats_summary(top=5)
    (OUT / "batch_summary.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _outcome_from_meta(meta: dict) -> str:
    if meta.get("has_data"):
        return "ok"
    st = meta.get("status") or "no_data"
    if st == "traffic_error":
        return "traffic"
    if st == "captcha":
        return "captcha"
    if any(a.get("err") == 90309999 for a in meta.get("getpc_attempts", [])):
        return "blocked"
    if st == "proxy_err":
        return "proxy_err"
    return "no_data"


def _is_account_risk(meta: dict) -> bool:
    return meta.get("status") in ("captcha", "traffic_error")


def _is_proxy_fail(meta: dict) -> bool:
    return meta.get("status") in ("proxy_err", "no_data", "browser_closed") and not _is_account_risk(meta)


class BatchSession:
    """One browser + one optimized subscriber slot."""

    def __init__(
        self,
        cookies: dict,
        *,
        exclude: set[str] | None = None,
        keep_subscriber: str | None = None,
        human_mode: bool = True,
    ):
        self.work = dict(cookies)
        self.exclude = exclude or set()
        self.human_mode = human_mode
        self.keep_subscriber = keep_subscriber
        self.proxy, self.proxy_raw, self.sub_key = mint._proxy_dict("BR", exclude=self.exclude)
        if keep_subscriber and self.sub_key != keep_subscriber:
            # Browser refresh: try to keep same subscriber slot when possible.
            pass
        if not self.proxy:
            raise RuntimeError("no proxy available")
        self._p = None
        self._ctx = None
        self.page = None
        self.warmed = False
        self.urls_on_sub = 0
        self.urls_on_browser = 0

    def __enter__(self):
        from patchright.sync_api import sync_playwright

        self._p = sync_playwright().start()
        udd = tempfile.mkdtemp(prefix="brhum_")
        # Always normalize before inject — exports often have Asia/Taipei + en.
        self.work = enforce_br_locale(self.work)
        ctx_kw = br_context_kwargs()
        self._ctx = self._p.chromium.launch_persistent_context(
            udd, channel="chrome", headless=False, proxy=self.proxy,
            viewport={"width": VIEW_W, "height": VIEW_H},
            locale=ctx_kw["locale"],
            timezone_id=ctx_kw["timezone_id"],
            extra_http_headers=ctx_kw["extra_http_headers"],
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        self._ctx.add_cookies(pw_cookie_list(self.work, fresh_cert=False))
        self.page = self._ctx.new_page()
        return self

    def __exit__(self, *_):
        for closer in (self._ctx, self._p):
            try:
                if closer:
                    closer.close() if hasattr(closer, "close") else closer.stop()
            except Exception:
                pass

    def _sync_cookies(self) -> None:
        ck = {c["name"]: c["value"] for c in self._ctx.cookies()}
        for k, v in ck.items():
            if v and v not in ("-", "deleted"):
                if k == "AC_CERT_D" or k not in STRIP_CERT_KEYS:
                    self.work[k] = v
        # Browser may rewrite ssr-tz/language — force BR again after every sync.
        self.work = enforce_br_locale(self.work)

    def _warm_once(self) -> None:
        if self.warmed:
            return
        if self.human_mode:
            human.warm_home(self.page, DOM)
        else:
            try:
                self.page.goto(f"https://{DOM}/", wait_until="commit", timeout=60000)
            except Exception:
                pass
            human.pause(self.page, 1800, 3200)
        self.warmed = True

    def fetch(self, url: str) -> tuple[dict | None, dict]:
        """Extract product data via every non-device browser path available.

        Priority: get_pc → get_rw → item/get → HTML/OG/JSON-LD partial.
        """
        shop_id, item_id = parse_url_ids(url)
        pdp = url if url.startswith("http") else f"https://{DOM}/product/{shop_id}/{item_id}"
        holder: dict = {
            "attempts": [],
            "get_pc": None,
            "get_rw": None,
            "item_get": None,
            "method": None,
        }
        t0 = time.time()
        meta: dict = {
            "shop_id": shop_id, "item_id": item_id,
            "subscriber": (self.sub_key or "")[-72:],
        }

        def on_pdp_api(r):
            kind = classify_api_url(r.url)
            if not kind or str(item_id) not in r.url:
                return
            try:
                j = json.loads(r.text())
            except Exception:
                return
            err = j.get("error")
            signed = bool((r.request.headers or {}).get("x-sap-sec"))
            holder["attempts"].append({"api": kind, "err": err, "signed": signed})
            if not is_ok_payload(j):
                return
            data = j["data"]
            if kind in ("get_pc", "get_rw") and not match_item_id(data, item_id):
                return
            # Keep first good payload per endpoint; prefer get_pc later.
            if holder.get(kind) is None:
                holder[kind] = data

        self.page.on("response", on_pdp_api)
        try:
            if not self.warmed:
                self._warm_once()
            time.sleep(_jitter(*PRE_URL_JITTER_S))
            try:
                self.page.goto(pdp, wait_until="commit", timeout=90000)
            except Exception:
                pass

            deadline = time.time() + GETPC_WAIT_S + VERIFY_POLL_S
            reloaded = 0
            last_human = 0.0

            def _have_full() -> bool:
                return bool(holder.get("get_pc") or holder.get("get_rw") or holder.get("item_get"))

            while time.time() < deadline and not holder.get("get_pc"):
                u = self.page.url or ""
                if "/verify/captcha" in u:
                    meta["status"] = "captcha"
                    break
                if "/verify/traffic/error" in u:
                    meta["status"] = "traffic_error"
                    break
                if "/verify/traffic" in u:
                    human.traffic_challenge(self.page, VERIFY_POLL_S)
                    if not holder.get("get_pc") and reloaded < 1:
                        reloaded += 1
                        try:
                            self.page.goto(pdp, wait_until="commit", timeout=60000)
                        except Exception:
                            pass
                        human.pause(self.page, 800, 1600)
                else:
                    # get_rw / item_get may already be enough to stop early after min wait
                    if _have_full() and (time.time() - t0) > 8.0 and not holder.get("get_pc"):
                        break
                    if self.human_mode and time.time() - last_human > 1.2:
                        human.read_pdp(self.page)
                        last_human = time.time()
                    else:
                        human.pause(self.page, 350, 900)

            data = holder.get("get_pc")
            method = "browser_get_pc"
            if data is None and holder.get("get_rw") is not None:
                data = holder["get_rw"]
                method = "browser_get_rw"
            if data is None and holder.get("item_get") is not None:
                data = holder["item_get"]
                method = "browser_item_get"
            if data is None and meta.get("status") not in ("captcha", "traffic_error"):
                try:
                    html = self.page.content()
                except Exception:
                    html = ""
                partial = parse_html_partial(html, shop_id=shop_id, item_id=item_id)
                if partial:
                    data = {"item": {
                        "item_id": item_id,
                        "shop_id": shop_id,
                        "title": partial.get("title"),
                        "name": partial.get("title"),
                        "price": partial.get("price"),
                        "currency": partial.get("currency") or "BRL",
                        "image": partial.get("image"),
                        "description": partial.get("description"),
                    }, "partial": partial}
                    method = "html_partial"

            if data and self.human_mode and method.startswith("browser_"):
                human.after_success(self.page)

            holder["method"] = method if data else None
            self._sync_cookies()
            self.urls_on_sub += 1
            self.urls_on_browser += 1
            meta.update({
                "has_data": bool(data),
                "extract_method": holder.get("method"),
                "final_url": (self.page.url or "")[:90],
                "getpc_attempts": holder.get("attempts", [])[-6:],
                "apis_seen": sorted({a.get("api") for a in holder.get("attempts", []) if a.get("api")}),
                "ac_cert": bool(self.work.get("AC_CERT_D")),
                "elapsed_s": round(time.time() - t0, 1),
                "status": meta.get("status") or ("ok" if data else "no_data"),
            })
        finally:
            try:
                self.page.remove_listener("response", on_pdp_api)
            except Exception:
                pass

        return (data if meta.get("has_data") else None), meta


def run_batch(
    urls: list[str],
    cookies: dict,
    *,
    stats: dict | None = None,
    human_mode: bool = True,
    stop_on_traffic: bool = True,
    shift_quota: int = 0,
    on_ok=None,
    on_risk=None,
) -> dict:
    """Crawl URLs with account protection.

    shift_quota: stop after N successes this shift (0 = unlimited).
    on_ok(cookies): optional callback after each success (persist pool).
    on_risk(reason, cookies): optional callback on captcha/traffic_error.
    """
    stats = stats or {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "total": len(urls),
        "ok": 0,
        "fail": 0,
        "skipped": 0,
        "captcha_hits": 0,
        "traffic_errors": 0,
        "blocked_90309999": 0,
        "subscriber_rotations": 0,
        "browser_refreshes": 0,
        "human_mode": human_mode,
        "shift_quota": shift_quota or None,
        "stopped_reason": None,
        "items": {},
    }
    consec_fail = 0
    account_fail = 0
    session: BatchSession | None = None
    bad_subs: set[str] = set()
    work = dict(cookies)
    urls_since_home = 0

    def open_session(*, keep_sub: str | None = None, browser_only: bool = False) -> BatchSession:
        nonlocal stats
        proxy_pool.set_region("BR")
        exclude = set() if browser_only else bad_subs
        sess = BatchSession(work, exclude=exclude, keep_subscriber=keep_sub, human_mode=human_mode)
        if not browser_only:
            stats["subscriber_rotations"] = stats.get("subscriber_rotations", 0) + 1
        else:
            stats["browser_refreshes"] = stats.get("browser_refreshes", 0) + 1
        kind = "browser refresh" if browser_only else "subscriber"
        _log(f"  {kind}: ...{(sess.sub_key or '?')[-48:]}")
        return sess

    def close_session(sess: BatchSession | None, *, quarantine_sub: bool) -> None:
        if not sess:
            return
        work.update(sess.work)
        _persist_cookies(work)
        if quarantine_sub and sess.sub_key:
            bad_subs.add(sess.sub_key)
        sess.__exit__(None, None, None)

    def rotate_session(reason: str, *, quarantine_sub: bool = True) -> None:
        nonlocal session, consec_fail
        _log(f"  rotate proxy ({reason})")
        close_session(session, quarantine_sub=quarantine_sub)
        time.sleep(_jitter(*CHUNK_COOLDOWN_S))
        session = open_session().__enter__()
        consec_fail = 0

    def refresh_browser(reason: str) -> None:
        nonlocal session, urls_since_home
        _log(f"  refresh browser ({reason})")
        sub = session.sub_key if session else None
        close_session(session, quarantine_sub=False)
        time.sleep(_jitter(8.0, 18.0))
        session = open_session(keep_sub=sub, browser_only=True).__enter__()
        session.warmed = False
        urls_since_home = 0

    try:
        session = open_session().__enter__()
        for idx, url in enumerate(urls):
            shop_id, item_id = parse_url_ids(url)
            key = f"{shop_id}_{item_id}"

            if _out_path(shop_id, item_id).is_file():
                stats["skipped"] += 1
                stats["items"][key] = {"status": "skipped_existing"}
                continue

            if session.urls_on_sub >= SESSION_CHUNK:
                rotate_session(f"chunk {SESSION_CHUNK}", quarantine_sub=False)
            elif session.urls_on_browser >= BROWSER_REFRESH_URLS:
                refresh_browser(f"every {BROWSER_REFRESH_URLS} URLs")
            elif consec_fail >= FAIL_ROTATE:
                rotate_session(f"{consec_fail} consecutive proxy fails", quarantine_sub=True)

            if human_mode and urls_since_home >= HOME_EVERY_N:
                urls_since_home = human.maybe_visit_home(session.page, DOM, urls_since_home, HOME_EVERY_N)

            _log(f"[{idx + 1}/{len(urls)}] i.{shop_id}.{item_id}")

            data, meta = None, {}
            for attempt in range(1 + MAX_PROXY_RETRIES):
                if attempt > 0:
                    if _is_account_risk(meta):
                        break
                    rotate_session("retry after proxy fail", quarantine_sub=True)
                try:
                    data, meta = session.fetch(url)
                except Exception as exc:
                    # Closed Chrome / crashed context — reopen browser, do not burn account.
                    err = str(exc)
                    _log(f"  browser crash: {err[:120]}")
                    meta = {
                        "status": "browser_closed",
                        "detail": err[:200],
                        "shop_id": shop_id,
                        "item_id": item_id,
                    }
                    data = None
                    try:
                        refresh_browser("after browser_closed")
                    except Exception as exc2:
                        _log(f"  refresh failed: {exc2!r}")
                        rotate_session("refresh failed after browser_closed", quarantine_sub=True)
                outcome = _outcome_from_meta(meta)
                if session.sub_key:
                    proxy_pool.record(session.sub_key, outcome)
                if data or _is_account_risk(meta) or attempt >= MAX_PROXY_RETRIES:
                    break
                if meta.get("status") == "browser_closed":
                    continue

            stats["items"][key] = meta
            if meta.get("status") == "captcha":
                stats["captcha_hits"] += 1
            if meta.get("status") == "traffic_error":
                stats["traffic_errors"] += 1
            if any(a.get("err") == 90309999 for a in meta.get("getpc_attempts", [])):
                stats["blocked_90309999"] += 1

            if data:
                method = meta.get("extract_method") or "browser_get_pc"
                full_api = method in ("browser_get_pc", "browser_get_rw", "browser_item_get")
                _save(url, data, method=method)
                work.update(session.work)
                work = enforce_br_locale(work)
                stats.setdefault("by_method", {})
                stats["by_method"][method] = stats["by_method"].get(method, 0) + 1
                if full_api:
                    stats["ok"] += 1
                    consec_fail = 0
                    account_fail = 0
                    urls_since_home += 1
                else:
                    stats["partial_ok"] = stats.get("partial_ok", 0) + 1
                    consec_fail = 0
                try:
                    summary = summarize_data(data)
                except Exception:
                    summary = method
                _log(f"  OK [{method}] {summary} ({meta.get('elapsed_s')}s)")
                if stats["ok"] % 5 == 0:
                    _persist_cookies(work)
                if on_ok and full_api:
                    try:
                        on_ok(work)
                    except Exception:
                        pass
                # Soft quota — stop while cookies are still healthy (full APIs only).
                if full_api and shift_quota > 0 and stats["ok"] >= shift_quota:
                    stats["stopped_reason"] = "shift_quota_protect_cookies"
                    _log(
                        f"STOP: soft shift quota {stats['ok']}/{shift_quota} — "
                        "cookies preserved for next shift after cooldown."
                    )
                    _persist_cookies(work)
                    _write_summary(stats)
                    break
            else:
                stats["fail"] += 1
                if _is_account_risk(meta):
                    account_fail += 1
                    consec_fail = 0
                elif _is_proxy_fail(meta):
                    consec_fail += 1
                    account_fail = max(0, account_fail - 1)
                else:
                    consec_fail += 1
                _log(f"  FAIL {meta}")

            if CAPTCHA_STOP and meta.get("status") == "captcha":
                stats["stopped_reason"] = "captcha_protect_account"
                work.update(session.work if session else {})
                _persist_cookies(work)
                if on_risk:
                    try:
                        on_risk("captcha", work)
                    except Exception:
                        pass
                _log("STOP: captcha — account cookies cooled. Do not retry until cooldown.")
                _write_summary(stats)
                break
            if stop_on_traffic and TRAFFIC_STOP and meta.get("status") == "traffic_error":
                stats["stopped_reason"] = "traffic_protect_account"
                work.update(session.work if session else {})
                _persist_cookies(work)
                if on_risk:
                    try:
                        on_risk("traffic_error", work)
                    except Exception:
                        pass
                _log("STOP: traffic_error — account cookies cooled. Do not hammer this session.")
                _write_summary(stats)
                break
            if account_fail >= ACCOUNT_CIRCUIT:
                stats["stopped_reason"] = "account_circuit_breaker"
                work.update(session.work if session else {})
                _persist_cookies(work)
                if on_risk:
                    try:
                        on_risk(meta.get("status") or "account_risk", work)
                    except Exception:
                        pass
                _log("STOP: account-risk signal — session protected.")
                _write_summary(stats)
                break

            if idx + 1 < len(urls):
                time.sleep(_jitter(*URL_GAP_S))

            if (idx + 1) % 10 == 0:
                stats["updated_at"] = datetime.now(timezone.utc).isoformat()
                stats["progress"] = f"{idx + 1}/{len(urls)}"
                _persist_cookies(work)
                _write_summary(stats)
    finally:
        close_session(session, quarantine_sub=False)

    stats["finished_at"] = datetime.now(timezone.utc).isoformat()
    stats["success_rate"] = round(stats["ok"] / max(1, stats["ok"] + stats["fail"]), 4)
    _write_summary(stats)
    return stats


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BR batch crawl — human-like + account-safe")
    ap.add_argument("--urls", type=Path, default=URL_FILE)
    ap.add_argument("--only-missing", action="store_true", default=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--fast", action="store_true", help="disable human browsing (debug only)")
    ap.add_argument("--no-stop-traffic", action="store_true", help="keep going after traffic_error")
    ap.add_argument(
        "--shift-quota", type=int, default=DEFAULT_SHIFT_QUOTA,
        help="stop after N OK URLs to protect cookies (0=unlimited, default 60)",
    )
    args = ap.parse_args()

    out_dir = args.out or OUT
    globals()["OUT"] = out_dir

    cookies, proxies, _ = parse_br_test(BR_TEST)
    work = normalize_br_cookies(cookies, fresh_cert=False)
    px = ROOT / "proxy(BR shopee).txt"
    if proxies:
        px.write_text("\n".join(proxies) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()
    out_dir.mkdir(parents=True, exist_ok=True)
    _persist_cookies(work)

    urls = _load_urls(args.urls)
    if args.only_missing:
        urls = [u for u in urls if not _out_path(*parse_url_ids(u)).is_file()]
    if args.limit > 0:
        urls = urls[: args.limit]

    if not urls:
        _log("Nothing to crawl.")
        return 0

    human_mode = not args.fast
    quota = max(0, int(args.shift_quota))
    _log("=" * 70)
    _log("BR crawl — HUMAN-LIKE + ACCOUNT PROTECTION (cookie-preserving)")
    _log(f"  URLs: {len(urls)} | human={human_mode} | chunk={SESSION_CHUNK} | browser_refresh={BROWSER_REFRESH_URLS}")
    _log(f"  gap={URL_GAP_S[0]:.0f}-{URL_GAP_S[1]:.0f}s | home_every={HOME_EVERY_N} | stop_traffic={not args.no_stop_traffic}")
    _log(f"  shift_quota={quota or 'off'} (soft stop before heat — preserves AC_CERT / session)")
    _log(f"  Cookies: SPC_U={work.get('SPC_U')} AC_CERT={'yes' if work.get('AC_CERT_D') else 'no'}")
    _log(f"  Output: {out_dir}")
    _log(f"  Live session: {SESSION_LIVE}")
    _log("=" * 70)

    stats = run_batch(
        urls, work,
        human_mode=human_mode,
        stop_on_traffic=not args.no_stop_traffic,
        shift_quota=quota,
    )
    _log(
        f"\nDone: {stats['ok']} ok, {stats['fail']} fail, "
        f"{stats['skipped']} skipped | rate={stats['success_rate']} "
        f"| stop={stats.get('stopped_reason')}"
    )
    return 0 if stats["fail"] == 0 and not stats.get("stopped_reason") else 1


if __name__ == "__main__":
    raise SystemExit(main())
