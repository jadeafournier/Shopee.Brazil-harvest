# -*- coding: utf-8 -*-
"""Quick single-URL BR get_pc test with encoded proxy + cookies."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))

from _br_probe import parse_br_test, encode_proxy_url, parse_url_ids, summarize_data, get_pc_api_url, get_pc_headers, BR_TEST

cookies, proxy_lines, urls = parse_br_test(BR_TEST)
proxy = encode_proxy_url(proxy_lines[0])
url = urls[0]
shop_id, item_id = parse_url_ids(url)

print("proxy head:", proxy[:90], "...")
print(f"target i.{shop_id}.{item_id}")

from curl_cffi import requests as cr

proxies = {"http": proxy, "https": proxy}

# 1) anon get_pc
r = cr.get(get_pc_api_url(shop_id, item_id), impersonate="chrome131",
           headers=get_pc_headers(shop_id, item_id), proxies=proxies, timeout=30)
try:
    b = r.json()
    print("anon get_pc:", r.status_code, "error=", b.get("error"), "msg=", (b.get("error_msg") or "")[:80])
except Exception as e:
    print("anon get_pc non-json:", r.status_code, str(e)[:100])

# 2) cookie curl get_pc
r2 = cr.get(get_pc_api_url(shop_id, item_id), impersonate="chrome131",
            headers=get_pc_headers(shop_id, item_id), cookies=cookies, proxies=proxies, timeout=30)
ok = False
try:
    b2 = r2.json()
    err = b2.get("error")
    data = b2.get("data") or {}
    item = data.get("item") or {}
    ok = err in (0, None) and item.get("item_id")
    print("cookie curl get_pc:", r2.status_code, "error=", err, "ok=", ok)
    if ok:
        print("summary:", json.dumps(summarize_data(data), ensure_ascii=False))
except Exception as e:
    print("cookie curl non-json:", r2.status_code, str(e)[:100])

# 3) sign oracle (only if cookie curl failed)
if not ok:
    import proxy_pool
    import getpc_client
    proxy_pool.set_region("BR")
    proxy_pool.load()
    data, meta = getpc_client.fetch(shop_id, item_id, cookies, region="BR", proxy=proxy, retries=1)
    print("sign oracle:", meta)
    if data:
        print("oracle summary:", json.dumps(summarize_data(data), ensure_ascii=False))
