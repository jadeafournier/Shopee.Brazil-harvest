# -*- coding: utf-8 -*-
"""Supervisor: keep hot-burst crawler alive until 1000."""
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
SCRIPT = str(HERE / "_br_hot_burst_1k.py")
LOG = HERE / "br_hot_supervisor.log"
TARGET = 1000


def n() -> int:
    return len(list(DELIV.glob("output_*.json")))


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    # Brief settle after cool so proxy SIDs / account heat drop further
    log(f"supervisor boot n={n()} settle 180s")
    time.sleep(180)
    while n() < TARGET:
        log(f"supervisor n={n()} spawn hot_burst")
        p = subprocess.Popen([PY, "-u", SCRIPT], cwd=str(HERE))
        last = n()
        stagnant = 0
        while p.poll() is None and n() < TARGET:
            time.sleep(45)
            cur = n()
            if cur == last:
                stagnant += 1
            else:
                stagnant = 0
                last = cur
            if int(time.time()) % 300 < 50:
                log(f"n={cur} stagnant={stagnant}")
            # If unlocked then stuck, do not kill — let burst finish naturally
        rc = p.wait()
        log(f"hot_burst exit {rc} n={n()}")
        if n() >= TARGET:
            break
        # After a burn exit, long rest before respawn (avoid thrash)
        rest = 900 if rc != 0 else 60
        log(f"rest {rest}s before respawn")
        time.sleep(rest)
    log(f"SUPERVISOR DONE n={n()}")
    print(f"FINAL_COUNT={n()}")
    return 0 if n() >= TARGET else 1


if __name__ == "__main__":
    raise SystemExit(main())
