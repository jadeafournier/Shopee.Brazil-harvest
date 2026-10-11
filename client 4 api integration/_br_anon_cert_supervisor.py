# -*- coding: utf-8 -*-
"""Keep anon-cert crawler alive until 1000."""
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
SCRIPT = str(HERE / "_br_anon_cert_1k.py")
LOG = HERE / "br_anon_cert_supervisor.log"
LOCK = HERE / "br_anon_cert.lock"
TARGET = 1000


def n() -> int:
    return len(list(DELIV.glob("output_*.json")))


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    if LOCK.is_file():
        age = time.time() - LOCK.stat().st_mtime
        if age < 3 * 3600:
            log("lock held — exit")
            return 3
    LOCK.write_text(str(int(time.time())), encoding="utf-8")
    try:
        while n() < TARGET:
            log(f"supervisor n={n()} spawn anon_cert")
            p = subprocess.Popen([PY, "-u", SCRIPT], cwd=str(HERE))
            while p.poll() is None and n() < TARGET:
                time.sleep(60)
                if int(time.time()) % 300 < 70:
                    log(f"n={n()}")
            rc = p.wait()
            log(f"exit {rc} n={n()}")
            if n() >= TARGET:
                break
            time.sleep(120)
        log(f"DONE n={n()}")
        print(f"FINAL_COUNT={n()}")
        return 0 if n() >= TARGET else 1
    finally:
        try:
            LOCK.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
