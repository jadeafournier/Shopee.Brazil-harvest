# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
sys.path.insert(0, str(ROOT / "client 4 api integration"))
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["PYTHONUNBUFFERED"] = "1"

from br_emulator.vmos_app_capture import (
    DEFAULT_PORT,
    RUNTIME,
    SHOPEE_PKG,
    _log,
    _sh,
    ensure_device,
    setup_reverse_proxy,
    start_mitm,
    launch_shopee,
)


def main() -> int:
    serial = ensure_device()["serial"]
    up = (RUNTIME / "upstream.txt").read_text(encoding="utf-8").strip()
    start_mitm(DEFAULT_PORT, upstream=up)
    setup_reverse_proxy(serial, DEFAULT_PORT)
    _sh(serial, f"am force-stop {SHOPEE_PKG}")
    launch_shopee(serial)
    _log("mitm 재시작 + 홈 — 쿠키 배너 숨김")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
