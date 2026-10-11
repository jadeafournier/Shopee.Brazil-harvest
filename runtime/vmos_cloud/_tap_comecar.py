# -*- coding: utf-8 -*-
import os
import sys
import time

sys.path.insert(0, r"D:\Luxury\Great Scraper2\Great Scraper\client 4 api integration")
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"

from br_emulator.vmos_app_capture import _sh, ensure_device

s = ensure_device()["serial"]
# COMEÇAR 하단 가운데
_sh(s, "input tap 360 1505")
time.sleep(3.0)
_sh(s, "input tap 648 1584")
time.sleep(2.5)
_sh(s, "input tap 458 182")
print("tapped", flush=True)
