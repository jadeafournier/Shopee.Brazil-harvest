# -*- coding: utf-8 -*-
"""Keep continuous long-cool unlocker alive until 1000."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DELIV = HERE / "br_deliverable_getpc"
PY = str((HERE.parent / ".venv" / "Scripts" / "python.exe").resolve())
if not Path(PY).is_file():
    PY = sys.executable
SCRIPT = str(HERE / "_br_long_cool_unlock.py")
LOG = HERE / "br_cont_supervisor.log"
LOCK = HERE / "br_cont_supervisor.lock"
TARGET = 1000


def n() -> int:
    return len(list(DELIV.glob("output_*.json")))


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    if LOCK.is_file() and time.time() - LOCK.stat().st_mtime < 4 * 3600:
        log("supervisor lock — exit")
        return 3
    LOCK.write_text(str(int(time.time())), encoding="utf-8")
    try:
        while n() < TARGET:
            # child handles its own br_long_cool.lock; pass 0 boot cool on respawn
            log(f"supervisor n={n()} spawn unlocker")
            p = subprocess.Popen([PY, "-u", SCRIPT, "0"], cwd=str(HERE))
            while p.poll() is None and n() < TARGET:
                time.sleep(60)
                if int(time.time()) % 300 < 70:
                    log(f"n={n()}")
            rc = p.wait()
            log(f"unlocker exit {rc} n={n()}")
            if n() >= TARGET:
                break
            time.sleep(90)
        log(f"SUPERVISOR DONE n={n()}")
        print(f"FINAL_COUNT={n()}")
        return 0 if n() >= TARGET else 1
    finally:
        try:
            LOCK.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
