# -*- coding: utf-8 -*-
"""Debug Playwright BR PDP — log API traffic and try in-page get_pc fetch."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))

from _br_probe import parse_br_test, parse_url_ids, BR_TEST
from sign_oracle import _pw_proxy
import proxy_pool

cookies, proxy_lines, urls = parse_br_test(BR_TEST)
proxy_pool.set_region("BR")
proxy_pool.load()
proxy = proxy_pool.pick()
shop_id, item_id = parse_url_ids(urls[0])
domain = "shopee.com.br"
pdp = f"https://{domain}/product/{shop_id}/{item_id}"
api_url = (
    f"https://{domain}/api/v4/pdp/get_pc?display_model_id=0"
    f"&item_id={item_id}&model_selection_logic=3&shop_id={shop_id}"
    f"&tz_offset_in_minutes=180&detail_level=0"
)

print(f"target i.{shop_id}.{item_id}")

from playwright.sync_api import sync_playwright

hits = []
api_calls = []

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True, proxy=_pw_proxy(proxy), args=["--no-sandbox"]
    )
    ctx = browser.new_context(
        locale="pt-BR",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1366, "height": 768},
    )
    ctx.add_cookies([
        {"name": k, "value": v, "domain": f".{domain}", "path": "/"}
        for k, v in cookies.items() if v
    ])
    page = ctx.new_page()

    def on_resp(resp):
        u = resp.url
        if f"{domain}/api" in u:
            api_calls.append({"status": resp.status, "url": u[-140:]})
        if "get_pc" in u and str(item_id) in u:
            try:
                j = json.loads(resp.text())
                hits.append({"status": resp.status, "error": j.get("error"),
                             "has_item": bool((j.get("data") or {}).get("item", {}).get("item_id"))})
            except Exception as exc:
                hits.append({"status": resp.status, "parse_err": str(exc)[:80]})

    page.on("response", on_resp)
    page.goto(f"https://{domain}/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    page.goto(pdp, wait_until="domcontentloaded", timeout=60000)
    for _ in range(40):
        if hits:
            break
        page.wait_for_timeout(500)

    html = page.content()
    has_state = '"initialState":{' in html or "window.__INITIAL_STATE__" in html
    og = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    print("pdp url:", page.url)
    print("title:", (page.title() or "")[:80])
    print("html len:", len(html), "initialState:", has_state, "og:", (og.group(1)[:60] if og else None))
    print("api calls:", len(api_calls))
    for c in api_calls[:12]:
        print(" ", c)
    print("passive get_pc hits:", hits)

    # in-page fetch (uses browser cookies + any JS signing hooks if present)
    try:
        result = page.evaluate(
            """async (url) => {
                const r = await fetch(url, {
                    credentials: 'include',
                    headers: {
                        'Accept': 'application/json',
                        'x-api-source': 'pc',
                        'x-shopee-language': 'pt',
                        'x-requested-with': 'XMLHttpRequest',
                    }
                });
                const t = await r.text();
                return {status: r.status, body_head: t.slice(0, 500)};
            }""",
            api_url,
        )
        print("in-page fetch:", json.dumps(result, ensure_ascii=False)[:600])
        if result.get("body_head"):
            try:
                j = json.loads(result["body_head"] if result["body_head"].endswith("}") else result["body_head"] + "...")
            except Exception:
                j = json.loads(result["body_head"].split("...")[0] if "..." in result.get("body_head","") else result["body_head"])
    except Exception as exc:
        print("in-page fetch err:", str(exc)[:200])
        result = None

    browser.close()
