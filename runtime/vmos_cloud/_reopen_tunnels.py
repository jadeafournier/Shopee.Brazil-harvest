# -*- coding: utf-8 -*-
"""끊긴 SSH 터널을 폰마다 다시 열고, 그 폰의 ADB 서버에만 연결한다."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CLIENT4 = Path(r"D:\Luxury\Great Scraper2\Great Scraper\client 4 api integration")
sys.path.insert(0, str(CLIENT4))
from br_emulator import vmos_cloud  # noqa: E402

ROOT = CLIENT4.parent
ADB = str(ROOT / "emu_setup" / "platform-tools" / "adb.exe")
PHONES_PATH = ROOT / "runtime" / "vmos_cloud" / "phones.json"


def load_phones() -> list[dict]:
    phones = json.loads(PHONES_PATH.read_text(encoding="utf-8"))
    phones.sort(key=lambda p: int(p["shard"]))
    return phones


def main() -> int:
    for phone in load_phones():
        pad = phone["pad"]
        adb_port = str(phone["adb_port"])
        mitm = str(phone["port"])
        info = vmos_cloud.enable_adb(pad)
        serial = vmos_cloud._parse_adb_serial(info.get("adb") or "")
        print(f"== {phone.get('name')} {pad} {serial} adb {adb_port}", flush=True)
        vmos_cloud.start_ssh_tunnel(info.get("command") or "", info.get("key") or "")
        subprocess.run([ADB, "-P", adb_port, "start-server"], capture_output=True, text=True, timeout=20)
        conn = subprocess.run(
            [ADB, "-P", adb_port, "connect", serial],
            capture_output=True, text=True, timeout=20,
        )
        print(((conn.stdout or "") + (conn.stderr or "")).strip(), flush=True)
        rev = subprocess.run(
            [ADB, "-P", adb_port, "-s", serial, "reverse", f"tcp:{mitm}", f"tcp:{mitm}"],
            capture_output=True, text=True, timeout=20,
        )
        proxy = subprocess.run(
            [ADB, "-P", adb_port, "-s", serial, "shell", "settings", "put", "global", "http_proxy", f"127.0.0.1:{mitm}"],
            capture_output=True, text=True, timeout=20,
        )
        ping = subprocess.run(
            [ADB, "-P", adb_port, "-s", serial, "shell", "echo", "ok"],
            capture_output=True, text=True, timeout=15,
        )
        print(
            f"reverse={((rev.stdout or '') + (rev.stderr or '')).strip()} "
            f"proxy_rc={proxy.returncode} ping={((ping.stdout or '') + (ping.stderr or '')).strip()}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
