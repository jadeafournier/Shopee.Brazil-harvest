# -*- coding: utf-8 -*-
"""현재 로그인 삭제 후, 같은 브라질 프록시 위에서 로그인 화면(Entrar)을 연다."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "client 4 api integration"))
sys.path.insert(0, str(HERE))
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["PYTHONUNBUFFERED"] = "1"

from br_emulator.vmos_app_capture import (
    DEFAULT_PORT,
    SHOPEE_PKG,
    _account_userid_on_device,
    _log,
    _sh,
    ensure_device,
    install_system_ca,
    reset_shopee_login,
    setup_reverse_proxy,
    start_mitm,
)
from _fresh_login_launch import pick_quality


def main() -> int:
    rec = ensure_device()
    serial = rec["serial"]
    old = _account_userid_on_device(serial)
    _log(f"  현재 userid={old or '-'}")
    upstream = pick_quality()
    install_system_ca(serial)
    start_mitm(DEFAULT_PORT, upstream=upstream)
    setup_reverse_proxy(serial, DEFAULT_PORT)
    reset_shopee_login(serial)
    time.sleep(5.0)
    # Eu 탭 → Entrar
    _sh(serial, "input tap 648 1584")
    time.sleep(3.5)
    _sh(serial, "input tap 458 182")
    time.sleep(2.0)
    uid = _account_userid_on_device(serial)
    _log(f"  로그인 삭제 후 userid={uid or '-'} — Entrar 화면에서 다른 계정으로 로그인하세요")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
