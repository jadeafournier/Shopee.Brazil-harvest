# -*- coding: utf-8 -*-
"""Supervisor: keep a single BR get_pc worker alive until 1000 or budget exhausted."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"
WORKER = HERE / "_br_crawl_to_1k.py"
LOG = HERE / "br_crawl_to_1k.log"
DELIV = HERE / "br_deliverable_getpc"
TARGET = 1000
MAX_RESTARTS = 40
JARS = [
    "br_1706801030_ydvUxsIK_live.json",
    "br_1706785017_CoOHjjSQ_live.json",
    "br_1706784820_OUk7hm0A_live.json",
    "br_1705283243_F3zrbhjv_live.json",
]


def count() -> int:
    import json, re
    n = 0
    for p in DELIV.glob("output_*.json"):
        if not re.match(r"output_\d+_\d+\.json", p.name):
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            payload = doc.get("data") if isinstance(doc.get("data"), dict) else doc
            item = payload.get("item") if isinstance(payload, dict) else None
            if isinstance(item, dict) and len(item) >= 40:
                n += 1
        except Exception:
            pass
    return n


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] SUPER {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    restarts = 0
    jar_i = 0
    stagnant = 0
    last = count()
    log(f"supervisor start n={last}/{TARGET}")
    while count() < TARGET and restarts < MAX_RESTARTS and stagnant < 15:
        jar = JARS[jar_i % len(JARS)]
        jar_i += 1
        restarts += 1
        before = count()
        log(f"spawn #{restarts} jar={jar} n={before}")
        cmd = [
            str(PY), "-u", str(WORKER),
            "--worker=0", f"--jar={jar}", "--shards=1", "--no-rotate",
        ]
        try:
            # Each spawn runs until worker exits (shift loop ends or crash)
            # Cap one spawn runtime so we can rotate jars
            p = subprocess.Popen(cmd, cwd=str(HERE))
            deadline = time.time() + 12 * 60  # 12 min per spawn
            while time.time() < deadline:
                if p.poll() is not None:
                    break
                time.sleep(10)
                n = count()
                if n != before:
                    log(f"progress n={n}/{TARGET} (+{n-before})")
                    before = n
                if n >= TARGET:
                    break
            if p.poll() is None:
                log("spawn timebox — terminating worker")
                p.terminate()
                try:
                    p.wait(timeout=15)
                except Exception:
                    p.kill()
            else:
                log(f"spawn exited rc={p.returncode} n={count()}")
        except Exception as e:
            log(f"spawn exc {e}")
        after = count()
        if after <= last:
            stagnant += 1
        else:
            stagnant = 0
        last = after
        # rotate SIDs between spawns via worker's rotate by briefly calling helper
        try:
            subprocess.run(
                [str(PY), "-c",
                 "import sys; sys.path.insert(0, r'%s'); import _br_crawl_to_1k as m; m.rotate_proxies(50)" % str(HERE).replace('\\', '\\\\')],
                cwd=str(HERE), timeout=30,
            )
        except Exception:
            pass
        time.sleep(8)
    final = count()
    log(f"FINAL n={final}/{TARGET} reached={final>=TARGET} restarts={restarts} stagnant={stagnant}")
    print(f"FINAL_COUNT={final} REACHED_1000={str(final>=TARGET).lower()}")
    return 0 if final >= TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
