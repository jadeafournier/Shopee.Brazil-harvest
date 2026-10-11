# -*- coding: utf-8 -*-
"""Probe: can anonymous (no SPC_U) browser session still get full raw get_pc?"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(HERE))

from br_cookies import br_context_kwargs, pw_cookie_list, enforce_br_locale, normalize_br_cookies
from _br_probe import parse_br_test, BR_TEST, parse_url_ids
import br_human as human
import captcha_mint as mint
import proxy_pool

URLS = [
    "https://shopee.com.br/product/395237078/22498391636",
    "https://shopee.com.br/product/1673405419/58257432419",
]


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    from patchright.sync_api import sync_playwright

    cookies, proxies, _ = parse_br_test(BR_TEST)
    # Anonymous: strip ALL login identity; keep device/session scaffolding only.
    anon = {
        k: v
        for k, v in cookies.items()
        if k not in ("SPC_U", "SPC_EC", "SPC_ST") and v and v not in ("-", "deleted")
    }
    # Force remint cert on this fingerprint
    anon = enforce_br_locale(normalize_br_cookies(anon, fresh_cert=True))
    print("anon_keys", sorted(anon.keys()), flush=True)
    print("has_SPC_U", "SPC_U" in anon, "has_CERT", "AC_CERT_D" in anon, flush=True)

    if proxies:
        (ROOT / "proxy(BR shopee).txt").write_text("\n".join(proxies) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()
    proxy, _, sub = mint._proxy_dict("BR")
    print("proxy_sub", (sub or "")[-48:], flush=True)

    results = []
    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="anonprobe_")
    cx = br_context_kwargs()
    try:
        ctx = p.chromium.launch_persistent_context(
            udd,
            channel="chrome",
            headless=False,
            proxy=proxy,
            viewport={"width": 1366, "height": 768},
            locale=cx["locale"],
            timezone_id=cx["timezone_id"],
            extra_http_headers=cx["extra_http_headers"],
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        ctx.add_cookies(pw_cookie_list(anon, fresh_cert=True))
        page = ctx.new_page()
        hits = []

        def on_resp(r):
            try:
                if "/api/v4/pdp/get_pc" not in r.url:
                    return
                j = r.json()
                item = ((j.get("data") or {}).get("item") if isinstance(j.get("data"), dict) else {}) or {}
                hits.append({
                    "err": j.get("error"),
                    "is_login": j.get("is_login"),
                    "item_keys": len(item) if isinstance(item, dict) else 0,
                    "data_keys": len(j.get("data") or {}) if isinstance(j.get("data"), dict) else 0,
                    "price": item.get("price") if isinstance(item, dict) else None,
                    "models": len(item.get("models") or []) if isinstance(item, dict) else 0,
                })
                print("GET_PC", hits[-1], flush=True)
            except Exception as e:
                print("GET_PC parse", e, flush=True)

        page.on("response", on_resp)
        human.warm_home(page, "shopee.com.br")
        # wait for possible AC_CERT mint
        deadline = time.time() + 60
        while time.time() < deadline:
            ck = {c["name"]: c["value"] for c in ctx.cookies()}
            if ck.get("AC_CERT_D"):
                print("reminted AC_CERT_D", flush=True)
                break
            u = page.url or ""
            if "/verify/captcha" in u:
                print("HIT captcha on home", flush=True)
                break
            if "/verify/traffic/error" in u:
                print("HIT traffic_error on home", flush=True)
                break
            if "/verify/traffic" in u:
                mint._wait_traffic_pass(page, "shopee.com.br", secs=20)
            human.pause(page, 800, 1400)
        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        print("post_warm cookies", sorted(ck.keys()), "CERT", bool(ck.get("AC_CERT_D")), "U", ck.get("SPC_U"), flush=True)

        for url in URLS:
            hits.clear()
            sid, iid = parse_url_ids(url)
            print(f"\n=== PDP i.{sid}.{iid} ===", flush=True)
            try:
                page.goto(url, wait_until="commit", timeout=60000)
            except Exception as e:
                print("goto", e, flush=True)
            t0 = time.time()
            while time.time() - t0 < 35 and not hits:
                human.read_pdp(page)
            results.append({"url": url, "hits": list(hits), "page_url": page.url})
            print("result", results[-1], flush=True)

        out = HERE / "br_anon_raw_probe_result.json"
        out.write_text(json.dumps({
            "mode": "anonymous_no_SPC_U",
            "results": results,
            "final_cookies": sorted({c["name"] for c in ctx.cookies()}),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote", out, flush=True)
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
