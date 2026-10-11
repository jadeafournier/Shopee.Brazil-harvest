# -*- coding: utf-8 -*-
"""게스트(무쿠키) 상품 HTML 에 데이터가 SSR 로 실려오는지 진단."""
from __future__ import annotations
import re, sys, time, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))
from br_cookies import br_context_kwargs
import br_human as human, captcha_mint as mint
from br_pdp_methods import parse_html_partial

DOM = "shopee.com.br"


def _log(m): print(m, flush=True)


def main():
    url = "https://shopee.com.br/product/395237078/22498391636"
    sid, iid = 395237078, 22498391636
    from patchright.sync_api import sync_playwright
    proxy, _, _ = mint._proxy_dict("BR")
    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="brhtml_")
    ck = br_context_kwargs()
    ctx = p.chromium.launch_persistent_context(
        udd, channel="chrome", headless=False, proxy=proxy,
        viewport={"width": mint.VIEW_W, "height": mint.VIEW_H},
        locale=ck["locale"], timezone_id=ck["timezone_id"],
        extra_http_headers=ck["extra_http_headers"],
        args=["--no-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"])
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    page = ctx.new_page()
    _log("게스트 홈 워밍업…")
    human.warm_home(page, DOM)
    try: page.wait_for_timeout(3000)
    except Exception: time.sleep(3)
    _log(f"홈 후 URL: {page.url}")
    try:
        page.goto(url, wait_until="commit", timeout=45000)
    except Exception as e:
        _log(f"goto: {e}")
    try: page.wait_for_timeout(8000)
    except Exception: time.sleep(8)
    _log(f"상품 후 URL: {page.url}")
    try:
        html = page.content()
    except Exception as e:
        html = ""; _log(f"content 예외: {e}")
    _log(f"HTML 길이: {len(html)}")
    # 임베디드 데이터 신호 탐색
    for marker in ("__INITIAL_STATE__", "application/ld+json", "og:title",
                   "productItem", "pdpTrackingData", "item_id", '"price"'):
        _log(f"  포함[{marker}]: {marker in html}")
    part = parse_html_partial(html, shop_id=sid, item_id=iid)
    _log(f"parse_html_partial → {part}")
    # 저장해 육안 확인
    Path("_guest_pdp_sample.html").write_text(html, encoding="utf-8")
    _log("HTML 저장: _guest_pdp_sample.html")
    try: ctx.close(); p.stop()
    except Exception: pass


if __name__ == "__main__":
    if sys.platform == "win32":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    main()
