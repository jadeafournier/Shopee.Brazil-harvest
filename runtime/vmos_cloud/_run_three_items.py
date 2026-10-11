# -*- coding: utf-8 -*-
"""상품 번호 3개의 상점 번호를 앱 검색으로 찾은 뒤 수집한다."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["SHOPEE_PKG"] = "com.shopee.br"
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["BR_EMU_HARVEST_URLS"] = "1"
os.environ["BR_EMU_VERBOSE"] = "1"
os.environ["HARVEST_LOG"] = str(ROOT / "runtime" / "vmos_cloud" / "br_three_items.log")

sys.path.insert(0, str(ROOT / "client 4 api integration"))

from br_emulator import vmos_app_capture as cap  # noqa: E402
from br_emulator import _save  # noqa: E402

ITEMS = [10018838198, 10014765377, 10021285534]
SERIAL = "localhost:64642"
URL_FILE = _save.CLIENT4 / "br_active_product_urls_100k.txt"


def search_one(item_id: int) -> None:
    uri = f"https://shopee.com.br/search?keyword={item_id}"
    cap._log(f"검색 {item_id}")
    cap._view(SERIAL, uri, stop_first=True)
    time.sleep(8.0)


def pairs_for(item_id: int) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    text = cap.MITM_LOG.read_text(encoding="utf-8", errors="replace") if cap.MITM_LOG.is_file() else ""
    # 로그 URL 과 수확 파일 양쪽에서 shop/item 을 찾는다.
    blob = text
    if URL_FILE.is_file():
        blob += "\n" + URL_FILE.read_text(encoding="utf-8", errors="replace")[-400_000:]
    needle = str(item_id)
    for line in blob.splitlines():
        if needle not in line:
            continue
        ids = _save.parse_ids(line)
        if ids and ids[1] == item_id:
            found.append(ids)
            continue
        # shopid= / itemid= 가 한 줄에 없을 수 있어 item 만 있는 줄은 건너뛴다.
    # 중복 제거, 순서 유지
    out = []
    seen = set()
    for pair in found:
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def main() -> int:
    cap.install_system_ca(SERIAL)
    mitm = cap.start_mitm(8088, upstream=None)
    cap.setup_reverse_proxy(SERIAL, 8088)
    targets: list[tuple[int, int, str]] = []
    try:
        for item_id in ITEMS:
            search_one(item_id)
        time.sleep(2.0)
        targets = []
        for item_id in ITEMS:
            hits = pairs_for(item_id)
            cap._log(f"매칭 {item_id} → {hits[:3]}")
            if hits:
                shop, item = hits[0]
                targets.append((shop, item, _save.product_url(shop, item)))
        if not targets:
            cap._log("상점 번호를 찾지 못해 수집하지 않음")
            return 2
        # 검색용 mitm 을 닫고 수집 루프가 새로 띄운다.
    finally:
        cap.stop_mitm(mitm)
        cap.clear_proxy(SERIAL, 8088)
    if not targets:
        return 2
    rec = cap.capture_many(
        targets,
        port=8088,
        keep_proxy=False,
        upstream=None,
        skip_first_recover=True,
    )
    cap._log(f"결과 저장 {rec.get('saved')} 미저장 {rec.get('miss')}")
    return 0 if rec.get("saved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
