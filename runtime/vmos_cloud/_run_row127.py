# -*- coding: utf-8 -*-
"""row_127 상품 목록. 기본방식, 300건마다 세션 전환. 저장은 별도 폴더."""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
HERE = Path(__file__).resolve().parent
LOG = HERE / "row127.log"
URLS = HERE / "row127_urls.txt"
CSV_PATH = Path(r"C:\Users\Administrator\Pictures\client5_target_84_shipfrom\row_127\products.csv")
OUT = ROOT / "client 4 api integration" / "br_deliverable_row127"

os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["SHOPEE_PKG"] = "com.shopee.br"
os.environ["SHOPEE_WEB_HOST"] = "shopee.com.br"
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HARVEST_LOG"] = str(LOG)
os.environ.pop("HARVEST_HUMAN", None)
os.environ.pop("HARVEST_MODE", None)
os.environ["SHOPEE_OUT_DIR"] = str(OUT)
os.environ.pop("BLOCKED_USERIDS", None)
os.environ.pop("PHONE_EGRESS", None)

sys.path.insert(0, str(ROOT / "client 4 api integration"))
from br_emulator import _save  # noqa: E402

seen: set[tuple[int, int]] = set()
todo: list[str] = []
with CSV_PATH.open(encoding="utf-8-sig", newline="") as fh:
    for row in csv.DictReader(fh):
        try:
            shop_id = int(row["shop_id"])
            item_id = int(row["item_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if (shop_id, item_id) in seen:
            continue
        seen.add((shop_id, item_id))
        if _save.already_saved(shop_id, item_id):
            continue
        todo.append(f"https://shopee.com.br/product/{shop_id}/{item_id}")
URLS.write_text("\n".join(todo) + ("\n" if todo else ""), encoding="utf-8")
print(f"row127 unique={len(seen)} todo={len(todo)} out={OUT}", flush=True)

with LOG.open("a", encoding="utf-8") as fh:
    fh.write("\n---- row127 기본방식 300건 세션 전환 ----\n")

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
