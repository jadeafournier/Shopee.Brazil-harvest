# -*- coding: utf-8 -*-
"""런처: iOS/모바일 플릿 (한국어).

  python "_br_ios_fleet.py" --probe
  python "_br_ios_fleet.py" --urls br_active_product_urls_1000.txt --workers 20 --limit 50
"""
from __future__ import annotations

import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
sys.path.insert(0, str(DIR.parent))

from br_ios_fleet.fleet_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
