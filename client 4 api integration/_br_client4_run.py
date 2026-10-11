# -*- coding: utf-8 -*-
"""Customer 4 BR pipeline — single session, keep AC_CERT_D."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_patchright_extract import main

if __name__ == "__main__":
    raise SystemExit(main())
