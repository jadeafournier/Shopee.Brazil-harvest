# -*- coding: utf-8 -*-
"""Cool accounts, then single hot-supervisor until 1000 (never dual-worker)."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = str((HERE.parent / ".venv" / "Scripts" / "python.exe").resolve())
if not Path(PY).is_file():
    PY = sys.executable
LOG = HERE / "br_cool_then_hot.log"
# Remaining cool after 14:15 burn; override via argv seconds
DEFAULT_COOL_S = 40 * 60
LOCK = HERE / "br_harvest.lock"


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    cool_s = DEFAULT_COOL_S
    if len(sys.argv) > 1:
        try:
            cool_s = max(0, int(sys.argv[1]))
        except ValueError:
            pass

    if LOCK.is_file():
        try:
            old = int(LOCK.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            old = 0
        # stale lock > 3h
        if old and time.time() - old < 3 * 3600:
            log(f"lock held by pid stamp {old} — exit")
            return 3
    LOCK.write_text(str(int(time.time())), encoding="utf-8")
    try:
        log(f"cooling {cool_s}s…")
        time.sleep(cool_s)
        log("cool done — launch single hot supervisor")
        return subprocess.call([PY, "-u", str(HERE / "_br_hot_supervisor.py")], cwd=str(HERE))
    finally:
        try:
            LOCK.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
