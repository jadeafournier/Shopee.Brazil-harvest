# -*- coding: utf-8 -*-
"""A/B: 딥링크 자동 오픈 vs 손으로 탭. pdp/get 상태를 비교한다."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["SHOPEE_PKG"] = "com.shopee.tw"
os.environ["SHOPEE_WEB_HOST"] = "shopee.tw"
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HARVEST_LOG"] = str(ROOT / "runtime" / "vmos_cloud" / "_ab_probe.log")

sys.path.insert(0, str(ROOT / "client 4 api integration"))
from br_emulator import vmos_app_capture as cap  # noqa: E402

SERIAL = "localhost:60907"
# A 용 상품(딥링크). B 는 사용자가 화면에서 아무 상품이나 손으로 연다.
A_SHOP, A_ITEM = 300846271, 18481094241


def pdp_lines(since: int) -> list[str]:
    out = []
    for ln in cap._mitm_since(since).splitlines():
        if "/pdp/get?" in ln or "/pdp/get " in ln:
            out.append(ln.strip()[:130])
    return out


def main() -> int:
    # CA 는 이전 세션에서 이미 시스템 저장소에 설치됨 → 설치 단계 생략(adb push 타임아웃 회피)
    mitm = cap.start_mitm(8088, upstream=None)
    cap.setup_reverse_proxy(SERIAL, 8088)
    try:
        # ---- A: 딥링크 자동 오픈 (콜드 스타트) ----
        off = cap._mitm_size()
        cap._log("=== A: 딥링크 자동 오픈 ===")
        cap.open_product(SERIAL, A_SHOP, A_ITEM, restart=True)
        t = time.time()
        while time.time() - t < 12 and not pdp_lines(off):
            time.sleep(0.5)
        a = pdp_lines(off)
        cap._log(f"A pdp/get {len(a)}건")
        for ln in a:
            cap._log("  A> " + ln)

        # ---- B: 사용자가 화면에서 손으로 상품 열기 ----
        time.sleep(1.0)
        off_b = cap._mitm_size()
        cap._log("=== B: 지금 화면에서 손으로 아무 상품이나 여세요 (60초 대기) ===")
        t = time.time()
        while time.time() - t < 60 and not pdp_lines(off_b):
            time.sleep(0.5)
        b = pdp_lines(off_b)
        cap._log(f"B pdp/get {len(b)}건")
        for ln in b:
            cap._log("  B> " + ln)
        cap._log("=== 결과 ===")
        cap._log(f"A(딥링크): {'418' if any('418' in x or '90309999' in x for x in a) else ('200' if a else '응답없음')}")
        cap._log(f"B(수동):   {'418' if any('418' in x or '90309999' in x for x in b) else ('200' if b else '응답없음')}")
    finally:
        cap.stop_mitm(mitm)
        cap.clear_proxy(SERIAL, 8088)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
