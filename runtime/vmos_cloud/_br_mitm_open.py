# -*- coding: utf-8 -*-
"""mitm+프록시를 붙인 상태에서 같은 브라질 상품을 열고 pdp/get 상태와 화면을 본다."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["SHOPEE_PKG"] = "com.shopee.br"
os.environ["SHOPEE_WEB_HOST"] = "shopee.com.br"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HARVEST_LOG"] = str(ROOT / "runtime" / "vmos_cloud" / "_br_mitm_open.log")

sys.path.insert(0, str(ROOT / "client 4 api integration"))
from br_emulator import vmos_app_capture as cap  # noqa: E402

SERIAL = "localhost:64642"
SHOP, ITEM = 398188570, 22897433377


def pdp(since: int):
    return [ln.strip()[:150] for ln in cap._mitm_since(since).splitlines() if "/pdp/get?" in ln]


def main() -> int:
    mitm = cap.start_mitm(8088, upstream=None)
    cap.setup_reverse_proxy(SERIAL, 8088)
    try:
        off = cap._mitm_size()
        cap._log("=== mitm 붙인 상태로 상품 열기 (콜드) ===")
        cap.open_product(SERIAL, SHOP, ITEM, restart=True)
        t = time.time()
        while time.time() - t < 12 and not pdp(off):
            time.sleep(0.4)
        for x in pdp(off):
            cap._log("  " + x)
        time.sleep(2)
        cap._sh(SERIAL, "screencap -p /sdcard/_m.png")
        cap._adb(SERIAL, "pull", "/sdcard/_m.png",
                 str(ROOT / "runtime" / "vmos_cloud" / "_br_mitm.png"))
    finally:
        cap.stop_mitm(mitm)
        cap.clear_proxy(SERIAL, 8088)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
