# -*- coding: utf-8 -*-
"""로그인 제거(SPC_U 없음) + 기기 인증서로 검색 표면이 동작하는지 검증.
검색은 캡차가 걸리는 표면이었다. 계정 연동이 원인이었다면 무로그인에서 완화될 수 있다."""
from __future__ import annotations
import re, sys, time, tempfile
from urllib.parse import quote
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))
from _br_probe import BR_TEST, parse_br_test
from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies, enforce_br_locale
import br_human as human, captcha_mint as mint

DOM = "shopee.com.br"
KWS = ["fone bluetooth", "camiseta masculina", "tenis feminino"]
ITEM = re.compile(r'"item_?id"\s*:\s*(\d+)')


def _log(m): print(m, flush=True)


def main():
    ck, _, _ = parse_br_test(BR_TEST)
    ck = enforce_br_locale(normalize_br_cookies(ck, fresh_cert=False))
    ck = {k: v for k, v in ck.items() if k != "SPC_U"}
    from patchright.sync_api import sync_playwright
    proxy, _, _ = mint._proxy_dict("BR")
    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="brsr_")
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
    stat = {"search_items": 0, "items": set(), "traffic": 0, "captcha": 0}

    def on_resp(r):
        try:
            u = r.url
            if "search_items" in u and r.status == 200:
                stat["search_items"] += 1
                for m in ITEM.findall(r.text()):
                    stat["items"].add(m)
            if "/verify/traffic" in u:
                stat["traffic"] += 1
            if "/verify/captcha" in u:
                stat["captcha"] += 1
        except Exception:
            pass

    page.on("response", on_resp)
    human.warm_home(page, DOM)
    try: page.wait_for_timeout(3000)
    except Exception: time.sleep(3)
    for kw in KWS:
        url = f"https://{DOM}/search?keyword={quote(kw)}"
        _log(f"검색: {kw}")
        try: page.goto(url, wait_until="commit", timeout=45000)
        except Exception: pass
        deadline = time.time() + 40
        base = len(stat["items"])
        while time.time() < deadline:
            cur = page.url or ""
            if "/verify/captcha" in cur:
                _log("  → CAPTCHA (검색 표면 차단)"); break
            if len(stat["items"]) > base + 5:
                break
            try: page.mouse.wheel(0, 1200); page.wait_for_timeout(1200)
            except Exception: time.sleep(1.2)
        _log(f"  현재 URL: {page.url[:50]}")
        _log(f"  누적 search_items 응답={stat['search_items']} 고유 item={len(stat['items'])} "
             f"traffic={stat['traffic']} captcha={stat['captcha']}")
    _log(f"\n결론: 무로그인 검색 고유 item={len(stat['items'])} "
         f"{'✅ 검색 동작' if len(stat['items'])>5 else '❌ 검색 차단/무응답'}")
    try: ctx.close(); p.stop()
    except Exception: pass


if __name__ == "__main__":
    if sys.platform == "win32":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    main()
