# -*- coding: utf-8 -*-
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_probe import parse_br_test, parse_url_ids, BR_TEST
from br_cookies import BR_TIMEZONE, normalize_br_cookies, pw_cookie_list
import captcha_mint as mint

cookies, _, urls = parse_br_test(BR_TEST)
work = normalize_br_cookies(cookies, fresh_cert=False)
sid, iid = parse_url_ids(urls[0])

from patchright.sync_api import sync_playwright

proxy, _, _ = mint._proxy_dict("BR")
hits = []

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        tempfile.mkdtemp(prefix="t_"),
        channel="chrome",
        headless=False,
        proxy=proxy,
        viewport={"width": 1280, "height": 800},
        locale="pt-BR",
        timezone_id=BR_TIMEZONE,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    )
    ctx.add_cookies(pw_cookie_list(work, fresh_cert=False))
    page = ctx.new_page()

    def on(r):
        if "/api/v4/pdp/get_pc" not in r.url:
            return
        try:
            j = json.loads(r.text())
            tz = None
            if "tz_offset_in_minutes=" in r.url:
                tz = r.url.split("tz_offset_in_minutes=")[1].split("&")[0]
            hits.append({"err": j.get("error"), "tz": tz, "signed": "x-sap-sec" in (r.request.headers or {})})
        except Exception as exc:
            hits.append({"parse": str(exc)[:60]})

    page.on("response", on)
    page.goto("https://shopee.com.br/", wait_until="load", timeout=60000)
    page.wait_for_timeout(3000)
    page.goto(f"https://shopee.com.br/product/{sid}/{iid}", wait_until="load", timeout=90000)
    for _ in range(40):
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(500)
    print("url:", (page.url or "")[:90])
    print("hits:", json.dumps(hits, indent=2))
    ctx.close()
