# -*- coding: utf-8 -*-
"""Redis URL frontier for 1M/day workers (optional — falls back to file queue).

Pattern from scrapy-redis / distributed Scrapy blogs:
  seed → RPUSH br:urls → workers LPOP → SADD br:done
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
URLS = HERE / "br_active_product_urls_100k.txt"
DELIV = HERE / "br_deliverable_getpc"
KEY_Q = "br:urls"
KEY_DONE = "br:done"


def parse_ids(u: str):
    m = re.search(r"i\.(\d+)\.(\d+)", u) or re.search(r"/product/(\d+)/(\d+)", u)
    return f"{m.group(1)}:{m.group(2)}" if m else None


def connect(url: str = "redis://127.0.0.1:6379/0"):
    try:
        import redis
    except ImportError:
        print("pip install redis")
        raise SystemExit(2)
    return redis.from_url(url, decode_responses=True)


def seed(r, limit: int = 0) -> int:
    have = set()
    for f in DELIV.glob("output_*.json"):
        m = re.match(r"output_(\d+)_(\d+)\.json", f.name)
        if m:
            have.add(f"{m.group(1)}:{m.group(2)}")
    n = 0
    pipe = r.pipeline()
    for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
        key = parse_ids(line.strip())
        if not key or key in have:
            continue
        if r.sismember(KEY_DONE, key):
            continue
        pipe.rpush(KEY_Q, key)
        n += 1
        if n % 5000 == 0:
            pipe.execute()
            pipe = r.pipeline()
        if limit and n >= limit:
            break
    pipe.execute()
    print(f"seeded {n} into {KEY_Q}")
    return n


def stats(r) -> None:
    print(f"queue={r.llen(KEY_Q)} done={r.scard(KEY_DONE)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("seed", "stats", "clear"))
    ap.add_argument("--redis", default="redis://127.0.0.1:6379/0")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    r = connect(args.redis)
    if args.cmd == "seed":
        seed(r, args.limit)
    elif args.cmd == "stats":
        stats(r)
    else:
        r.delete(KEY_Q)
        print("cleared queue (done set kept)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
