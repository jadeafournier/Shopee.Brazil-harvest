# -*- coding: utf-8 -*-
"""끊긴 mitm을 같은 브라질 프록시로 다시 붙여 홈이 열리게 한다. 수집/로그인 삭제 없음."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
sys.path.insert(0, str(ROOT / "client 4 api integration"))
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["PYTHONUNBUFFERED"] = "1"

from br_emulator import vmos_cloud
from br_emulator.vmos_app_capture import (
    DEFAULT_PORT,
    RUNTIME,
    SHOPEE_PKG,
    _log,
    _sh,
    ensure_device,
    install_system_ca,
    launch_shopee,
    setup_reverse_proxy,
    start_mitm,
)


def main() -> int:
    rec = ensure_device()
    serial = rec["serial"]
    _log(f"  기기 {serial}")
    try:
        vmos_cloud.device_probe(serial)
    except Exception:
        rec = vmos_cloud.ensure_connected()
        serial = rec["serial"]
        _log(f"  ADB 재연결 {serial}")
    up = ""
    saved = RUNTIME / "upstream.txt"
    if saved.is_file():
        up = saved.read_text(encoding="utf-8").strip()
    if not up:
        _log("  저장된 브라질 프록시 없음")
        return 2
    install_system_ca(serial)
    start_mitm(DEFAULT_PORT, upstream=up)
    setup_reverse_proxy(serial, DEFAULT_PORT)
    _sh(serial, f"am force-stop {SHOPEE_PKG}")
    launch_shopee(serial)
    _log("홈 경로 복구 — 같은 브라질 프록시, mitm 유지")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
