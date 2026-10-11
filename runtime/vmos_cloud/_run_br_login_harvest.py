# -*- coding: utf-8 -*-
"""품질 좋은 BR 프록시 채택 → 그 IP 위에서 새 로그인 대기 → 수집."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
HERE = Path(__file__).resolve().parent
LOG = HERE / "tk_br_rows23_120_part1_restart_capture.log"
URLS = HERE / "tk_br_rows23_120_part1_urls.txt"

os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["HARVEST_LOG"] = str(LOG)
os.environ["PYTHONIOENCODING"] = "utf-8"
LOG.write_text("", encoding="utf-8")

sys.path.insert(0, str(HERE))
from _fresh_login_launch import pick_quality  # noqa: E402

pick_quality()

sys.path.insert(0, str(ROOT / "client 4 api integration"))
sys.argv = [
    "vmos_app_capture",
    "--wait-login",
    "--force-relogin",
    "--keep-proxy",
    "--urls",
    str(URLS),
]
from br_emulator.vmos_app_capture import main

if __name__ == "__main__":
    raise SystemExit(main())
