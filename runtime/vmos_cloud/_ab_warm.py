# -*- coding: utf-8 -*-
"""웜 진입 확인: 앱이 떠 있는 기존 세션으로 상품을 열 때 pdp/get 상태."""
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
os.environ["HARVEST_LOG"] = str(ROOT / "runtime" / "vmos_cloud" / "_ab_probe.log")

sys.path.insert(0, str(ROOT / "client 4 api integration"))
from br_emulator import vmos_app_capture as cap  # noqa: E402

SERIAL = "localhost:60907"
SHOP, ITEM = 300846271, 18481094241


def pdp_lines(since: int):
    return [ln.strip() for ln in cap._mitm_since(since).splitlines() if "/pdp/get?" in ln]


def verdict(lines):
    if not lines:
        return "응답없음"
    if any("418" in x or "90309999" in x for x in lines):
        return "418(차단)"
    if any("st=200" in x for x in lines):
        return "200(정상)"
    return "기타"


def main() -> int:
    mitm = cap.start_mitm(8088, upstream=None)
    cap.setup_reverse_proxy(SERIAL, 8088)
    try:
        # 앱을 홈으로 먼저 웜업(콜드 -S 아님)
        cap._adb(SERIAL, "shell", f"monkey -p {cap.SHOPEE_PKG} -c android.intent.category.LAUNCHER 1")
        time.sleep(6)
        off = cap._mitm_size()
        cap._log("=== 웜 딥링크(restart=False)로 상품 열기 ===")
        cap.open_product(SERIAL, SHOP, ITEM, restart=False)
        t = time.time()
        while time.time() - t < 20 and not pdp_lines(off):
            time.sleep(0.5)
        ls = pdp_lines(off)
        cap._log(f"웜 pdp/get {len(ls)}건")
        for x in ls:
            cap._log("  W> " + x[:150])
        cap._log(f"=== 웜 진입 판정: {verdict(ls)} ===")
    finally:
        cap.stop_mitm(mitm)
        cap.clear_proxy(SERIAL, 8088)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
