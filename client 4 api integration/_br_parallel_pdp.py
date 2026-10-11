# -*- coding: utf-8 -*-
"""Parallel browser PDP workers (no iOS) — shard URLs across N headed Chromes.

WARNING: Same cookie on many browsers heats the account faster.
Only raise --workers above 1 if you have multiple healthy BR accounts
or accept shorter shifts / more cooldown.

Each worker runs _br_batch_crawl on a URL shard with primary cookies.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
ROOT = DIR.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
URL_DEFAULT = DIR / "br_active_product_urls_100k.txt"
OUT_DEFAULT = DIR / "br_batch_output_100k"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _load_urls(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _missing(urls: list[str], out_dir: Path) -> list[str]:
    missing = []
    for u in urls:
        # output_SHOP_ITEM.json
        try:
            from _br_probe import parse_url_ids
            sid, iid = parse_url_ids(u)
        except Exception:
            continue
        if not (out_dir / f"output_{sid}_{iid}.json").is_file():
            missing.append(u)
    return missing


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", type=Path, default=URL_DEFAULT)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--workers", type=int, default=1, help="1 recommended for single cookie")
    ap.add_argument("--per-worker", type=int, default=40, help="URLs per worker this round")
    ap.add_argument("--shift-quota", type=int, default=40)
    args = ap.parse_args()

    workers = max(1, min(4, int(args.workers)))
    if workers > 1:
        _log("WARNING: multi-browser same-cookie increases traffic_error risk.")

    args.out.mkdir(parents=True, exist_ok=True)
    missing = _missing(_load_urls(args.urls), args.out)
    _log(f"Parallel PDP: missing={len(missing):,} workers={workers} per_worker={args.per_worker}")
    if not missing:
        _log("Nothing missing.")
        return 0

    shard_dir = DIR / "br_parallel_shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    py = str(PY if PY.is_file() else sys.executable)
    procs = []
    for wid in range(workers):
        shard = missing[wid::workers][: args.per_worker]
        if not shard:
            continue
        shard_path = shard_dir / f"shard_w{wid}.txt"
        shard_path.write_text("\n".join(shard) + "\n", encoding="utf-8")
        cmd = [
            py, str(DIR / "_br_batch_crawl.py"),
            "--urls", str(shard_path),
            "--out", str(args.out),
            "--shift-quota", str(args.shift_quota),
        ]
        log_path = shard_dir / f"worker_{wid}.log"
        lf = open(log_path, "a", encoding="utf-8", errors="replace")
        _log(f"  spawn worker {wid+1}/{workers} urls={len(shard)} → {log_path.name}")
        procs.append((
            subprocess.Popen(cmd, cwd=str(ROOT), stdout=lf, stderr=subprocess.STDOUT),
            lf,
        ))

    codes = []
    for proc, lf in procs:
        codes.append(proc.wait())
        try:
            lf.close()
        except Exception:
            pass
    _log(f"Done exit_codes={codes}")
    return 0 if all(c == 0 for c in codes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
