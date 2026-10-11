# -*- coding: utf-8 -*-
"""BR product extraction helpers that do NOT require an iOS device / app signer.

Working without a physical device or forged tokens:
  A) Browser SPA intercept of pdp/get_pc   — full Client-1 style payload (proven)
  B) Browser SPA intercept of pdp/get_rw   — secondary signed payload if SPA fires it
  C) Browser SPA intercept of item/get     — rare; capture if present
  D) PDP HTML / OG / JSON-LD / initialState — partial fields when APIs fail
  E) Listing cards (search/recommend/shop) — bulk title/price/sold without PDP

Never: CapSolver, forged x-sap-sec / AC_CERT_*, curl get_pc (always 90309999 on BR).
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional


def is_ok_payload(body: dict | None) -> bool:
    if not isinstance(body, dict):
        return False
    if body.get("error") not in (0, None):
        return False
    data = body.get("data")
    return isinstance(data, dict) and bool(data)


def item_from_pdp_data(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    item = data.get("item")
    return item if isinstance(item, dict) else {}


def match_item_id(data: dict, item_id: int) -> bool:
    item = item_from_pdp_data(data)
    try:
        return int(item.get("item_id") or item.get("itemid") or 0) == int(item_id)
    except Exception:
        return False


def classify_api_url(url: str) -> Optional[str]:
    u = url or ""
    if "/api/v4/pdp/get_pc" in u:
        return "get_pc"
    if "/api/v4/pdp/get_rw" in u:
        return "get_rw"
    if "/api/v4/item/get" in u:
        return "item_get"
    return None


def parse_html_partial(html: str, *, shop_id: int, item_id: int) -> Optional[dict]:
    """Best-effort PDP HTML scrape — titles/prices only, never a full get_pc substitute."""
    if not html or len(html) < 40:
        return None
    out: dict[str, Any] = {
        "source": "html_partial",
        "shop_id": shop_id,
        "item_id": item_id,
    }
    def _meta(prop: str) -> str | None:
        m = re.search(
            rf'property=["\']{re.escape(prop)}["\']\s+content=["\']([^"\']+)["\']',
            html,
            flags=re.I,
        )
        if m:
            return m.group(1)
        m = re.search(
            rf'content=["\']([^"\']+)["\']\s+property=["\']{re.escape(prop)}["\']',
            html,
            flags=re.I,
        )
        return m.group(1) if m else None

    og_title = _meta("og:title")
    og_desc = _meta("og:description")
    og_img = _meta("og:image")
    price = _meta("product:price:amount") or _meta("og:price:amount")
    currency = _meta("product:price:currency")
    if og_title:
        out["title"] = og_title
    if og_desc:
        out["description"] = og_desc[:2000]
    if og_img:
        out["image"] = og_img
    if price:
        out["price"] = price
    if currency:
        out["currency"] = currency

    # JSON-LD Product
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.I | re.S,
    ):
        raw = (m.group(1) or "").strip()
        if not raw:
            continue
        try:
            node = json.loads(raw)
        except Exception:
            continue
        nodes = node if isinstance(node, list) else [node]
        for n in nodes:
            if not isinstance(n, dict):
                continue
            typ = n.get("@type") or ""
            if isinstance(typ, list):
                typ = " ".join(str(x) for x in typ)
            if "Product" not in str(typ):
                continue
            out.setdefault("title", n.get("name"))
            out.setdefault("description", (n.get("description") or "")[:2000] or None)
            offers = n.get("offers") or {}
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                out.setdefault("price", offers.get("price"))
                out.setdefault("currency", offers.get("priceCurrency"))
            img = n.get("image")
            if isinstance(img, list) and img:
                out.setdefault("image", img[0])
            elif isinstance(img, str):
                out.setdefault("image", img)

    # Compact initialState item snippet (when present)
    for pat in (
        r'"itemid"\s*:\s*%d\s*,\s*"shopid"\s*:\s*%d' % (item_id, shop_id),
        r'"item_id"\s*:\s*%d\s*,\s*"shop_id"\s*:\s*%d' % (item_id, shop_id),
    ):
        if re.search(pat, html):
            out["has_item_ids_in_html"] = True
            break

    if out.get("title") or out.get("price"):
        # Strip Nones for cleaner JSON
        return {k: v for k, v in out.items() if v is not None}
    return None


def _as_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _card_from_obj(obj: dict) -> Optional[dict]:
    """Normalize one listing object into a product card."""
    if not isinstance(obj, dict):
        return None
    basic = obj.get("item_basic") if isinstance(obj.get("item_basic"), dict) else obj
    if not isinstance(basic, dict):
        return None
    shop_id = _as_int(basic.get("shopid") or basic.get("shop_id"))
    item_id = _as_int(basic.get("itemid") or basic.get("item_id"))
    if not shop_id or not item_id:
        return None
    rating = basic.get("item_rating") if isinstance(basic.get("item_rating"), dict) else {}
    price = basic.get("price")
    # Shopee often stores price * 100000
    price_min = basic.get("price_min")
    price_max = basic.get("price_max")
    card = {
        "shop_id": shop_id,
        "item_id": item_id,
        "url": f"https://shopee.com.br/product/{shop_id}/{item_id}",
        "name": basic.get("name") or basic.get("title") or "",
        "currency": basic.get("currency") or "BRL",
        "price": price,
        "price_min": price_min,
        "price_max": price_max,
        "price_before_discount": basic.get("price_before_discount"),
        "sold": basic.get("sold") or basic.get("historical_sold") or 0,
        "historical_sold": basic.get("historical_sold"),
        "stock": basic.get("stock"),
        "liked_count": basic.get("liked_count"),
        "ctime": basic.get("ctime"),
        "image": basic.get("image"),
        "images": basic.get("images") if isinstance(basic.get("images"), list) else None,
        "shop_location": basic.get("shop_location"),
        "rating_star": rating.get("rating_star"),
        "rating_count": rating.get("rating_count"),
        "is_official_shop": bool(basic.get("is_official_shop") or basic.get("official_shop")),
        "discount": basic.get("discount"),
        "brand": basic.get("brand"),
        "catid": basic.get("catid") or basic.get("cat_id"),
        "source": "listing_card",
    }
    return {k: v for k, v in card.items() if v is not None and v != ""}


def extract_listing_cards(obj: Any, out: dict[tuple[int, int], dict] | None = None) -> dict[tuple[int, int], dict]:
    """Walk search/recommend/shop JSON and collect unique product cards."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        card = _card_from_obj(obj)
        if card:
            key = (card["shop_id"], card["item_id"])
            # Prefer richer cards (more keys)
            prev = out.get(key)
            if prev is None or len(card) >= len(prev):
                out[key] = card
        for v in obj.values():
            extract_listing_cards(v, out)
    elif isinstance(obj, list):
        for v in obj:
            extract_listing_cards(v, out)
    return out


def client4_envelope(url: str, data: dict, *, token: str, method: str) -> dict:
    return {
        "task_link": url,
        "token": token,
        "api_key": token,
        "bff_meta": {"extract_method": method},
        "error": None,
        "error_msg": None,
        "data": data,
    }
