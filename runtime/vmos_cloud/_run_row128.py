# -*- coding: utf-8 -*-
"""row_128 을 폰 3대에 나눠 수집한다. 폰마다 포트·로그·계정이 다르다."""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
HERE = Path(__file__).resolve().parent
CSV_PATH = Path(r"C:\Users\Administrator\Pictures\client5_target_84_shipfrom\row_128\products.csv")
OUT = ROOT / "client 4 api integration" / "br_deliverable_row128"

PHONES = [
    {
        "pad": "AC20250306MHUQVN",
        "serial": "localhost:58070",
        "name": "Phone3",
        "port": "8088",
        "sessions": "1706784820,15774375407,14856138265",
    },
    {
        "pad": "ACP251005M8NPVM5",
        "serial": "localhost:55949",
        "name": "Phone2",
        "port": "8089",
        "sessions": "14836448858,13526726098,1706785017",
    },
    {
        "pad": "ACP250719PQRGGM6",
        "serial": "localhost:53463",
        "name": "Phone1",
        "port": "8090",
        "sessions": "15670411309,14797524913",
    },
]


def prepare_urls() -> int:
    sys.path.insert(0, str(ROOT / "client 4 api integration"))
    os.environ["SHOPEE_OUT_DIR"] = str(OUT)
    os.environ["SHOPEE_WEB_HOST"] = "shopee.com.br"
    from br_emulator import _save

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
    shards = [todo[i::3] for i in range(3)]
    for i, rows in enumerate(shards):
        path = HERE / f"row128_urls_{i}.txt"
        path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
        print(f"shard {i} {len(rows)}", flush=True)
    print(f"unique={len(seen)} todo={len(todo)}", flush=True)
    return len(todo)


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] == "prep":
        prepare_urls()
        return 0
    shard = int(sys.argv[1])
    phone = PHONES[shard]
    log = HERE / f"row128_{shard}.log"
    urls = HERE / f"row128_urls_{shard}.txt"
    os.environ["VMOS_PAD_CODE"] = phone["pad"]
    os.environ["SHOPEE_SERIAL"] = phone["serial"]
    os.environ["SHOPEE_PAD_NAME"] = phone["name"]
    os.environ["SHOPEE_PKG"] = "com.shopee.br"
    os.environ["SHOPEE_WEB_HOST"] = "shopee.com.br"
    os.environ["PYTHONUNBUFFERED"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["HARVEST_LOG"] = str(log)
    os.environ["SHOPEE_MITM_LOG"] = str(HERE / f"mitm_row128_{shard}.log")
    os.environ["SHOPEE_SESSION_ALLOW"] = phone["sessions"]
    os.environ["SHOPEE_OUT_DIR"] = str(OUT)
    os.environ.pop("HARVEST_HUMAN", None)
    os.environ.pop("HARVEST_MODE", None)
    os.environ.pop("BLOCKED_USERIDS", None)
    os.environ.pop("PHONE_EGRESS", None)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n---- row128 shard {shard} {phone['name']} port {phone['port']} ----\n")
    sys.path.insert(0, str(ROOT / "client 4 api integration"))
    sys.argv = [
        "vmos_app_capture",
        "--method", "기본방식",
        "--no-proxy",
        "--go",
        "--port", phone["port"],
        "--urls", str(urls),
    ]
    from br_emulator.vmos_app_capture import main as harvest_main
    return harvest_main()


if __name__ == "__main__":
    raise SystemExit(main())
