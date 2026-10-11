# -*- coding: utf-8 -*-
"""482건 때와 동일: 상류 프록시 없음, 앱 한 번 재기동, 상품마다 am start -S.

이 스크립트는 사용자가 수집 시작을 명시한 뒤에만 실행한다.
로그인 확인 후 자동으로 돌리지 않는다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
HERE = Path(__file__).resolve().parent
LOG = HERE / "tk_br_rows23_120_part1_noproxy_500.log"
URLS = HERE / "tk_br_rows23_120_part1_urls.txt"

os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HARVEST_LOG"] = str(LOG)
with LOG.open("a", encoding="utf-8") as fh:
    fh.write("\n---- no-proxy bounce-4 home-abort rest-40 captcha-wait ----\n")

sys.path.insert(0, str(ROOT / "client 4 api integration"))
sys.argv = [
    "vmos_app_capture",
    "--method", "기본방식",
    "--no-proxy",
    "--go",
    "--keep-proxy",
    "--urls",
    str(URLS),
]
from br_emulator.vmos_app_capture import main

if __name__ == "__main__":
    raise SystemExit(main())
