# -*- coding: utf-8 -*-
"""TW loginless get_pc — DIRECT (no proxy) + optional sticky IP retries.

Earlier SPA with proxy reached signed get_pc but 90309999.
Many residential exits now hard-fail on /verify/traffic/error before PDP.
This probe tries:
  1) no proxy (machine egress)
  2) grass mobile TW
  3) a few fresh proxy001 sticky
and never aborts solely on traffic/error — still navigates PDP and intercepts get_pc.
"""
from __future__ import annotations

import json
import os
import random
import string
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(r"D:\Great Scraper\Great Scraper")
HERE = ROOT / "client 4 api integration"
CRAWLER = ROOT / "shopee_getpc_crawler"
sys.path[:0] = [str(CRAWLER), str(HERE), str(ROOT)]

OUT = ROOT / "tw_deliverable_getpc"
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "guest_direct.log"
KEY = HERE / ".capsolver_key.local"
MIN_KEYS = 40
CANARY = "https://shopee.tw/product/3109018/51908008002"
ALSO = [
    "https://shopee.tw/product/3892560/182787875",
    "https://shopee.tw/product/72724/29750673165",
]


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def sid(n=8):
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def proxy_specs() -> list[dict | None]:
    """Playwright proxy dicts or None for direct."""
    out: list[dict | None] = [None]  # direct first
    # grass mobile TW
    out.append(
        {
            "server": "http://a9338eef79b42a4e.fkz.as.grassdata.net:2333",
            "username": "grass_sanzang-zone-mob-region-tw",
            "password": "qwe123",
        }
    )
    # grass custom TW
    out.append(
        {
            "server": "http://aproxy.grassdata.net:2333",
            "username": f"grass_sanzang-zone-custom-region-TW-session-{sid(9)}-sessTime-15",
            "password": "qwe123",
        }
    )
    # proxy001
    for _ in range(4):
        out.append(
            {
                "server": "http://us.proxy001.com:7878",
                "username": f"ojxsll758984_custom_zone_TW_sid_{sid(8)}_time_15",
                "password": "pwd893517",
            }
        )
    # 1024 sticky
    for _ in range(3):
        out.append(
            {
                "server": "http://us.1024proxy.io:3000",
                "username": f"gdxc12191-region-TW-sid-{sid()}-t-15",
                "password": "qadnrubc",
            }
        )
    return out


