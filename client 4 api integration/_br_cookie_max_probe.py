# -*- coding: utf-8 -*-
"""Measure max full raw get_pc this BR TEST cookie can yield before hard stop.

Pushes past soft 35/shift: high shift-quota, many shifts, stop only on
captcha / traffic_error / cookie death. Dedicated output dir so counts are
clean for THIS cookie refresh.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "shopee_getpc_crawler"), str(HERE)]

import _br_batch_crawl as batch  # noqa: E402
from _br_long_crawl import run_one_shift, _sync_proxies  # noqa: E402
from _br_probe import parse_url_ids  # noqa: E402
from br_account_pool import AccountPool, discover_accounts  # noqa: E402
import proxy_pool  # noqa: E402

OUT = HERE / "br_cookie_max_probe"
URL_POOL = HERE / "br_active_product_urls_100k.txt"
URL_WORK = HERE / "br_cookie_max_urls.txt"
STATUS = OUT / "status.json"
LOG = HERE / "br_cookie_max_probe.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def n_ok() -> int:
    return sum(
        1
        for p in OUT.glob("output_*.json")
        if re.match(r"output_\d+_\d+\.json$", p.name)
    )


def build_urls(want: int) -> list[str]:
    have: set[tuple[str, str]] = set()
    for folder in (
        OUT,
        HERE / "br_human_raw_getpc",
        HERE / "br_deliverable_getpc",
        HERE / "br_batch_output",
        HERE / "br_batch_output_100k",
    ):
        if not folder.is_dir():
            continue
        for p in folder.glob("output_*.json"):
            m = re.match(r"output_(\d+)_(\d+)\.json$", p.name)
            if m:
                have.add((m.group(1), m.group(2)))
    pending: list[str] = []
    for line in URL_POOL.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or "shopee.com.br" not in line:
            continue
        m = re.search(r"/product/(\d+)/(\d+)", line) or re.search(
            r"i\.(\d+)\.(\d+)", line
        )
        if not m:
            continue
        key = (m.group(1), m.group(2))
        if key in have:
            continue
        pending.append(f"https://shopee.com.br/product/{key[0]}/{key[1]}")
        if len(pending) >= want:
            break
    random.shuffle(pending)
    return pending


def apply_pacing() -> None:
    batch.SESSION_CHUNK = 3
    batch.BROWSER_REFRESH_URLS = 28
    batch.HOME_EVERY_N = 8
    batch.CHUNK_COOLDOWN_S = (16.0, 32.0)
    batch.URL_GAP_S = (8.0, 20.0)
    batch.PRE_URL_JITTER_S = (0.6, 2.4)
    batch.GETPC_WAIT_S = 36.0
    batch.VERIFY_POLL_S = 24.0


def write_status(**extra) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    doc = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "max_yield_this_cookie",
        "out_dir": str(OUT),
        "full_raw_files": n_ok(),
        "live_get_pc_api": True,
        **extra,
    }
    STATUS.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def smoke(pool: AccountPool, aid: str) -> bool:
    """One PDP smoke — prove cookie unlocks full get_pc."""
    test_urls: list[str] = []
    br_test = (HERE / "BR TEST.txt").read_text(encoding="utf-8", errors="replace")
    for line in br_test.splitlines():
        if "shopee.com.br" in line and ("i." in line or "/product/" in line):
            test_urls.append(line.strip().split()[0])
            break
    if not test_urls:
        test_urls = build_urls(1)
    if not test_urls:
        log("SMOKE: no URL")
        return False
    cookies = pool.load_cookies(aid)
    log(f"SMOKE url={test_urls[0][:90]}")
    log(
        f"SMOKE SPC_U={cookies.get('SPC_U')} "
        f"SPC_EC={'set' if cookies.get('SPC_EC') not in (None, '', '-') else 'empty'} "
        f"AC_CERT={'Y' if cookies.get('AC_CERT_D') else 'N'} "
        f"SPC_ST={'Y' if cookies.get('SPC_ST') else 'N'}"
    )
    stats = run_one_shift(
        pool,
        urls=test_urls[:1],
        out_dir=OUT,
        human_mode=True,
        shift_quota=1,
        primary_only=True,
    )
    log(f"SMOKE stats={stats}")
    return int(stats.get("ok") or 0) >= 1 or n_ok() >= 1


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=400, help="ceiling to aim for")
    ap.add_argument(
        "--shift-quota",
        type=int,
        default=80,
        help="per-shift soft stop (raise to find hard limit)",
    )
    ap.add_argument("--max-shifts", type=int, default=10)
    ap.add_argument("--rest-between-shifts", type=float, default=75.0)
    ap.add_argument("--skip-smoke", action="store_true")
    ap.add_argument("--fresh-out", action="store_true", help="wipe OUT before run")
    args = ap.parse_args()

    apply_pacing()
    if args.fresh_out and OUT.is_dir():
        for p in OUT.glob("output_*.json"):
            p.unlink()
        log(f"wiped previous outputs in {OUT}")
    OUT.mkdir(parents=True, exist_ok=True)
    batch.OUT = OUT

    pool = AccountPool(shift_quota=args.shift_quota, cooldown_s=3 * 3600)
    discover_accounts(pool, primary_only=True)
    aid = pool.register_br_test()
    if not aid:
        log("FATAL: no SPC_U in BR TEST.txt")
        return 2
    ck = pool.load_cookies(aid)
    log(
        f"registered {aid} AC_CERT={'Y' if ck.get('AC_CERT_D') else 'N'} "
        f"SPC_EC={'empty' if ck.get('SPC_EC') in (None,'','-') else 'set'}"
    )

    _sync_proxies()
    log(f"proxies={len(getattr(proxy_pool, '_POOL', []) or [])}")

    if not args.skip_smoke:
        pool.reset_shift()
        if not smoke(pool, aid):
            log("SMOKE FAILED — cookie may be blocked; abort max probe")
            write_status(phase="smoke_fail", account=aid)
            return 4
        log(f"SMOKE OK files={n_ok()}")
        # Soft rest from smoke quota=1 must not block the max harvest.
        rec = pool._acc(aid)
        rec["cooldown_until"] = 0
        rec["last_status"] = "ok"
        rec["shift_ok"] = 0
        pool.save()
        pool.reset_shift()
        log("cleared soft cooldown after smoke")

    urls = build_urls(max(args.target * 2, 80))
    URL_WORK.write_text("\n".join(urls) + "\n", encoding="utf-8")
    log(f"work_urls={len(urls)} already={n_ok()} target={args.target}")
    write_status(
        phase="start",
        account=aid,
        target=args.target,
        shift_quota=args.shift_quota,
        work_urls=len(urls),
    )

    shifts = 0
    total_ok = 0
    stop_reason = None
    per_shift: list[dict] = []

    while n_ok() < args.target and shifts < args.max_shifts:
        missing = [
            u
            for u in urls
            if not (OUT / f"output_{parse_url_ids(u)[0]}_{parse_url_ids(u)[1]}.json").is_file()
        ]
        if len(missing) < 5:
            urls = build_urls(max(args.target * 2, 80))
            URL_WORK.write_text("\n".join(urls) + "\n", encoding="utf-8")
            missing = [
                u
                for u in urls
                if not (
                    OUT / f"output_{parse_url_ids(u)[0]}_{parse_url_ids(u)[1]}.json"
                ).is_file()
            ]
        if not missing:
            stop_reason = "url_pool_exhausted"
            break

        pool.reset_shift()
        ok, why = pool.available(aid)
        if not ok:
            log(f"account unavailable: {why}")
            stop_reason = why
            break

        log(f"=== SHIFT {shifts+1} missing={len(missing)} have={n_ok()} ===")
        stats = run_one_shift(
            pool,
            urls=missing,
            out_dir=OUT,
            human_mode=True,
            shift_quota=args.shift_quota,
            primary_only=True,
        )
        shifts += 1
        got = int(stats.get("ok") or 0)
        total_ok += got
        per_shift.append(
            {
                "shift": shifts,
                "ok": got,
                "fail": stats.get("fail"),
                "stop": stats.get("stopped_reason"),
                "files_after": n_ok(),
            }
        )
        log(
            f"shift done ok={got} fail={stats.get('fail')} "
            f"stop={stats.get('stopped_reason')} files={n_ok()}"
        )
        write_status(
            phase="shift",
            account=aid,
            shifts=shifts,
            per_shift=per_shift,
            total_ok_this_run=total_ok,
            target=args.target,
        )

        reason = stats.get("stopped_reason")
        if reason in ("captcha", "traffic_error"):
            stop_reason = reason
            log("HARD STOP — protect cookie")
            break
        if n_ok() >= args.target:
            stop_reason = "target_reached"
            break
        if reason == "shift_quota_protect_cookies":
            rec = pool._acc(aid)
            rec["cooldown_until"] = 0
            rec["last_status"] = "ok"
            pool.save()
        rest = max(40.0, args.rest_between_shifts + random.uniform(-8, 40))
        log(f"rest {rest:.0f}s")
        time.sleep(rest)

    final = n_ok()
    write_status(
        phase="done",
        account=aid,
        shifts=shifts,
        per_shift=per_shift,
        total_ok_this_run=total_ok,
        full_raw_files=final,
        stop_reason=stop_reason or "max_shifts",
        target=args.target,
        verdict={
            "max_full_raw_this_cookie_run": total_ok,
            "files_in_out_dir": final,
            "hard_stop": stop_reason,
            "note": "soft shift_quota is artificial; hard stop = captcha/traffic/cookie death",
        },
    )
    log(
        f"DONE max_ok_this_run={total_ok} files={final} "
        f"shifts={shifts} stop={stop_reason}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
