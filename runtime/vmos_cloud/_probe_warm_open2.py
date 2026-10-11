# -*- coding: utf-8 -*-
"""웜 상태에서 상품이 실제로 열리는 인텐트를 찾는다. 마지막에 -S 대조."""
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


def pdp(since: int) -> list[str]:
    return [ln.strip()[:120] for ln in cap._mitm_since(since).splitlines() if "/pdp/get?" in ln]


def try_open(label: str, cmd: str) -> bool:
    off = cap._mitm_size()
    cap._log(f"=== {label} ===")
    out = cap._sh(SERIAL, cmd)
    if out.strip():
        cap._log("  " + out.strip().replace("\n", " ")[:200])
    t = time.time()
    while time.time() - t < 7 and not pdp(off):
        time.sleep(0.4)
    ls = pdp(off)
    cap._log("  pdp " + (ls[-1] if ls else "없음"))
    return bool(ls)


def main() -> int:
    mitm = cap.start_mitm(8088, upstream=None)
    cap.setup_reverse_proxy(SERIAL, 8088)
    try:
        cap._sh(SERIAL, f"monkey -p {cap.SHOPEE_PKG} -c android.intent.category.LAUNCHER 1")
        time.sleep(4)
        urls = [
            f"https://shopee.tw/product/{SHOP}/{ITEM}",
            f"https://shopee.tw/universal-link/product/{SHOP}/{ITEM}",
            f"shopeetw://product/{SHOP}/{ITEM}",
        ]
        for u in urls:
            q = u.replace("&", "\\&")
            try_open(f"implicit {u.split('://',1)[0]}", f'am start -a android.intent.action.VIEW -d "{q}"')
        # 프로세스는 유지하고 태스크만 비운 뒤 VIEW (force-stop 아님)
        q = urls[0].replace("&", "\\&")
        try_open(
            "CLEAR_TASK no -S",
            f'am start -f 0x10008000 -n {cap.PROXY_ACT} -a android.intent.action.VIEW '
            f'-c android.intent.category.BROWSABLE -d "{q}"',
        )
        try_open(
            "control -S",
            f'am start -S -n {cap.PROXY_ACT} -a android.intent.action.VIEW '
            f'-c android.intent.category.BROWSABLE -d "{q}"',
        )
    finally:
        cap.stop_mitm(mitm)
        cap.clear_proxy(SERIAL, 8088)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
