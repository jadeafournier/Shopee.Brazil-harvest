# -*- coding: utf-8 -*-
"""BR long-term crawl — protect account cookies across shifts.

Strategy (no credential forging):
  1. Browser-minted SAP / AC_CERT only (passive SPA get_pc intercept).
  2. Soft shift quota (~60 OK) — stop while session is still healthy.
  3. On traffic_error / captcha — cool account for hours; never retry hot.
  4. Rotate to next available account if pool has more than one.
  5. Optional wait loop: sleep until cooldown ends, then continue --only-missing.

Four-process workflow (optimized):
  1. Status     — python "_br_long_crawl.py" --status
  2. One shift    — python "_br_long_crawl.py" --urls ... --out ...   (default 1 shift)
  3. Auto resume  — python "_br_long_crawl.py" --wait-cooldown --max-shifts 999
  4. After rest   — python "_br_long_crawl.py" --reset-shift   (only if all accounts quota-exhausted)

Recommended single command after updating BR TEST.txt cookies:
  python "_br_long_crawl.py" --run-remaining
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_batch_crawl import (  # noqa: E402
    OUT as DEFAULT_OUT,
    URL_FILE,
    _load_urls,
    _out_path,
    _write_summary,
    run_batch,
)
from _br_probe import parse_br_test, parse_url_ids  # noqa: E402
from br_account_pool import (  # noqa: E402
    BR_TEST,
    DEFAULT_COOLDOWN_S,
    DEFAULT_SHIFT_QUOTA,
    AccountPool,
    discover_accounts,
)
from br_cookies import normalize_br_cookies  # noqa: E402
import proxy_pool  # noqa: E402


def _log(msg: str) -> None:
    print(msg, flush=True)


def _sync_proxies() -> None:
    _, proxies, _ = parse_br_test(BR_TEST)
    px = ROOT / "proxy(BR shopee).txt"
    if proxies:
        px.write_text("\n".join(proxies) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()


def _missing_urls(url_file: Path, out_dir: Path) -> list[str]:
    urls = _load_urls(url_file)
    # Patch OUT used by _out_path
    import _br_batch_crawl as batch

    batch.OUT = out_dir
    return [u for u in urls if not batch._out_path(*parse_url_ids(u)).is_file()]


def cmd_status(pool: AccountPool, url_file: Path, out_dir: Path, *, primary_only: bool = False) -> int:
    discover_accounts(pool, primary_only=primary_only)
    urls = _load_urls(url_file)
    missing = _missing_urls(url_file, out_dir)
    done = len(urls) - len(missing)
    primary = pool.state.get("primary_account")
    _log("BR account pool status")
    _log(f"  progress: {done}/{len(urls)} done | {len(missing)} remaining")
    if primary:
        _log(f"  primary (BR TEST): {primary}")
    rows = pool.summary()
    _log(json.dumps(rows, ensure_ascii=False, indent=2))
    wait = pool.next_available_wait_s()
    if wait > 0:
        _log(f"Next cooldown ends in ~{int(wait / 60) + 1} minutes")
    elif not pool.pick(primary_only=primary_only):
        n = pool.reset_quota_exhausted()
        if n:
            _log(f"Auto-reset shift counters for {n} account(s) (quota exhausted, no risk cooldown)")
            if pool.pick(primary_only=primary_only):
                _log("At least one account is now ready — run a shift or --run-remaining")
    return 0


def run_one_shift(
    pool: AccountPool,
    *,
    urls: list[str],
    out_dir: Path,
    human_mode: bool,
    shift_quota: int,
    primary_only: bool = False,
) -> dict:
    import _br_batch_crawl as batch

    batch.OUT = out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    aid = pool.pick(primary_only=primary_only)
    if not aid:
        return {"stopped_reason": "no_available_account", "ok": 0, "fail": 0}

    ok, why = pool.available(aid)
    if not ok:
        return {"stopped_reason": f"account_unavailable:{why}", "ok": 0, "fail": 0}

    cookies = pool.load_cookies(aid)
    if not cookies:
        return {"stopped_reason": "empty_cookies", "ok": 0, "fail": 0}

    cookies = normalize_br_cookies(cookies, fresh_cert=False)
    remaining_quota = max(0, shift_quota - int(pool._acc(aid).get("shift_ok") or 0))
    if remaining_quota <= 0:
        return {"stopped_reason": "shift_quota_exhausted", "ok": 0, "fail": 0}

    # Cap this shift's URL list to remaining soft quota (+ small buffer for skips).
    shift_urls = urls[: remaining_quota]

    _log("=" * 70)
    _log("BR LONG CRAWL SHIFT — cookie-preserving")
    _log(f"  account={aid} SPC_U={cookies.get('SPC_U')}")
    _log(f"  AC_CERT={'yes' if cookies.get('AC_CERT_D') else 'no'}")
    _log(f"  shift_quota_remaining={remaining_quota} | urls_this_shift={len(shift_urls)}")
    _log(f"  session={pool.session_path(aid)}")
    _log("=" * 70)

    def on_ok(ck: dict) -> None:
        pool.record_ok(aid, ck)

    def on_risk(reason: str, ck: dict) -> None:
        pool.mark_risk(aid, reason, ck)
        _log(f"  pool: {aid} cooled for risk={reason}")

    # batch.SESSION_LIVE → this account's live file
    batch.SESSION_LIVE = pool.session_path(aid)

    stats = run_batch(
        shift_urls,
        cookies,
        human_mode=human_mode,
        stop_on_traffic=True,
        shift_quota=remaining_quota,
        on_ok=on_ok,
        on_risk=on_risk,
    )
    stats["account"] = aid
    # Persist final cookies from batch live file if present
    final = pool.load_cookies(aid)
    if final:
        pool.persist_cookies(aid, final)

    if stats.get("stopped_reason") == "shift_quota_protect_cookies":
        # Soft rest: short cooldown so same account isn't immediately reused
        # unless user has multiple accounts.
        rec = pool._acc(aid)
        if float(rec.get("cooldown_until") or 0) < time.time():
            rec["cooldown_until"] = time.time() + min(3600, pool.cooldown_s // 2)
            pool.save()
            _log(f"  pool: soft rest 60m after healthy quota stop")

    return stats


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BR long-term crawl — protect cookies across shifts")
    ap.add_argument("--urls", type=Path, default=URL_FILE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--shift-quota", type=int, default=DEFAULT_SHIFT_QUOTA)
    ap.add_argument("--cooldown-hours", type=float, default=DEFAULT_COOLDOWN_S / 3600)
    ap.add_argument("--max-shifts", type=int, default=1, help="shifts to run now (default 1)")
    ap.add_argument("--wait-cooldown", action="store_true", help="wait for cooldown and keep shifting")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset-shift", action="store_true", help="clear shift_ok counters")
    ap.add_argument("--register-test", action="store_true", help="re-register BR TEST.txt cookies (fresh shift)")
    ap.add_argument(
        "--run-remaining",
        action="store_true",
        help="register BR TEST, sync proxies, crawl all missing URLs with auto wait/rotate",
    )
    ap.add_argument(
        "--primary-only",
        action="store_true",
        help="use only BR TEST.txt cookie (never rotate stale backup accounts)",
    )
    ap.add_argument(
        "--all-accounts",
        action="store_true",
        help="allow rotating backup session jars (not recommended)",
    )
    args = ap.parse_args()

    pool = AccountPool(
        shift_quota=args.shift_quota,
        cooldown_s=int(args.cooldown_hours * 3600),
    )

    if args.run_remaining:
        args.wait_cooldown = True
        args.primary_only = True
        if args.max_shifts == 1:
            args.max_shifts = 999

    if args.all_accounts:
        args.primary_only = False

    discover_accounts(pool, primary_only=args.primary_only)
    if args.register_test:
        aid = pool.register_br_test()
        if aid:
            _log(f"Registered BR TEST (fresh) → {aid}")
        else:
            _log("BR TEST.txt: no SPC_U cookie found")
            return 1
    elif args.run_remaining or args.wait_cooldown:
        aid = pool.sync_br_test()
        if aid:
            ok, why = pool.available(aid)
            _log(f"Synced BR TEST → {aid} ({why})")

    if args.reset_shift:
        pool.reset_shift()
        _log("Shift counters reset.")
        return 0

    if args.status:
        return cmd_status(pool, args.urls, args.out, primary_only=args.primary_only)

    if args.run_remaining:
        _log("=" * 70)
        mode = "single cookie (BR TEST)" if args.primary_only else "multi-account"
        _log(f"BR RUN-REMAINING — {mode}")
        cmd_status(pool, args.urls, args.out, primary_only=args.primary_only)
        _log("=" * 70)

    _sync_proxies()
    out_dir = args.out
    human_mode = not args.fast

    shifts_done = 0
    total_ok = 0
    while shifts_done < args.max_shifts or args.wait_cooldown:
        missing = _missing_urls(args.urls, out_dir)
        if not missing:
            _log("All URLs complete.")
            break

        aid = pool.pick(primary_only=args.primary_only)
        if not aid:
            wait = pool.next_available_wait_s(primary_only=args.primary_only)
            if wait <= 0:
                n = pool.reset_quota_exhausted()
                if n:
                    _log(f"Auto-reset shift for {n} quota-exhausted account(s)")
                    aid = pool.pick(primary_only=args.primary_only)
            if not aid and args.wait_cooldown and wait > 0:
                label = "primary cookie" if args.primary_only else "accounts"
                sleep_s = min(wait + 5, 1800)
                _log(
                    f"No {label} ready. Cooling {int(wait / 60) + 1}m total — "
                    f"sleeping {int(sleep_s / 60)}m then rechecking..."
                )
                time.sleep(sleep_s)
                continue
            if not aid:
                _log("No available account. Use --status, wait for cooldown, or --reset-shift after rest.")
                _log(json.dumps(pool.summary(), ensure_ascii=False, indent=2))
                return 2

        stats = run_one_shift(
            pool,
            urls=missing,
            out_dir=out_dir,
            human_mode=human_mode,
            shift_quota=args.shift_quota,
            primary_only=args.primary_only,
        )
        shifts_done += 1
        total_ok += int(stats.get("ok") or 0)
        _log(
            f"Shift done: ok={stats.get('ok')} fail={stats.get('fail')} "
            f"stop={stats.get('stopped_reason')} account={stats.get('account')}"
        )
        summary = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "shifts_done": shifts_done,
            "total_ok_this_run": total_ok,
            "last_shift": {
                k: stats.get(k)
                for k in ("ok", "fail", "stopped_reason", "account", "success_rate")
            },
            "pool": pool.summary(),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "long_crawl_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        if not args.wait_cooldown:
            break
        # Between shifts: brief pause
        time.sleep(30)

    _log(f"\nLong crawl finished: shifts={shifts_done} ok={total_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
