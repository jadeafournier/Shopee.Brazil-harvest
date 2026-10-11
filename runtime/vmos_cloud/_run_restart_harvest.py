# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
LOG = Path(__file__).with_name("tk_br_rows23_120_part1_fast_capture.log")
URLS = Path(__file__).with_name("tk_br_rows23_120_part1_urls.txt")

os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HARVEST_LOG"] = str(LOG)
with LOG.open("a", encoding="utf-8") as fh:
    fh.write("\n---- restart target 500/hr CLEAR_TASK ----\n")

sys.path.insert(0, str(ROOT / "client 4 api integration"))
sys.argv = [
    "vmos_app_capture",
    "--go",
    "--keep-proxy",
    "--urls",
    str(URLS),
]
from br_emulator.vmos_app_capture import main

if __name__ == "__main__":
    raise SystemExit(main())
