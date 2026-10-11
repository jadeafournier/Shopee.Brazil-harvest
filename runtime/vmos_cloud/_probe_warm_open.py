# -*- coding: utf-8 -*-
"""켜 둔 앱이 상품 VIEW 를 받는지 플래그별로 확인."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["SHOPEE_PKG"] = "com.shopee.tw"
os.environ["SHOPEE_WEB_HOST"] = "shopee.tw"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HARVEST_LOG"] = str(ROOT / "runtime" / "vmos_cloud" / "_warm_open.log")

sys.path.insert(0, str(ROOT / "client 4 api integration"))
from br_emulator import vmos_app_capture as cap  # noqa: E402

SERIAL = "localhost:60907"
SHOP, ITEM = 92647135, 4138120469
URL = f"https://shopee.tw/product/{SHOP}/{ITEM}"


def pdp(since: int) -> list[str]:
    return [ln.strip()[:140] for ln in cap._mitm_since(since).splitlines() if "/pdp/get?" in ln]


def try_open(label: str, cmd: str) -> None:
    off = cap._mitm_size()
    cap._log(f"=== {label} ===")
    out = cap._sh(SERIAL, cmd)
    if out.strip():
        cap._log("  am: " + out.strip()[:180])
    t = time.time()
    while time.time() - t < 8 and not pdp(off):
        time.sleep(0.4)
    ls = pdp(off)
    cap._log(f"  pdp {len(ls)}건" + ((" | " + ls[-1]) if ls else " | 없음"))
    time.sleep(1.0)


def main() -> int:
    mitm = cap.start_mitm(8088, upstream=None)
    cap.setup_reverse_proxy(SERIAL, 8088)
    try:
        cap._sh(SERIAL, f"monkey -p {cap.SHOPEE_PKG} -c android.intent.category.LAUNCHER 1")
        time.sleep(5)
        q = URL.replace("&", "\\&")
        act = cap.PROXY_ACT
        base = f'-n {act} -a android.intent.action.VIEW -c android.intent.category.BROWSABLE -d "{q}"'
        try_open("noflag", f"am start {base}")
        try_open("NEW_TASK", f"am start -f 0x10000000 {base}")
        try_open("NEW_TASK|CLEAR_TOP", f"am start -f 0x14000000 {base}")
        try_open("NEW_TASK|CLEAR_TASK", f"am start -f 0x10008000 {base}")
        cap._sh(SERIAL, "input keyevent KEYCODE_HOME")
        time.sleep(1.0)
        try_open("HOME then noflag", f"am start {base}")
    finally:
        cap.stop_mitm(mitm)
        cap.clear_proxy(SERIAL, 8088)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
