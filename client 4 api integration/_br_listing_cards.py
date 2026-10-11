# -*- coding: utf-8 -*-
"""Harvest rich product CARDS from BR listing APIs (no iOS device).

Uses the same browser-signed search/shop/recommend APIs as URL harvest, but
saves structured fields (title, price, sold, rating, image) — not just URLs.

This is the high-volume path when full get_pc is rate-limited:
  ~hundreds of cards/min without opening each PDP.

Output: br_listing_cards.jsonl  (one JSON object per line)
Also appends product URLs into br_active_product_urls_100k.txt when new.

Usage:
  python "_br_listing_cards.py" --target 50000 --fast
  python "_br_listing_cards.py" --status
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
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_probe import BR_TEST, parse_br_test  # noqa: E402
from _br_url_harvest import SEARCH_KEYWORDS, SORT_MODES  # noqa: E402
from br_cookies import br_context_kwargs, pw_cookie_list  # noqa: E402
from br_pdp_methods import extract_listing_cards  # noqa: E402
import br_human as human  # noqa: E402
import captcha_mint as mint  # noqa: E402
import proxy_pool  # noqa: E402

DIR = Path(__file__).resolve().parent
OUT_JSONL = DIR / "br_listing_cards.jsonl"
STATE_FILE = DIR / "br_listing_cards_state.json"
URL_FILE = DIR / "br_active_product_urls_100k.txt"
DOM = "shopee.com.br"
BASE = f"https://{DOM}"
LISTING_RE = (
    "search_items", "recommend/recommend", "rcmd_items",
    "search/search_filter", "shop/get_shop_base",
)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sync_proxies() -> None:
    _, proxies, _ = parse_br_test(BR_TEST)
    px = ROOT / "proxy(BR shopee).txt"
    if proxies:
        tmp = px.with_suffix(".tmp")
        tmp.write_text("\n".join(proxies) + "\n", encoding="utf-8")
        tmp.replace(px)
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()


def _load_seen_keys() -> set[tuple[int, int]]:
    seen: set[tuple[int, int]] = set()
    if OUT_JSONL.is_file():
        for line in OUT_JSONL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                seen.add((int(o["shop_id"]), int(o["item_id"])))
            except Exception:
                pass
    return seen


def _count_cards() -> int:
    if not OUT_JSONL.is_file():
        return 0
    return sum(1 for ln in OUT_JSONL.read_text(encoding="utf-8").splitlines() if ln.strip())


def _load_state() -> dict[str, Any]:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"keyword_idx": 0, "page": 0, "sort_idx": 0, "stopped_reason": None}


def _save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _iso()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_url_keys() -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    if not URL_FILE.is_file():
        return keys
    from _br_probe import parse_url_ids
    for line in URL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            keys.add(parse_url_ids(line))
        except Exception:
            pass
    return keys


def _append_cards(
    cards: dict[tuple[int, int], dict],
    seen: set[tuple[int, int]],
    url_keys: set[tuple[int, int]],
) -> int:
    if not cards:
        return 0
    new_rows = []
    new_urls = []
    for key, card in cards.items():
        if key in seen:
            continue
        seen.add(key)
        card = dict(card)
        card["harvested_at"] = _iso()
        new_rows.append(json.dumps(card, ensure_ascii=False))
        if key not in url_keys:
            url_keys.add(key)
            new_urls.append(card["url"])
    if not new_rows:
        return 0
    with OUT_JSONL.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(new_rows) + "\n")
    if new_urls:
        with URL_FILE.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(new_urls) + "\n")
    return len(new_rows)


class CardSession:
    def __init__(self, cookies: dict, *, fast: bool = False):
        self.work = dict(cookies)
        self.fast = fast
        self.proxy, _, _ = mint._proxy_dict("BR")
        if not self.proxy:
            raise RuntimeError("no BR proxy")
        self._p = None
        self._ctx = None
        self.page = None
        self._batch: dict[tuple[int, int], dict] = {}
        self.risk = False
        self.risk_reason = ""
        self.spa_hot = False

    def __enter__(self):
        from patchright.sync_api import sync_playwright

        self._p = sync_playwright().start()
        udd = tempfile.mkdtemp(prefix="brcards_")
        ctx_kw = br_context_kwargs()
        self._ctx = self._p.chromium.launch_persistent_context(
            udd, channel="chrome", headless=False, proxy=self.proxy,
            viewport={"width": mint.VIEW_W, "height": mint.VIEW_H},
            locale=ctx_kw["locale"],
            timezone_id=ctx_kw["timezone_id"],
            extra_http_headers=ctx_kw["extra_http_headers"],
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        self._ctx.route("**/*", self._route)
        self._ctx.add_cookies(pw_cookie_list(self.work, fresh_cert=False))
        self.page = self._ctx.new_page()
        self.page.on("response", self._on_response)
        return self

    def __exit__(self, *_):
        for closer in (self._ctx, self._p):
            try:
                if closer:
                    closer.close() if hasattr(closer, "close") else closer.stop()
            except Exception:
                pass

    def _route(self, route) -> None:
        try:
            rtype = route.request.resource_type
            if rtype in ("image", "media", "font", "ping", "beacon"):
                route.abort()
                return
            route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    def _on_response(self, resp) -> None:
        url = resp.url
        if self.risk:
            return
        if "/verify/traffic" in url or "/verify/captcha" in url:
            self.risk = True
            self.risk_reason = "traffic_error" if "traffic" in url else "captcha"
            return
        if not any(x in url for x in LISTING_RE):
            return
        try:
            if resp.status != 200:
                return
            data = resp.json()
        except Exception:
            return
        if isinstance(data, dict) and data.get("error") == 90309999:
            return
        extract_listing_cards(data, self._batch)

    def drain(self) -> dict[tuple[int, int], dict]:
        out = dict(self._batch)
        self._batch.clear()
        return out

    def _api_timeout_s(self) -> float:
        # search_items 발화까지 신선한 내비게이션은 ~20-40s 걸린다. 성공 페이지는
        # 카드 도착 즉시 루프를 빠져나가므로(>=12), 긴 상한은 빈 페이지에만 비용.
        return 38.0

    def warmup(self) -> None:
        """홈 워밍업 + 검색 SPA 1회 프라임(콜드 로드 후 핫 경로)."""
        human.warm_home(self.page, DOM)
        try:
            self.page.wait_for_timeout(2500 if self.fast else 4000)
        except Exception:
            time.sleep(3)
        _log("  프라이밍: 검색 SPA 콜드 로드(최초 1회, 최대 40s)…")
        self.search("camiseta", 0, "sales")
        if self._batch:
            self.spa_hot = True
            _log(f"  SPA 핫 — {len(self._batch)} 카드 프라임됨(이후 짧은 대기)")
        else:
            _log("  SPA 아직 콜드 — 초기 키워드는 긴 대기 사용")

    def search(self, keyword: str, page_no: int, sort_by: str) -> None:
        self._batch.clear()
        url = f"{BASE}/search?keyword={quote(keyword)}&page={page_no}&sortBy={sort_by}"
        # 단일 내비게이션 후, 응답 핸들러(_on_response)가 카드를 채울 때까지 대기.
        try:
            self.page.goto(url, wait_until="commit", timeout=45000)
        except Exception:
            pass
        timeout_s = self._api_timeout_s()
        deadline = time.time() + timeout_s
        scrolled = False
        while time.time() < deadline:
            if self.risk:
                return
            if len(self._batch) >= 12:
                break
            # 로딩 도중 1회 스크롤로 lazy 발화 유도.
            if not scrolled and time.time() > deadline - timeout_s + 2.5:
                try:
                    self.page.evaluate(
                        "window.scrollTo(0, Math.min(2200, document.body.scrollHeight))"
                    )
                except Exception:
                    pass
                scrolled = True
            try:
                self.page.wait_for_timeout(200)
            except Exception:
                time.sleep(0.2)
        if self._batch:
            self.spa_hot = True
        if "/verify/captcha" in (self.page.url or ""):
            self.risk = True
            self.risk_reason = "captcha"


def harvest_cards(
    target: int,
    *,
    fast: bool = True,
    cookies_override: dict | None = None,
    account_id: str | None = None,
    shift_cards: int = 0,
    shift_pages: int = 0,
) -> int:
    """Harvest listing cards.

    Return codes:
      0 target reached / keywords exhausted
      1 error (no cookie)
      2 risk (captcha / traffic) — cool this account
      3 shift budget reached (cards/pages) — rotate account, no penalty
    """
    if cookies_override is not None:
        cookies = dict(cookies_override)
    else:
        cookies, _, _ = parse_br_test(BR_TEST)
    if not cookies.get("SPC_U"):
        _log("no SPC_U cookie")
        return 1
    _sync_proxies()
    seen = _load_seen_keys()
    url_keys = _load_url_keys()
    state = _load_state()
    start = _count_cards()
    if start >= target:
        _log(f"Already have {start:,} cards (target {target:,})")
        return 0

    _log("=" * 70)
    _log("BR LISTING CARDS (no iOS) — search/recommend API cards")
    _log(f"  existing={start:,} target={target:,} SPC_U={cookies.get('SPC_U')}")
    if account_id:
        _log(f"  account={account_id} shift_cards={shift_cards or '∞'} shift_pages={shift_pages or '∞'}")
    _log(f"  output={OUT_JSONL}")
    _log("=" * 70)

    gap = (0.2, 0.5) if fast else (1.5, 3.5)
    ki = int(state.get("keyword_idx") or 0)
    pg = int(state.get("page") or 0)
    sort_idx = int(state.get("sort_idx") or 0)

    shift_added = 0
    shift_pg = 0
    rotate = {"now": False}

    def _budget_hit() -> bool:
        if shift_cards > 0 and shift_added >= shift_cards:
            rotate["now"] = True
            return True
        if shift_pages > 0 and shift_pg >= shift_pages:
            rotate["now"] = True
            return True
        return False

    with CardSession(cookies, fast=fast) as sess:
        sess.warmup()
        while ki < len(SEARCH_KEYWORDS) and _count_cards() < target:
            if sess.risk or _budget_hit():
                break
            kw = SEARCH_KEYWORDS[ki]
            while sort_idx < len(SORT_MODES) and _count_cards() < target:
                if sess.risk or _budget_hit():
                    break
                sort_by = SORT_MODES[sort_idx]
                empty = 0
                while pg < 20 and _count_cards() < target:
                    if sess.risk or _budget_hit():
                        break
                    _log(
                        f"cards [{ki+1}/{len(SEARCH_KEYWORDS)}] sort={sort_by} "
                        f"page={pg} kw={kw!r} total={_count_cards():,}"
                    )
                    sess.search(kw, pg, sort_by)
                    n = _append_cards(sess.drain(), seen, url_keys)
                    shift_pg += 1
                    if n:
                        _log(f"  +{n} cards (total {_count_cards():,})")
                        empty = 0
                        shift_added += n
                    else:
                        empty += 1
                        if empty >= 2:
                            break
                    state.update({"keyword_idx": ki, "page": pg + 1, "sort_idx": sort_idx})
                    _save_state(state)
                    pg += 1
                    time.sleep(random.uniform(*gap))
                pg = 0
                sort_idx += 1
                state.update({"page": 0, "sort_idx": sort_idx})
                _save_state(state)
            sort_idx = 0
            pg = 0
            ki += 1
            state.update({"keyword_idx": ki, "page": 0, "sort_idx": 0})
            _save_state(state)

        if sess.risk:
            state["stopped_reason"] = sess.risk_reason
            _save_state(state)
            _log(f"STOP: {sess.risk_reason}")
            return 2

    if rotate["now"] and _count_cards() < target:
        state["stopped_reason"] = "shift_budget"
        _save_state(state)
        _log(f"SHIFT END: budget ({shift_added} cards this shift) — rotate account")
        return 3

    final = _count_cards()
    state["stopped_reason"] = "target_reached" if final >= target else "exhausted"
    _save_state(state)
    _log(f"Done: {final:,} listing cards → {OUT_JSONL}")
    return 0 if final >= target else 1


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="BR listing card harvest (no iOS)")
    ap.add_argument("--target", type=int, default=50_000)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--fast", action="store_true", default=True)
    ap.add_argument("--no-fast", action="store_true")
    args = ap.parse_args()
    if args.status:
        _log(f"listing cards: {_count_cards():,}")
        _log(f"  file: {OUT_JSONL}")
        st = _load_state()
        _log(f"  state: kw={st.get('keyword_idx')} page={st.get('page')} sort={st.get('sort_idx')}")
        return 0
    return harvest_cards(args.target, fast=not args.no_fast)


if __name__ == "__main__":
    raise SystemExit(main())
