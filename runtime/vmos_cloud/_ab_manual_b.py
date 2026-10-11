# -*- coding: utf-8 -*-
"""B 재현(깨끗): 앱 종료→홈→사용자가 손으로 상품 진입. pdp/get 상태만 측정."""
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


def pdp_lines(since: int) -> list[str]:
    out = []
    for ln in cap._mitm_since(since).splitlines():
        if "/pdp/get?" in ln:
            out.append(ln.strip())
    return out


def verdict(lines: list[str]) -> str:
    if not lines:
        return "응답없음"
    if any("418" in x or "90309999" in x for x in lines):
        return "418(차단)"
    if any("st=200" in x for x in lines):
        return "200(정상)"
    return "기타: " + lines[-1][:80]


def main() -> int:
    mitm = cap.start_mitm(8088, upstream=None)
    cap.setup_reverse_proxy(SERIAL, 8088)
    try:
        # 앱 완전 종료 → 홈(런처)로 → 홈화면 콜드 오픈
        cap._adb(SERIAL, "shell", f"am force-stop {cap.SHOPEE_PKG}")
        time.sleep(1.0)
        cap._adb(SERIAL, "shell", "input keyevent KEYCODE_HOME")
        time.sleep(0.5)
        # 앱 홈만 띄운다(상품 딥링크 아님)
        cap._adb(SERIAL, "shell", f"monkey -p {cap.SHOPEE_PKG} -c android.intent.category.LAUNCHER 1")
        cap._log("=== 앱 홈 띄움. 이제 화면에서 손으로 검색→상품 탭 해주세요 (90초) ===")
        off = cap._mitm_size()
        t = time.time()
        while time.time() - t < 90:
            ls = pdp_lines(off)
            if ls:
                break
            time.sleep(0.5)
        ls = pdp_lines(off)
        cap._log(f"B(수동) pdp/get {len(ls)}건")
        for x in ls:
            cap._log("  B> " + x[:150])
        cap._log(f"=== B(수동 진입) 판정: {verdict(ls)} ===")
    finally:
        cap.stop_mitm(mitm)
        cap.clear_proxy(SERIAL, 8088)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
