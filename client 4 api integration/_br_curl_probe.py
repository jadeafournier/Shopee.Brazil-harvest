# -*- coding: utf-8 -*-
"""Quick probe of all non-browser BR extraction paths."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_probe import (
    BR_TEST, SHOPEE_BR, UA_CHROME, UA_FB, UA_GBOT,
    encode_proxy_url, parse_br_test, parse_url_ids,
    try_anon_get_pc, try_cookie_curl_getpc, try_pdp_html,
)
import proxy_pool
from curl_cffi import requests as cr

cookies, plines, urls = parse_br_test(BR_TEST)
proxy_pool.set_region("BR")
proxy_pool.load()
px = encode_proxy_url(plines[0])
proxies = {"http": px, "https": px}
sid, iid = parse_url_ids(urls[0])
pdp = f"{SHOPEE_BR}/product/{sid}/{iid}"

print("=== API (curl) ===")
for name, fn, args in [
    ("anon_get_pc", try_anon_get_pc, (sid, iid, px)),
    ("cookie_get_pc", try_cookie_curl_getpc, (sid, iid, cookies, px)),
]:
    r, d = fn(*args)
    print(name, "ok=", r.get("ok"), "err=", r.get("error"))

rw = (
    f"{SHOPEE_BR}/api/v4/pdp/get_rw?item_id={iid}&shop_id={sid}"
    f"&tz_offset_in_minutes=180&detail_level=0"
    f"&incoming_pdp_page_source=0&incoming_pdp_page_scenario=0"
)
hdrs = {
    "User-Agent": UA_CHROME,
    "Accept": "application/json",
    "x-api-source": "pc",
    "x-shopee-language": "pt",
    "Referer": pdp,
}
for label, ck in [("rw_anon", {}), ("rw_cookies", cookies)]:
    resp = cr.get(rw, impersonate="chrome131", headers=hdrs, cookies=ck, proxies=proxies, timeout=30)
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:120]}
    item = (body.get("data") or {}).get("item") or {}
    print(label, resp.status_code, body.get("error"), "item=", bool(item.get("item_id")))

print("\n=== HTML crawlers ===")
for label, ua in [("chrome", UA_CHROME), ("facebook", UA_FB), ("googlebot", UA_GBOT)]:
    r = try_pdp_html(label, pdp, ua, px)
    print(label, "ok=", r.get("ok"), "initialState=", r.get("has_initialState"), "og=", (r.get("og_title") or "")[:50])

print("\n=== FB HTML price scan ===")
resp = cr.get(pdp, impersonate="chrome131", headers={"User-Agent": UA_FB}, proxies=proxies, timeout=30)
html = resp.text or ""
for tag in ("og:title", "product:price:amount", "og:price:amount", "og:description"):
    m = re.search(rf'property="{re.escape(tag)}"[^>]+content="([^"]+)"', html)
    print(tag, (m.group(1)[:100] if m else None))
blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
print("jsonld blocks:", len(blocks))
for block in blocks[:2]:
    try:
        j = json.loads(block)
        print(" ld type:", j.get("@type"), "offers keys:", list((j.get("offers") or {}).keys())[:6])
    except Exception as exc:
        print(" ld parse err:", exc)
