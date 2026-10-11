# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
sys.path.insert(0, str(ROOT / "client 4 api integration"))
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"

from br_emulator.vmos_app_capture import ensure_device, _vu

serial = ensure_device()["serial"]
for name in ("COOKIE_MANAGER_PREF.xml", "native_home_page.xml", "loginStore.xml"):
    print("====", name)
    print(_vu(serial, f"cat /data/data/com.shopee.br/shared_prefs/{name}", timeout=12)[:4000])
