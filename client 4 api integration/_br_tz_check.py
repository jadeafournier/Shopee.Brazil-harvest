# -*- coding: utf-8 -*-
"""Quick diagnostic: tz in passive get_pc + effect of AC_CERT strip vs keep."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_probe import BR_TEST, parse_br_test, parse_url_ids
from br_cookies import BR_TIMEZONE, normalize_br_cookies, pw_cookie_list
import captcha_mint as mint
import proxy_pool

cookies, _, urls = parse_br_test(BR_TEST)
shop_id, item_id = parse_url_ids(urls[0])
proxy_pool.set_region("BR")
proxy_pool.load()


def probe(*, fresh_cert: bool, headless: bool = True, timezone: str | None = BR_TIMEZONE) -> dict:
    from patchright.sync_api import sync_playwright

    work = normalize_br_cookies(cookies, fresh_cert=fresh_cert)
    proxy, _, _ = mint._proxy_dict("BR")
    hits = []
    with sync_playwright() as p:
        udd = tempfile.mkdtemp(prefix="brtz_")
        ctx = p.chromium.launch_persistent_context(
            udd, channel="chrome", headless=headless, proxy=proxy,
            viewport={"width": 1280, "height": 800}, locale="pt-BR",
            timezone_id=timezone,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx.add_cookies(pw_cookie_list(work, fresh_cert=False))
            page = ctx.new_page()

            def on_resp(r):
                if "/api/v4/pdp/get_pc" not in r.url:
                    return
                tz = None
                if "tz_offset_in_minutes=" in r.url:
                    try:
                        tz = int(r.url.split("tz_offset_in_minutes=")[1].split("&")[0])
                    except Exception:
                        pass
                try:
                    j = json.loads(r.text())
                    hits.append({"err": j.get("error"), "tz": tz})
                except Exception:
                    hits.append({"parse_err": True, "tz": tz})

            page.on("response", on_resp)
            page.goto(f"https://shopee.com.br/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            page.goto(
                f"https://shopee.com.br/product/{shop_id}/{item_id}",
                wait_until="load", timeout=60000,
            )
            for _ in range(30):
                if hits or "/verify/" in (page.url or ""):
                    break
                page.wait_for_timeout(500)
            ck = {c["name"]: c["value"] for c in ctx.cookies()}
            return {
                "fresh_cert": fresh_cert,
                "headless": headless,
                "timezone": timezone,
                "had_ac_cert_inject": bool(work.get("AC_CERT_D")),
                "final_url": (page.url or "")[:90],
                "ac_cert_now": bool(ck.get("AC_CERT_D")),
                "hits": hits,
            }
        finally:
            ctx.close()


if __name__ == "__main__":
    cases = [
        {"fresh_cert": True, "headless": True, "timezone": None},
        {"fresh_cert": True, "headless": True, "timezone": BR_TIMEZONE},
        {"fresh_cert": False, "headless": False, "timezone": BR_TIMEZONE},
    ]
    for kw in cases:
        print(json.dumps(probe(**kw), indent=2), flush=True)
        print("---", flush=True)
