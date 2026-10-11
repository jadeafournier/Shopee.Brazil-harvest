# -*- coding: utf-8 -*-
"""BR get_pc — scan clean proxies + single-session batch (captcha bypass path)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_probe import BR_TEST, parse_br_test, parse_url_ids, summarize_data
from br_session import scan_and_extract
import proxy_pool

OUT = Path(__file__).resolve().parent / "br_probe_output"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _save(url: str, data: dict) -> None:
    sid, iid = parse_url_ids(url)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"output_{sid}_{iid}.json").write_text(json.dumps({
        "task_link": url, "token": "br_client4_test", "api_key": "br_client4_test",
        "bff_meta": None, "error": None, "error_msg": None, "data": data,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BR warm extract with proxy scan")
    ap.add_argument("--scan", type=int, default=30, help="max proxy attempts")
    ap.add_argument("--only-missing", action="store_true")
    args = ap.parse_args()

    cookies, _, urls = parse_br_test(BR_TEST)
    proxy_pool.set_region("BR")
    proxy_pool.load()

    targets = list(urls)
    if args.only_missing:
        targets = [
            u for u in urls
            if not (OUT / f"output_{parse_url_ids(u)[0]}_{parse_url_ids(u)[1]}.json").is_file()
        ]
    if not targets:
        _log("All outputs exist.")
        return 0

    _log("BR extraction strategy:")
    _log("  • curl get_pc / get_rw / SSR → 90309999 (blocked, no bypass exists)")
    _log("  • Captcha bypass = find IP that never sees /verify/captcha")
    _log("  • One browser session, AC_CERT_D reused across URLs")
    _log(f"  • Scanning up to {args.scan} proxies for {len(targets)} URL(s)\n")

    results, meta = scan_and_extract(targets, cookies, max_proxy_tries=args.scan, log=_log)
    for url in targets:
        sid, iid = parse_url_ids(url)
        key = f"{sid}_{iid}"
        if key in results:
            _save(url, results[key])
            _log(f"SAVED i.{sid}.{iid} {summarize_data(results[key])}")

    saved = len(results)
    OUT.joinpath("probe_summary.json").write_text(json.dumps({
        "saved": saved, "total_urls": len(targets),
        "method": "scan_and_extract_single_session",
        "meta": meta,
        "non_browser_viable": False,
        "captcha_bypass": "clean_proxy_only",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    _log(f"\nDone: {saved}/{len(targets)} | proxies tried: {meta.get('proxies_tried')}")
    if saved < len(targets):
        _log("If all proxies hit captcha: refresh proxy pool or solve once when 90s window opens.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
