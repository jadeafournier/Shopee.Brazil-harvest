# -*- coding: utf-8 -*-
"""세션 삭제 후 Google 없이 이메일 로그인 화면까지."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
sys.path.insert(0, str(ROOT / "client 4 api integration"))
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"

from br_emulator import vmos_cloud
from br_emulator.vmos_app_capture import (
    DEFAULT_PORT,
    _account_userid_on_device,
    _log,
    _sh,
    ensure_device,
    reset_shopee_login,
    setup_reverse_proxy,
)

ADB = vmos_cloud.adb_bin()
DUMP = Path(r"D:\Luxury\Great Scraper2\Great Scraper\runtime\vmos_cloud\uidump.xml")


def texts(serial: str) -> list[tuple[str, int, int]]:
    subprocess.run([ADB, "-s", serial, "shell", "rm", "-f", "/sdcard/uidump.xml"], capture_output=True)
    subprocess.run([ADB, "-s", serial, "shell", "uiautomator", "dump", "/sdcard/uidump.xml"], capture_output=True)
    subprocess.run([ADB, "-s", serial, "pull", "/sdcard/uidump.xml", str(DUMP)], capture_output=True)
    xml = DUMP.read_text(encoding="utf-8", errors="replace") if DUMP.exists() else ""
    out = []
    for m in re.finditer(r'text="([^"]+)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
        t, x1, y1, x2, y2 = m.groups()
        out.append((t, (int(x1) + int(x2)) // 2, (int(y1) + int(y2)) // 2))
    return out


def tap_label(serial: str, keys: list[str]) -> bool:
    rows = texts(serial)
    _log("  UI " + " | ".join(t for t, _, _ in rows if t.strip())[:240])
    for t, x, y in rows:
        if any(k.lower() in t.lower() for k in keys):
            _log(f"  tap {t!r} {x},{y}")
            _sh(serial, f"input tap {x} {y}")
            return True
    return False


def main() -> int:
    serial = ensure_device()["serial"]
    try:
        setup_reverse_proxy(serial, DEFAULT_PORT)
    except Exception as exc:
        _log(str(exc))
    _log(f"  삭제 전 userid={_account_userid_on_device(serial) or '-'}")
    reset_shopee_login(serial)
    time.sleep(4.0)
    if not tap_label(serial, ["COMEÇAR", "Comecar", "Começar"]):
        _sh(serial, "input tap 360 1505")
    time.sleep(3.0)
    # Google 말고 Entrar / e-mail
    if not tap_label(serial, ["Entrar"]):
        tap_label(serial, ["E-mail", "Telefone", "Senha"])
    time.sleep(2.0)
    rows = texts(serial)
    if any("Google" in t for t, _, _ in rows) and not any("Senha" in t for t, _, _ in rows):
        _sh(serial, "input keyevent KEYCODE_BACK")
        time.sleep(1.5)
        tap_label(serial, ["Entrar"])
        time.sleep(1.5)
        tap_label(serial, ["Senha", "E-mail"])
    uid = _account_userid_on_device(serial)
    _log(f"  준비 userid={uid or '-'} — 이메일/비밀번호로 다른 계정 로그인")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
