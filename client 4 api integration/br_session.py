# -*- coding: utf-8 -*-
"""BR proxy preflight + warm patchright session — single browser lifetime."""
from __future__ import annotations

import json
import random
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_probe import parse_url_ids
from br_cookies import BR_TIMEZONE, normalize_br_cookies, pw_cookie_list
import captcha_mint as mint
import captcha_playwright as cpw
import proxy_pool

VIEW_W, VIEW_H = mint.VIEW_W, mint.VIEW_H
DOM = "shopee.com.br"


def _fetch_product_page(
    page, ctx, shop_id: int, item_id: int, gen: dict, *,
    timeout_s: float = 120,
    allow_captcha_solve: bool = True,
    log: Callable[[str], None] = print,
) -> tuple[Optional[dict], dict]:
    """Navigate to one product and capture get_pc. Returns (data, meta)."""
    holder: dict = {}
    captcha_tried = False

    def on_getpc(r):
        if "/api/v4/pdp/get_pc" in r.url and str(item_id) in r.url:
            try:
                j = json.loads(r.text())
                if j.get("error") in (0, None):
                    item = (j.get("data") or {}).get("item") or {}
                    if item.get("item_id"):
                        holder["data"] = j["data"]
            except Exception:
                pass

    page.on("response", on_getpc)
    page.goto(f"https://{DOM}/product/{shop_id}/{item_id}", wait_until="commit", timeout=60000)
    deadline = time.time() + timeout_s
    reloaded = 0
    while time.time() < deadline and not holder.get("data"):
        u = page.url or ""
        if "/verify/traffic" in u:
            cpw.wait_traffic_pass(page, DOM, secs=18)
        elif "/verify/captcha" in u and allow_captcha_solve and not captcha_tried:
            captcha_tried = True
            sol = cpw.solve_captcha_page(page, dom=DOM, gen_flag=gen)
            if not sol.get("ok"):
                log("  auto-solve failed — solve captcha in browser (90s)")
                cpw.wait_for_captcha_clear(page, 90)
        elif "/verify/captcha" in u:
            break
        elif "/product/" in u:
            if reloaded < 2 and time.time() > deadline - 80:
                reloaded += 1
                page.reload(wait_until="commit", timeout=60000)
                page.wait_for_timeout(1200)
            else:
                page.mouse.wheel(0, random.randint(200, 500))
                page.wait_for_timeout(500)
        else:
            page.wait_for_timeout(500)

    try:
        page.remove_listener("response", on_getpc)
    except Exception:
        pass

    ck = {c["name"]: c["value"] for c in ctx.cookies()}
    meta = {
        "has_data": bool(holder.get("data")),
        "final_url": (page.url or "")[:100],
        "ac_cert": bool(ck.get("AC_CERT_D")),
        "on_captcha": "/verify/captcha" in (page.url or ""),
    }
    return holder.get("data"), meta


def scan_and_extract(
    urls: list[str],
    cookies: dict,
    *,
    max_proxy_tries: int = 30,
    log: Callable[[str], None] = print,
) -> tuple[dict[str, dict], dict]:
    """Scan proxies; on first captcha-free get_pc, continue same session for all URLs."""
    from patchright.sync_api import sync_playwright

    proxy_pool.set_region("BR")
    proxy_pool.load()
    results: dict[str, dict] = {}
    meta: dict = {"proxies_tried": 0, "urls": {}}
    work = normalize_br_cookies(cookies)
    manual_captcha_used = False
    has_cert = bool(work.get("AC_CERT_D"))

    with sync_playwright() as p:
        for attempt in range(1, max_proxy_tries + 1):
            proxy, raw, _ = mint._proxy_dict("BR")
            host = (raw or "?").split("@")[-1].split(":")[0]
            meta["proxies_tried"] = attempt
            log(f"\n>>> Proxy {attempt}/{max_proxy_tries} host={host}")
            udd = tempfile.mkdtemp(prefix="brwarm_")
            ctx = None
            try:
                ctx = p.chromium.launch_persistent_context(
                    udd, channel="chrome", headless=False, proxy=proxy,
                    viewport={"width": VIEW_W, "height": VIEW_H}, locale="pt-BR",
                    timezone_id=BR_TIMEZONE,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                ctx.add_cookies(pw_cookie_list(work, fresh_cert=False))
                page = ctx.new_page()
                gen = {"ok": False}
                cpw.attach_puzzle_listener(page, gen)
                page.goto(f"https://{DOM}/", wait_until="commit", timeout=60000)
                page.wait_for_timeout(random.randint(2000, 3500))

                # Try each URL in this session until one succeeds or captcha blocks.
                session_ok = False
                for idx, url in enumerate(urls):
                    shop_id, item_id = parse_url_ids(url)
                    key = f"{shop_id}_{item_id}"
                    if key in results:
                        continue
                    log("=" * 60)
                    log(f"[{idx + 1}/{len(urls)}] i.{shop_id}.{item_id}")
                    data, umeta = _fetch_product_page(
                        page, ctx, shop_id, item_id, gen,
                        allow_captcha_solve=not manual_captcha_used, log=log,
                    )
                    if umeta.get("on_captcha") and not data:
                        manual_captcha_used = True
                    meta["urls"][key] = umeta
                    ck = {c["name"]: c["value"] for c in ctx.cookies()}
                    if ck.get("AC_CERT_D"):
                        work["AC_CERT_D"] = ck["AC_CERT_D"]
                    log(f"  {umeta}")
                    if data:
                        results[key] = data
                        session_ok = True
                    elif umeta.get("on_captcha"):
                        log("  captcha wall — rotating proxy")
                        break
                    page.wait_for_timeout(random.randint(800, 1500))

                if session_ok and len(results) == len(urls):
                    meta["clean_proxy"] = host
                    ctx.close()
                    return results, meta
                if session_ok:
                    # Partial success — keep trying remaining URLs on new proxies
                    remaining = [u for u in urls if f"{parse_url_ids(u)[0]}_{parse_url_ids(u)[1]}" not in results]
                    if not remaining:
                        meta["clean_proxy"] = host
                        ctx.close()
                        return results, meta
            except Exception as exc:
                log(f"  session err: {str(exc)[:80]}")
            finally:
                try:
                    if ctx is not None:
                        ctx.close()
                except Exception:
                    pass

    meta["clean_proxy"] = None
    return results, meta
