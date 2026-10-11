# -*- coding: utf-8 -*-
"""PDP 로드 시 함께 발화하는 추천/연관 상품 API 를 탐지하고 item id 를 추출한다.
검색 표면(차단됨)을 쓰지 않고 저위험 PDP 채널만으로 URL 프런티어를 확장하기 위함."""
from __future__ import annotations
import json, re, sys, time, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))
from _br_probe import BR_TEST, parse_br_test
from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies, enforce_br_locale
import br_human as human, captcha_mint as mint

DOM = "shopee.com.br"
URL = "https://shopee.com.br/product/1006215031/24076819963"


def _log(m): print(m, flush=True)

SHOP = re.compile(r'"shop_?id"\s*:\s*(\d+)')
ITEM = re.compile(r'"item_?id"\s*:\s*(\d+)')


def _count_ids(body: str):
    shops = SHOP.findall(body)
    items = ITEM.findall(body)
    return len(set(items)), len(set(shops))


def main():
    ck, _, _ = parse_br_test(BR_TEST)
    ck = enforce_br_locale(normalize_br_cookies(ck, fresh_cert=False))
    ck = {k: v for k, v in ck.items() if k != "SPC_U"}
    from patchright.sync_api import sync_playwright
    proxy, _, _ = mint._proxy_dict("BR")
    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="brrcmd_")
    cx = br_context_kwargs()
    ctx = p.chromium.launch_persistent_context(
        udd, channel="chrome", headless=False, proxy=proxy,
        viewport={"width": mint.VIEW_W, "height": mint.VIEW_H},
        locale=cx["locale"], timezone_id=cx["timezone_id"],
        extra_http_headers=cx["extra_http_headers"],
        args=["--no-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"])
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    ctx.add_cookies(pw_cookie_list(ck, fresh_cert=False))
    page = ctx.new_page()
    hits = {}

    def on_resp(r):
        try:
            u = r.url
            if "/api/" not in u or r.status != 200:
                return
            ct = (r.headers or {}).get("content-type", "")
            if "json" not in ct:
                return
            body = r.text()
            ni, ns = _count_ids(body)
            key = u.split("?")[0].split("/api/")[-1]
            prev = hits.get(key, (0, 0))
            hits[key] = (max(prev[0], ni), max(prev[1], ns))
        except Exception:
            pass

    page.on("response", on_resp)
    human.warm_home(page, DOM)
    try: page.wait_for_timeout(2500)
    except Exception: time.sleep(2.5)
    try: page.goto(URL, wait_until="commit", timeout=45000)
    except Exception: pass
    # PDP 하단까지 스크롤하여 추천 캐러셀 lazy 발화 유도
    for _ in range(8):
        try:
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(1500)
        except Exception:
            time.sleep(1.5)
    try: page.wait_for_timeout(4000)
    except Exception: time.sleep(4)
    _log(f"현재 URL: {page.url}")
    _log(f"발화한 JSON /api/ 엔드포인트 {len(hits)}개 (item수, shop수):")
    for k, (ni, ns) in sorted(hits.items(), key=lambda kv: -kv[1][0]):
        _log(f"  {k}: items~{ni} shops~{ns}")
    try: ctx.close(); p.stop()
    except Exception: pass


if __name__ == "__main__":
    if sys.platform == "win32":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    main()
