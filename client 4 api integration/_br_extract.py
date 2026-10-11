# -*- coding: utf-8 -*-
"""BR get_pc via Playwright with verify/traffic pass (sign-oracle logic)."""
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
from sign_oracle import _pw_proxy, VERIFY_WAIT_S, GETPC_WAIT_S, NAV_TIMEOUT_MS
import proxy_pool

cookies, proxy_lines, urls = parse_br_test(BR_TEST)
proxy_pool.set_region("BR")
proxy_pool.load()

OUT = Path(__file__).resolve().parent / "br_probe_output"
OUT.mkdir(parents=True, exist_ok=True)


def fetch_one(url: str, cookies: dict, proxy: str) -> tuple[dict, dict | None]:
    shop_id, item_id = parse_url_ids(url)
    domain = "shopee.com.br"
    pdp = f"https://{domain}/product/{shop_id}/{item_id}"
    holder: dict = {}

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            proxy=_pw_proxy(proxy),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(
            locale="pt-BR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        ctx.add_cookies([
            {"name": k, "value": v, "domain": f".{domain}", "path": "/"}
            for k, v in cookies.items() if v
        ])
        page = ctx.new_page()

        def on_resp(resp):
            if "get_pc" in resp.url and str(item_id) in resp.url:
                try:
                    j = json.loads(resp.text())
                    if j.get("error") in (0, None) and (j.get("data") or {}).get("item", {}).get("item_id"):
                        holder["data"] = j["data"]
                        holder["ok"] = True
                    else:
                        holder["last_err"] = j.get("error")
                except Exception:
                    pass

        page.on("response", on_resp)

        # warmup home
        page.goto(f"https://{domain}/", wait_until="commit", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(random.randint(2000, 3500))
        try:
            page.mouse.move(random.randint(200, 900), random.randint(150, 500))
            page.mouse.wheel(0, random.randint(800, 2000))
        except Exception:
            pass
        page.wait_for_timeout(random.randint(800, 1500))

        # PDP
        try:
            page.goto(pdp, wait_until="commit", timeout=NAV_TIMEOUT_MS)
        except Exception:
            pass

        # verify/traffic pass loop (mirrors sign_oracle)
        deadline = time.time() + GETPC_WAIT_S + VERIFY_WAIT_S
        while time.time() < deadline and "data" not in holder:
            cur = page.url or ""
            if "/verify/captcha" in cur:
                holder["status"] = "captcha"
                break
            if "/verify/" in cur:
                try:
                    page.mouse.move(random.randint(150, 1100), random.randint(120, 650))
                    if random.random() < 0.4:
                        page.mouse.wheel(0, random.randint(200, 900))
                except Exception:
                    pass
                page.wait_for_timeout(random.randint(500, 1100))
            else:
                page.wait_for_timeout(200)

        meta = {
            "final_url": page.url,
            "title": (page.title() or "")[:80],
            "last_err": holder.get("last_err"),
            "status": holder.get("status") or ("ok" if holder.get("data") else "no_data"),
        }
        browser.close()
        return meta, holder.get("data")


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    saved = 0
    for url in urls:
        shop_id, item_id = parse_url_ids(url)
        print("=" * 70)
        print(f"i.{shop_id}.{item_id}")
        meta, data = None, None
        for attempt in range(3):
            proxy = proxy_pool.pick()
            print(f"  attempt {attempt + 1} ...")
            meta, data = fetch_one(url, cookies, proxy)
            print(f"  -> {meta}")
            if data:
                break

        if data:
            payload = {
                "task_link": url,
                "token": "br_client4_test",
                "api_key": "br_client4_test",
                "bff_meta": None,
                "error": None,
                "error_msg": None,
                "data": data,
            }
            path = OUT / f"output_{shop_id}_{item_id}.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print("  SAVED", path.name, summarize_data(data))
            saved += 1
        else:
            print("  FAILED")

    print(f"\nDone: {saved}/{len(urls)} saved")
    return 0 if saved == len(urls) else 1


if __name__ == "__main__":
    raise SystemExit(main())
