# -*- coding: utf-8 -*-
import os
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
sys.path.insert(0, str(ROOT / "client 4 api integration"))
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"

from br_emulator.vmos_app_capture import (
    DEFAULT_PORT,
    HOME_ACT,
    PROXY_ACT,
    SHOPEE_PKG,
    _log,
    _sh,
    _view,
    ensure_device,
    launch_shopee,
    setup_reverse_proxy,
)


def main() -> int:
    serial = ensure_device()["serial"]
    try:
        setup_reverse_proxy(serial, DEFAULT_PORT)
    except Exception as exc:
        _log(f"reverse: {exc}")
    _sh(serial, f"am force-stop {SHOPEE_PKG}")
    time.sleep(0.8)
    _sh(serial, f"am start -n {HOME_ACT}")
    time.sleep(5.0)
    url = "https://shopee.com.br/buyer/login"
    _log(f"로그인 화면 VIEW {url}")
    out = _view(serial, url, stop_first=True)
    _log((out or "")[:200])
    time.sleep(4.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
