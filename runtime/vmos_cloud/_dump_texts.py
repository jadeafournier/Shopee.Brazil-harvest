# -*- coding: utf-8 -*-
import re
import subprocess
from pathlib import Path

adb = r"D:\Luxury\Great Scraper2\Great Scraper\emu_setup\platform-tools\adb.exe"
serial = "localhost:54639"
dump = "/sdcard/uidump.xml"
local = Path(r"D:\Luxury\Great Scraper2\Great Scraper\runtime\vmos_cloud\uidump.xml")
subprocess.run([adb, "-s", serial, "shell", "rm", "-f", dump], capture_output=True)
subprocess.run([adb, "-s", serial, "shell", "uiautomator", "dump", dump], capture_output=True)
subprocess.run([adb, "-s", serial, "pull", dump, str(local)], capture_output=True)
xml = local.read_text(encoding="utf-8", errors="replace") if local.exists() else ""
print("bytes", len(xml))
for m in re.finditer(r'text="([^"]+)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
    print(repr(m.group(1)), m.group(2), m.group(3), m.group(4), m.group(5))
