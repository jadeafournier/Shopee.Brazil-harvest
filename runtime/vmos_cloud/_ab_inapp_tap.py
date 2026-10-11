# -*- coding: utf-8 -*-
"""B(인앱 탭): 팝업 닫고 홈의 상품 썸네일을 탭해서 진입. pdp/get 상태 측정."""
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


def tap(x: int, y: int) -> None:
    cap._adb(SERIAL, "shell", f"input tap {x} {y}")


def pdp_lines(since: int) -> list[str]:
    out = []
    for ln in cap._mitm_since(since).splitlines():
        if "/pdp/get?" in ln:
            out.append(ln.strip())
    return out


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
        # 팝업 닫기 (10.10 배너 X 버튼 ~ 720x1280 기준 우상단)
        cap._log("팝업 닫기 시도")
        tap(626, 258)
        time.sleep(1.2)
        tap(626, 258)  # 혹시 두 겹일 때
        time.sleep(1.0)
        off = cap._mitm_size()
        # 홈 하단 왼쪽 상품 썸네일(아이폰) 탭 → 진짜 인앱 진입
        cap._log("상품 썸네일 탭")
        tap(180, 860)
        # pdp/get 대기
        t = time.time()
        while time.time() - t < 25 and not pdp_lines(off):
            time.sleep(0.5)
        ls = pdp_lines(off)
        if not ls:
            # 두번째 시도: 다른 썸네일
            cap._log("두번째 썸네일 탭")
            tap(540, 980)
            t = time.time()
            while time.time() - t < 25 and not pdp_lines(off):
                time.sleep(0.5)
            ls = pdp_lines(off)
        cap._log(f"B(인앱탭) pdp/get {len(ls)}건")
        for x in ls:
            cap._log("  B> " + x[:150])
        cap._log(f"=== B(인앱 탭) 판정: {verdict(ls)} ===")
    finally:
        cap.stop_mitm(mitm)
        cap.clear_proxy(SERIAL, 8088)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
