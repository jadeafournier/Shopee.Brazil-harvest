# -*- coding: utf-8 -*-
import re
from pathlib import Path

xml = Path(__file__).with_name("uidump.xml").read_text(encoding="utf-8", errors="replace")
for m in re.finditer(r'text="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
    t, x1, y1, x2, y2 = m.groups()
    if t.strip():
        print(repr(t), x1, y1, x2, y2)
print("---clickable---")
for m in re.finditer(
    r'text="([^"]*)"[^>]*clickable="true"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml
):
    print("click", repr(m.group(1)), m.group(2), m.group(3), m.group(4), m.group(5))
