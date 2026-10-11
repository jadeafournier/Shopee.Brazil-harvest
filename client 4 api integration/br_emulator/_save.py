# -*- coding: utf-8 -*-
"""에뮬레이터 경로 공용 저장/유틸 — 기존 Client 1 봉투 계약을 그대로 재사용.

에뮬레이터(실기기 대체)로 확보한 상품 데이터를 브라우저/모바일 플릿과
동일한 형식으로 저장하여 하류 파이프라인이 소스에 무관하게 동작하도록 한다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DIR = Path(__file__).resolve().parent
CLIENT4 = DIR.parent
ROOT = CLIENT4.parent
for p in (str(ROOT), str(CLIENT4), str(ROOT / "shopee_getpc_crawler")):
    if p not in sys.path:
        sys.path.insert(0, p)

# Prefer deliverable dir for app-signed get_pc captures (falls back if unset).
OUT_DIR = Path(os.environ.get("SHOPEE_OUT_DIR") or (CLIENT4 / "br_deliverable_getpc"))
INDEX_JSONL = OUT_DIR / "captured_index.jsonl"
PRODUCT_HOST = os.environ.get("SHOPEE_WEB_HOST") or "shopee.com.br"

_LOCK = threading.Lock()


def iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_ids(url_or_text: str) -> tuple[int, int] | None:
    """URL 또는 임의 텍스트에서 (shop_id, item_id) 추출. 실패 시 None."""
    s = url_or_text or ""
    m = re.search(r"-i\.(\d+)\.(\d+)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"/product/(\d+)/(\d+)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    # API 쿼리스트링: shop_id / item_id (get_pc, get_rw) 또는 shopid/itemid (item/get)
    ms = re.search(r"shop_?id=(\d+)", s)
    mi = re.search(r"item_?id=(\d+)", s)
    if ms and mi:
        return int(ms.group(1)), int(mi.group(1))
    m = re.search(r"&&&&(\d+)&&&&(\d+)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def product_url(shop_id: int, item_id: int) -> str:
    return f"https://{PRODUCT_HOST}/product/{shop_id}/{item_id}"


def out_path(shop_id: int, item_id: int) -> Path:
    return OUT_DIR / f"{shop_id}_{item_id}.json"


def legacy_out_path(shop_id: int, item_id: int) -> Path:
    return OUT_DIR / f"output_{shop_id}_{item_id}.json"


_SAVED_DIRS: list[Path] | None = None


def _saved_dirs() -> list[Path]:
    """새 저장 폴더와, 이미 받아 둔 기존 폴더. 같은 상품을 다시 받지 않기 위함."""
    global _SAVED_DIRS
    if _SAVED_DIRS is not None:
        return _SAVED_DIRS
    dirs = [OUT_DIR]
    seen = {OUT_DIR.resolve()}
    received = CLIENT4 / "br_deliverable_received"
    candidates = [
        CLIENT4 / name
        for name in (
            "br_deliverable_getpc",
            "br_deliverable_20260927",
            "br_deliverable_row127",
            "br_deliverable_row128",
            "br_deliverable_rows25_27",
            "br_deliverable_inbox",
            "br_deliverable_received",
        )
    ]
    if received.is_dir():
        candidates.extend(p for p in received.iterdir() if p.is_dir())
    upload = ROOT / "Shopee.Brazil" / "Upload"
    if upload.is_dir():
        candidates.append(upload)
        candidates.extend(p for p in upload.iterdir() if p.is_dir())
    for historical in candidates:
        if not historical.is_dir():
            continue
        key = historical.resolve()
        if key in seen:
            continue
        seen.add(key)
        dirs.append(historical)
    _SAVED_DIRS = dirs
    return dirs


def already_saved(shop_id: int, item_id: int) -> bool:
    names = (f"{shop_id}_{item_id}.json", f"output_{shop_id}_{item_id}.json")
    return any((d / name).is_file() for d in _saved_dirs() for name in names)


# 웹 get_pc 패킷(client5 save_packet)과 같은 data 키 순서.
WEB_DATA_KEYS = (
    "item", "account", "product_images", "product_price", "flash_sale",
    "flash_sale_preview", "deep_discount", "exclusive_price", "exclusive_price_cta",
    "product_meta", "product_review", "promotion_info", "age_gate", "shipping_meta",
    "product_shipping", "shop_vouchers", "free_return", "coin_info", "product_attributes",
    "shop_detailed", "age_gate_control", "abnormal_popup", "return_on_spot",
    "display_sections", "ineligible_ep", "tax_disclaimer", "alcohol_disclaimer",
    "design_control", "shopee_free_return", "detail_level", "removed_fields",
    "size_guide", "designer_info", "shipping_info", "vehicle_compatibility_info",
    "nea", "button_group", "installment_drawer", "ongoing_banner", "teaser_banner",
    "price_breakdown", "membership_exclusive", "membership_exclusive_teaser",
    "service_entrance", "service_drawer", "trade_in", "product_description",
    "selected_tiers", "size_info", "size_recommendation", "translation_status",
    "product_description_tr", "default_warnings", "bcrs_disclaimer", "top_banner",
    "bottom_banner",
)


def align_web_data(data: dict | None) -> dict:
    """앱 pdp/get 의 추가 섹션은 빼고, 웹에만 있는 키는 null 로 맞춘다."""
    src = data if isinstance(data, dict) else {}
    return {k: src.get(k) if k in src else None for k in WEB_DATA_KEYS}


def shopee_body(payload: dict) -> dict:
    """쇼피 JSON 본문만 남긴다. 없는 키는 만들지 않는다."""
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("data"), dict) and any(k in payload for k in ("error", "error_msg", "bff_meta")):
        return {k: payload[k] for k in payload.keys()}
    return payload


def _valid_pdp(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    item = data.get("item") if isinstance(data.get("item"), dict) else None
    if item and (item.get("item_id") or item.get("itemid") or item.get("title") or item.get("name")):
        return True
    # get_pc 는 data.item 없이 섹션 구조일 수 있음 — 최소 키 수로 판단.
    return len(data) >= 3


def save_product(url: str, data: dict, *, source: str) -> Path | None:
    """쇼피 응답 본문만 저장한다. token, api_key, task_link, source 는 파일에 넣지 않는다."""
    body = shopee_body(data)
    inner = body.get("data") if isinstance(body.get("data"), dict) else body
    if not _valid_pdp(inner):
        return None
    ids = parse_ids(url)
    if not ids:
        return None
    sid, iid = ids
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = out_path(sid, iid)
    with _LOCK:
        path.write_text(
            json.dumps(body, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        title = ""
        item = inner.get("item") if isinstance(inner.get("item"), dict) else {}
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or "")[:80]
        rec = {"shop_id": sid, "item_id": iid, "source": source, "title": title, "at": iso()}
        with INDEX_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _push_when_auto(path)
    return path


def _push_when_auto(path: Path) -> None:
    """Auto mode: dedicated upload manager sends the file as soon as it is saved."""
    try:
        runtime = ROOT / "runtime" / "vmos_cloud"
        if str(runtime) not in sys.path:
            sys.path.insert(0, str(runtime))
        import upload_manager

        upload_manager.enqueue(path)
    except Exception:
        return


def load_urls(path: Path, *, limit: int = 0, skip_saved: bool = True) -> list[str]:
    urls = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if skip_saved:
        kept = []
        for u in urls:
            ids = parse_ids(u)
            if ids and already_saved(*ids):
                continue
            kept.append(u)
        urls = kept
    if limit > 0:
        urls = urls[:limit]
    return urls


def count_saved() -> int:
    if not OUT_DIR.is_dir():
        return 0
    names = {p.name for p in OUT_DIR.glob("*.json")}
    return sum(1 for n in names if re.fullmatch(r"(?:output_)?\d+_\d+\.json", n))


def dedup_iter(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def now_s() -> float:
    return time.time()
