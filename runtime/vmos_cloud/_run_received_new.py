# -*- coding: utf-8 -*-
"""received_tasks.jsonl 미저장분. 기본방식, 현재 로그인 계정 유지."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
HERE = Path(__file__).resolve().parent
LOG = HERE / "received_tasks_new.log"
URLS = HERE / "received_tasks_new_urls.txt"

os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["SHOPEE_PKG"] = "com.shopee.br"
os.environ["SHOPEE_WEB_HOST"] = "shopee.com.br"
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HARVEST_LOG"] = str(LOG)
os.environ.pop("HARVEST_HUMAN", None)
os.environ.pop("HARVEST_MODE", None)
os.environ["SHOPEE_OUT_DIR"] = str(
    ROOT / "client 4 api integration" / "br_deliverable_20260927"
)
os.environ.pop("BLOCKED_USERIDS", None)
os.environ.pop("PHONE_EGRESS", None)

with LOG.open("a", encoding="utf-8") as fh:
    fh.write("\n---- received_tasks new 기본방식 ----\n")

sys.path.insert(0, str(ROOT / "client 4 api integration"))
sys.argv = [
    "vmos_app_capture",
    "--method", "기본방식",
    "--no-proxy",
    "--go",
    "--urls",
    str(URLS),
]
from br_emulator.vmos_app_capture import main

if __name__ == "__main__":
    raise SystemExit(main())
