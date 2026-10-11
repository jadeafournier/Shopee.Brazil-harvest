# -*- coding: utf-8 -*-
"""BR human-like raw get_pc harvest — ≥100 Client-4 JSON envelopes.

Uses the login cookie in BR TEST.txt + São Paulo residential sticky proxies.
Never curl/fetch get_pc (always 90309999). Only headed Chrome + passive SPA
intercept of page-native signed get_pc — same path a real shopper triggers.

Anti-bot posture:
  * Real Chrome channel, headed, webdriver flag stripped
  * locale=pt-BR, timezone=America/Sao_Paulo (force-fix export mismatches)
  * Home warmup (scroll/mouse) before first PDP
  * PDP read simulation while get_pc fires
  * Homepage revisits every ~8 products
  * Slow jittered gaps; soft shift quota; stop on captcha/traffic_error
  * Sticky BR residential subscriber IPs; rotate gently, never burst

Usage:
  python _br_human_raw_100.py
  python _br_human_raw_100.py --target 120 --shift-quota 35
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(HERE))

import _br_batch_crawl as batch  # noqa: E402
from _br_long_crawl import run_one_shift, _sync_proxies  # noqa: E402
from _br_probe import parse_url_ids  # noqa: E402
from br_account_pool import AccountPool, discover_accounts  # noqa: E402
import proxy_pool  # noqa: E402

OUT = HERE / "br_human_raw_getpc"
URL_POOL = HERE / "br_active_product_urls_100k.txt"
URL_WORK = HERE / "br_human_raw_urls.txt"
STATUS = OUT / "status.json"
LOG = HERE / "br_human_raw_100.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def n_ok() -> int:
    return sum(
        1
        for p in OUT.glob("output_*.json")
        if re.match(r"output_\d+_\d+\.json$", p.name)
    )


def build_work_urls(target: int, *, oversample: float = 2.2) -> list[str]:
    have: set[tuple[str, str]] = set()
    for p in OUT.glob("output_*.json"):
        m = re.match(r"output_(\d+)_(\d+)\.json$", p.name)
        if m:
            have.add((m.group(1), m.group(2)))
    # Also skip items already proven in older BR deliverable dirs (optional dedupe)
    for folder in (HERE / "br_deliverable_getpc", HERE / "br_batch_output"):
        if not folder.is_dir():
            continue
        for p in folder.glob("output_*.json"):
            m = re.match(r"output_(\d+)_(\d+)\.json$", p.name)
            if m:
                have.add((m.group(1), m.group(2)))

    need = max(target - n_ok(), 0)
    want = int(max(need, 1) * oversample) + 20
    pending: list[str] = []
    for line in URL_POOL.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or "shopee.com.br" not in line:
            continue
        m = re.search(r"/product/(\d+)/(\d+)", line) or re.search(r"i\.(\d+)\.(\d+)", line)
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


def apply_human_pacing() -> None:
    """Slower than default batch — looks like a careful shopper, not a harvester."""
    batch.SESSION_CHUNK = 3
    batch.BROWSER_REFRESH_URLS = 28
    batch.HOME_EVERY_N = 8
    batch.CHUNK_COOLDOWN_S = (18.0, 36.0)
    batch.URL_GAP_S = (9.0, 22.0)
    batch.PRE_URL_JITTER_S = (0.8, 2.8)
    batch.GETPC_WAIT_S = 36.0
    batch.VERIFY_POLL_S = 24.0


def write_status(**extra) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    doc = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(OUT),
        "full_raw_files": n_ok(),
        "method": "browser_spa_get_pc_human",
        "login_cookies": True,
        "live_get_pc_api": True,
        "bot_mitigations": [
            "headed_chrome",
            "pt-BR_locale",
            "America/Sao_Paulo_tz",
            "home_warmup",
            "pdp_read_simulation",
            "homepage_revisits",
            "sticky_BR_residential",
            "soft_shift_quota",
            "stop_on_captcha_or_traffic",
            "no_curl_get_pc",
        ],
        **extra,
    }
    STATUS.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BR human raw get_pc ≥100")
    ap.add_argument("--target", type=int, default=110)
    ap.add_argument("--shift-quota", type=int, default=35)
    ap.add_argument("--max-shifts", type=int, default=6)
    ap.add_argument("--rest-between-shifts", type=float, default=90.0)
    args = ap.parse_args()

    apply_human_pacing()
    OUT.mkdir(parents=True, exist_ok=True)
    batch.OUT = OUT

    pool = AccountPool(shift_quota=args.shift_quota, cooldown_s=3 * 3600)
    discover_accounts(pool, primary_only=True)
    aid = pool.register_br_test()
    if not aid:
        log("FATAL: BR TEST.txt has no SPC_U — paste cookie under Cookie:")
        return 2
    log(f"registered account={aid} AC_CERT={'Y' if (pool.load_cookies(aid) or {}).get('AC_CERT_D') else 'N'}")

    _sync_proxies()
    proxy_pool.set_region("BR")
    proxy_pool.load()
    log(f"proxies loaded={len(getattr(proxy_pool, '_POOL', []) or [])}")

    urls = build_work_urls(args.target)
    if not urls:
        log("No pending URLs")
        write_status(note="no_pending")
        return 0 if n_ok() >= args.target else 3
    URL_WORK.write_text("\n".join(urls) + "\n", encoding="utf-8")
    log(f"work_urls={len(urls)} already_ok={n_ok()} target={args.target}")
    write_status(phase="start", work_urls=len(urls), target=args.target)

    shifts = 0
    total_ok = 0
    while n_ok() < args.target and shifts < args.max_shifts:
        missing = []
        for u in urls:
            sid, iid = parse_url_ids(u)
            if not (OUT / f"output_{sid}_{iid}.json").is_file():
                missing.append(u)
        if not missing:
            # rebuild from pool if work list exhausted
            urls = build_work_urls(args.target)
            URL_WORK.write_text("\n".join(urls) + "\n", encoding="utf-8")
            missing = urls
        if not missing:
            break

        # Soft reset shift counter for this dedicated run (same fresh cookie)
        pool.reset_shift()
        ok, why = pool.available(aid)
        if not ok:
            log(f"account not available: {why} — sleeping 20m")
            time.sleep(20 * 60)
            continue

        log(f"=== SHIFT {shifts + 1} missing={len(missing)} have={n_ok()} ===")
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
        log(
            f"shift done ok={got} fail={stats.get('fail')} "
            f"stop={stats.get('stopped_reason')} total_files={n_ok()}"
        )
        write_status(
            phase="shift",
            shifts=shifts,
            last_shift=stats,
            total_ok_this_run=total_ok,
            target=args.target,
        )

        if stats.get("stopped_reason") in ("captcha", "traffic_error"):
            log("ACCOUNT RISK — stopping to protect cookie. Cool ≥3h then resume.")
            break
        if n_ok() >= args.target:
            break
        # Healthy soft-quota stop: clear the 60m pool cooldown so we can continue
        # after a short human rest (risk cooldowns from captcha/traffic stay).
        if stats.get("stopped_reason") == "shift_quota_protect_cookies":
            rec = pool._acc(aid)
            rec["cooldown_until"] = 0
            rec["last_status"] = "ok"
            pool.save()
        rest = max(45.0, args.rest_between_shifts + random.uniform(-10, 50))
        log(f"human rest between shifts: {rest:.0f}s")
        time.sleep(rest)

    final = n_ok()
    write_status(phase="done", shifts=shifts, total_ok_this_run=total_ok, target=args.target)
    log(f"DONE files={final} target={args.target} shifts={shifts} ok_this_run={total_ok}")
    return 0 if final >= args.target else 1


if __name__ == "__main__":
    raise SystemExit(main())
