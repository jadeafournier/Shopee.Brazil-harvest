# -*- coding: utf-8 -*-
"""결정적: get_pc 를 여는 것이 로그인(SPC_U)인가 기기 인증서(AC_CERT_D)인가?

동일 상품을 3가지 쿠키 구성으로 시도:
  A) 전체 jar (BR TEST)                     — 현재 차단 여부 확인
  B) jar 에서 SPC_U(로그인) 제거            — 기기 인증서만으로 되는가
  C) AC_CERT_D + SPC_F + csrftoke 최소 구성 — 기기 인증서 핵심 검증
"""
from __future__ import annotations
import json, sys, time, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))
from _br_probe import BR_TEST, parse_br_test
from br_cookies import br_context_kwargs, pw_cookie_list, enforce_br_locale, normalize_br_cookies
import br_human as human, captcha_mint as mint

DOM = "shopee.com.br"
URL = "https://shopee.com.br/product/395237078/22498391636"
IID = 22498391636


def _log(m): print(m, flush=True)


def try_jar(p, proxy, jar: dict, label: str):
    udd = tempfile.mkdtemp(prefix="brcert_")
    ck = br_context_kwargs()
    ctx = p.chromium.launch_persistent_context(
        udd, channel="chrome", headless=False, proxy=proxy,
        viewport={"width": mint.VIEW_W, "height": mint.VIEW_H},
        locale=ck["locale"], timezone_id=ck["timezone_id"],
        extra_http_headers=ck["extra_http_headers"],
        args=["--no-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"])
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    if jar:
        ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
    page = ctx.new_page()
    holder = {}
    def on_resp(r):
        try:
            if "get_pc" in r.url and r.status == 200:
                holder["body"] = r.text()
        except Exception: pass
    page.on("response", on_resp)
    human.warm_home(page, DOM)
    try: page.wait_for_timeout(2500)
    except Exception: time.sleep(2.5)
    try: page.goto(URL, wait_until="commit", timeout=45000)
    except Exception: pass
    deadline = time.time() + 30
    while time.time() < deadline and "body" not in holder:
        if "/verify/captcha" in (page.url or ""):
            break
        try: page.wait_for_timeout(300)
        except Exception: time.sleep(0.3)
    err = "no_getpc"; keys = 0
    if "body" in holder:
        try:
            pl = json.loads(holder["body"]); err = pl.get("error")
            keys = len((pl.get("data") or {}).get("item") or {})
        except Exception: err = "nonjson"
    _log(f"[{label}] cur={page.url[:40]} get_pc.error={err} item_keys={keys} "
         f"→ {'✅ 성공' if (err in (0, None) and keys) else '차단/실패'}")
    try: ctx.close()
    except Exception: pass


def main():
    cookies, proxies, _ = parse_br_test(BR_TEST)
    cookies = enforce_br_locale(normalize_br_cookies(cookies, fresh_cert=False))
    _log(f"jar SPC_U={cookies.get('SPC_U')} AC_CERT_D={'yes' if cookies.get('AC_CERT_D') else 'no'}")
    from patchright.sync_api import sync_playwright
    proxy, _, _ = mint._proxy_dict("BR")
    with sync_playwright() as p:
        try_jar(p, proxy, dict(cookies), "A 전체jar")
        b = {k: v for k, v in cookies.items() if k != "SPC_U"}
        try_jar(p, proxy, b, "B SPC_U제거")
        keep = {k: cookies[k] for k in ("AC_CERT_D", "SPC_F", "csrftoken", "SPC_SI", "SPC_CLIENTID")
                if cookies.get(k)}
        try_jar(p, proxy, keep, "C 인증서최소")


if __name__ == "__main__":
    if sys.platform == "win32":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    main()
