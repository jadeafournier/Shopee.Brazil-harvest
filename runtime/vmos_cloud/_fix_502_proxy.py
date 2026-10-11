# -*- coding: utf-8 -*-
"""502는 상류 프록시가 끊긴 것. 새 São Paulo 세션으로 mitm만 교체."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "client 4 api integration"))
sys.path.insert(0, str(HERE))
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["PYTHONUNBUFFERED"] = "1"

from br_emulator.vmos_app_capture import (
    DEFAULT_PORT,
    _log,
    ensure_device,
    setup_reverse_proxy,
    start_mitm,
)
from _fresh_login_launch import pick_quality


def main() -> int:
    serial = ensure_device()["serial"]
    up = pick_quality(tries=5)
    start_mitm(DEFAULT_PORT, upstream=up)
    setup_reverse_proxy(serial, DEFAULT_PORT)
    _log("새 브라질 상류로 mitm 교체 — 인증 화면에서 뒤로 후 다시 시도")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
