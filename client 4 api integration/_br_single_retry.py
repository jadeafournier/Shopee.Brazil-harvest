# -*- coding: utf-8 -*-
"""Single URL BR extract with extended PDP wait."""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))

from _br_probe import parse_br_test, parse_url_ids, summarize_data, BR_TEST
from sign_oracle import _pw_proxy
import proxy_pool

cookies, _, urls = parse_br_test(BR_TEST)
url = urls[3]  # the one that reached PDP
shop_id, item_id = parse_url_ids(url)
proxy_pool.set_region("BR")
proxy_pool.load()

from playwright.sync_api import sync_playwright

domain = "shopee.com.br"
pdp = f"https://{domain}/product/{shop_id}/{item_id}"
holder = {}
proxy = proxy_pool.pick()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, proxy=_pw_proxy(proxy), args=["--no-sandbox"])
    ctx = browser.new_context(locale="pt-BR", viewport={"width": 1366, "height": 768})
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    ctx.add_cookies([{"name": k, "value": v, "domain": f".{domain}", "path": "/"} for k, v in cookies.items() if v])
    page = ctx.new_page()

    def on_resp(resp):
        if "get_pc" in resp.url and str(item_id) in resp.url:
            try:
                j = json.loads(resp.text())
                holder.setdefault("attempts", []).append({"error": j.get("error"), "status": resp.status})
                if j.get("error") in (0, None):
                    item = (j.get("data") or {}).get("item") or {}
                    if item.get("item_id"):
                        holder["data"] = j["data"]
            except Exception as exc:
                holder.setdefault("attempts", []).append({"parse_err": str(exc)[:60]})

    page.on("response", on_resp)
    page.goto(f"https://{domain}/", wait_until="commit", timeout=60000)
    page.wait_for_timeout(3000)
    page.goto(pdp, wait_until="commit", timeout=60000)

    for i in range(120):
        if holder.get("data"):
            break
        if "/verify/" in (page.url or ""):
            try:
                page.mouse.move(random.randint(100, 1000), random.randint(100, 600))
            except Exception:
                pass
        elif "/product/" in (page.url or "") and i == 40:
            print("reload pdp")
            page.reload(wait_until="commit", timeout=60000)
        page.wait_for_timeout(500)

    print("final url:", page.url[:120])
    print("title:", (page.title() or "")[:80])
    print("get_pc attempts:", holder.get("attempts", [])[-5:])
    if holder.get("data"):
        print("SUCCESS", json.dumps(summarize_data(holder["data"]), ensure_ascii=False))
        out = Path(__file__).resolve().parent / "br_probe_output" / f"output_{shop_id}_{item_id}.json"
        out.write_text(json.dumps({
            "task_link": url, "token": "br_client4_test", "api_key": "br_client4_test",
            "bff_meta": None, "error": None, "error_msg": None, "data": holder["data"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("saved", out)
    browser.close()
