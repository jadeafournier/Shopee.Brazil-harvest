# -*- coding: utf-8 -*-
"""Proxy pool optimization utilities for BR subscriber lines.

Each line in proxy(BR shopee).txt is a subscriber slot (account + geo + sid).
This tool probes slots, prints ranked stats, and learns from batch_summary.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))

import proxy_pool

CANARY = "https://shopee.com.br/"


def cmd_stats(region: str) -> int:
    proxy_pool.set_region(region)
    proxy_pool.load()
    print(json.dumps(proxy_pool.stats_summary(top=20), ensure_ascii=False, indent=2))
    return 0


def cmd_probe(region: str, limit: int) -> int:
    from curl_cffi import requests as cr

    proxy_pool.set_region(region)
    pool = proxy_pool.load()[:limit] if limit else proxy_pool.load()
    print(f"Probing {len(pool)} subscriber slot(s) region={region}")
    for i, tmpl in enumerate(pool, 1):
        key = proxy_pool.subscriber_key(tmpl)
        url = proxy_pool.randomize_session(tmpl, region)
        t0 = time.time()
        outcome = "ok"
        try:
            r = cr.get(CANARY, impersonate="chrome131", proxies={"http": url, "https": url}, timeout=25)
            if r.status_code >= 400:
                outcome = "http_err"
        except Exception as exc:
            outcome = "proxy_err"
            print(f"  [{i}] FAIL {key[-50:]} {str(exc)[:60]}")
        else:
            ms = int((time.time() - t0) * 1000)
            print(f"  [{i}] {outcome.upper()} {ms}ms ...{key[-50:]}")
        proxy_pool.record(key, "ok" if outcome == "ok" else outcome.replace("_err", ""))
    print(json.dumps(proxy_pool.stats_summary(top=10), ensure_ascii=False, indent=2))
    return 0


def cmd_learn(summary_path: Path) -> int:
    if not summary_path.is_file():
        print(f"Missing {summary_path}")
        return 1
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    proxy_pool.set_region("BR")
    proxy_pool.load()
    n = 0
    for _key, meta in (data.get("items") or {}).items():
        sub = meta.get("subscriber")
        if not sub:
            continue
        # batch meta stores truncated subscriber suffix — match by suffix
        full_key = None
        for tmpl in proxy_pool.load():
            k = proxy_pool.subscriber_key(tmpl)
            if k.endswith(sub) or sub.endswith(k[-48:]):
                full_key = k
                break
        if not full_key:
            continue
        outcome = "ok" if meta.get("has_data") else (
            "traffic" if meta.get("status") == "traffic_error" else
            "captcha" if meta.get("status") == "captcha" else
            "blocked" if any(a.get("err") == 90309999 for a in meta.get("getpc_attempts", [])) else
            "no_data"
        )
        proxy_pool.record(full_key, outcome)
        n += 1
    print(f"Learned {n} outcomes from {summary_path}")
    print(json.dumps(proxy_pool.stats_summary(top=15), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="BR")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats", help="Show subscriber success rankings")
    p_probe = sub.add_parser("probe", help="HTTP probe each subscriber slot")
    p_probe.add_argument("--limit", type=int, default=0)
    p_learn = sub.add_parser("learn", help="Import outcomes from batch_summary.json")
    p_learn.add_argument("--summary", type=Path,
                         default=Path(__file__).parent / "br_batch_output" / "batch_summary.json")
    args = ap.parse_args()
    if args.cmd == "stats":
        return cmd_stats(args.region)
    if args.cmd == "probe":
        return cmd_probe(args.region, args.limit)
    if args.cmd == "learn":
        return cmd_learn(args.summary)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