def save_full(j: dict, url: str) -> Path | None:
    data = j.get("data")
    if not isinstance(data, dict) or j.get("error") not in (0, None):
        return None
    item = data.get("item") or {}
    if not isinstance(item, dict) or len(item) < MIN_KEYS:
        return None
    shop = str(item.get("shop_id") or item.get("shopid") or "")
    it = str(item.get("item_id") or item.get("itemid") or "")
    if not shop or not it:
        return None
    path = OUT / f"output_{shop}_{it}.json"
    path.write_text(
        json.dumps(
            {
                "task_link": url or f"https://shopee.tw/product/{shop}/{it}",
                "token": "tw_guest_direct",
                "api_key": "tw_guest_direct",
                "bff_meta": {"extract_method": "browser_get_pc_loginless"},
                "error": None,
                "error_msg": None,
                "data": data,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def run_one(p, proxy, tag: str) -> dict:
    import captcha_mint as mint

    udd = tempfile.mkdtemp(prefix=f"twd_{tag}_")
    kw = dict(
        user_data_dir=udd,
        channel="chrome",
        headless=False,
        viewport={"width": 1366, "height": 800},
        locale="zh-TW",
        timezone_id="Asia/Taipei",
        extra_http_headers={"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
    )
    if proxy:
        kw["proxy"] = proxy
    ctx = p.chromium.launch_persistent_context(**kw)
    hits = []
    saved = None
    try:
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()

        def on_resp(r):
            nonlocal saved
            try:
                if "/api/v4/pdp/get_pc" not in r.url:
                    return
                j = r.json()
                item = ((j.get("data") or {}).get("item") if isinstance(j.get("data"), dict) else {}) or {}
                err = j.get("error")
                keys = len(item) if isinstance(item, dict) else 0
                signed = bool((r.request.headers or {}).get("x-sap-sec"))
                hits.append({"err": err, "keys": keys, "signed": signed, "http": r.status})
                log(f"  [{tag}] get_pc http={r.status} err={err} keys={keys} signed={signed}")
                if err in (0, None) and keys >= MIN_KEYS:
                    path = save_full(j, page.url)
                    if path:
                        saved = str(path)
                        # dump raw
                        (OUT / "_direct_ok_raw.json").write_text(
                            json.dumps(j, ensure_ascii=False)[:900000], encoding="utf-8"
                        )
            except Exception:
                pass

        page.on("response", on_resp)
        log(f"[{tag}] home proxy={proxy}")
        try:
            page.goto("https://shopee.tw/", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"  [{tag}] home_exc {type(e).__name__}: {e}")
        page.wait_for_timeout(4000)
        log(f"  [{tag}] url={page.url[:100]}")

        if "/verify/traffic" in page.url and "/error" not in page.url:
            try:
                mint._wait_traffic_pass(page, "shopee.tw", secs=22)
            except Exception as e:
                log(f"  [{tag}] traffic {e}")

        if "/verify/captcha" in page.url:
            try:
                import captcha_playwright as cpw

                sol = cpw.solve_captcha_page(page, dom="shopee.tw", gen_flag=None)
                log(f"  [{tag}] captcha {sol.get('ok')}")
            except Exception as e:
                log(f"  [{tag}] captcha_exc {e}")

        for url in [CANARY] + ALSO:
            if saved:
                break
            log(f"  [{tag}] goto {url[-50:]}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log(f"  [{tag}] goto_exc {type(e).__name__}")
            for _ in range(8):
                if saved:
                    break
                try:
                    page.mouse.wheel(0, 600)
                except Exception:
                    pass
                u = page.url or ""
                if "/verify/captcha" in u:
                    try:
                        import captcha_playwright as cpw

                        cpw.solve_captcha_page(page, dom="shopee.tw", gen_flag=None)
                    except Exception as e:
                        log(f"  [{tag}] cap {e}")
                elif "/verify/traffic" in u and "/error" not in u:
                    try:
                        mint._wait_traffic_pass(page, "shopee.tw", secs=15)
                    except Exception:
                        pass
                page.wait_for_timeout(1500)
            # reload once if blocked
            if not saved and any(h.get("err") == 90309999 for h in hits[-2:]):
                try:
                    page.reload(wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    pass
                page.wait_for_timeout(8000)

        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        return {
            "tag": tag,
            "ok": bool(saved),
            "saved": saved,
            "hits": hits,
            "url": (page.url or "")[:120],
            "cert": bool(ck.get("AC_CERT_D")),
            "cookie_n": len(ck),
        }
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if KEY.is_file() and not os.environ.get("CAPSOLVER_API_KEY"):
        os.environ["CAPSOLVER_API_KEY"] = KEY.read_text(encoding="utf-8").strip()

    from patchright.sync_api import sync_playwright

    specs = proxy_specs()
    log(f"DIRECT+proxy hunt n={len(specs)} cap={'Y' if os.environ.get('CAPSOLVER_API_KEY') else 'N'}")
    ok = 0
    with sync_playwright() as p:
        for i, px in enumerate(specs):
            tag = f"{i}_{'direct' if px is None else (px.get('server','').split('//')[-1].split(':')[0][:18])}"
            try:
                r = run_one(p, px, tag)
            except Exception as e:
                log(f"[{tag}] EXC {type(e).__name__}: {e}")
                continue
            log(f"[{tag}] RESULT ok={r.get('ok')} cert={r.get('cert')} hits={len(r.get('hits') or [])} saved={r.get('saved')}")
            (OUT / f"_direct_{tag}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
            if r.get("ok"):
                ok += 1
                break
    log(f"DONE ok={ok}")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
