# -*- coding: utf-8 -*-
"""결정적 실험: 백업 계정을 '새 브라우저 지문'에 로그인시키면 그 지문 전용
AC_CERT_D(기기 인증서)가 새로 발급되는가?

성립하면 계정 8개 × 서로 다른 지문 = 기기 인증서 8개 → 각 인증서를 '한 IP에
고정된 단일 세션'으로 부드럽게 돌려 안전한 병렬화가 가능하다(단일 인증서 과열 회피).
"""
from __future__ import annotations
import json, sys, time, tempfile, random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))
from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies, enforce_br_locale
import br_human as human, captcha_mint as mint

DOM = "shopee.com.br"
SESS = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
# 현재 핫한 인증서를 만든 계정(1707407334) 이 아닌 백업 계정으로 시험.
CAND = ["br_1706801030_ydvUxsIK_live.json", "br_1706785017_CoOHjjSQ_live.json",
        "br_1706784820_OUk7hm0A_live.json"]

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]
URL = "https://shopee.com.br/product/1006215031/24076819963"
IID = 24076819963


def _log(m): print(m, flush=True)


def main():
    jar = None; name = None
    for c in CAND:
        p = SESS / c
        if p.is_file():
            doc = json.loads(p.read_text(encoding="utf-8"))
            ck = doc.get("cookies") or {}
            if ck.get("SPC_U"):
                jar = ck; name = c; break
    if not jar:
        _log("백업 계정 jar 없음"); return
    _log(f"사용 백업 계정: {name} (SPC_U={jar.get('SPC_U')})")
    # 새 지문 강제: 기존 기기 인증서 제거 → 이 지문 전용으로 재발급 유도
    for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
        jar.pop(k, None)
    jar = enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))

    from patchright.sync_api import sync_playwright
    proxy, _, _ = mint._proxy_dict("BR")
    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="brregen_")
    cx = br_context_kwargs()
    ua = random.choice(UAS)
    ctx = p.chromium.launch_persistent_context(
        udd, channel="chrome", headless=False, proxy=proxy,
        viewport={"width": 1366, "height": 768},  # 다른 해상도
        locale=cx["locale"], timezone_id=cx["timezone_id"],
        user_agent=ua,
        extra_http_headers=cx["extra_http_headers"],
        args=["--no-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"])
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
    page = ctx.new_page()
    holder = {}
    def on_resp(r):
        try:
            if "get_pc" in r.url and r.status == 200:
                holder["body"] = r.text()
        except Exception: pass
    page.on("response", on_resp)

    _log(f"새 지문(UA={ua[-15:]}) 홈 워밍업 + 트래픽 통과…")
    human.warm_home(page, DOM)
    # 트래픽 챌린지 있으면 통과
    for _ in range(4):
        if "/verify/traffic" in (page.url or ""):
            mint._wait_traffic_pass(page, DOM, secs=25)
        try: page.wait_for_timeout(2500)
        except Exception: time.sleep(2.5)
        if "/verify" not in (page.url or ""):
            break
    ck_after_home = {c["name"]: c["value"] for c in ctx.cookies()}
    _log(f"홈 후 URL={page.url[:45]}  AC_CERT_D 발급? {'YES' if ck_after_home.get('AC_CERT_D') else 'no'}")

    _log("상품 열기 → get_pc 확인…")
    try: page.goto(URL, wait_until="commit", timeout=45000)
    except Exception: pass
    deadline = time.time() + 35
    while time.time() < deadline and "body" not in holder:
        if "/verify/captcha" in (page.url or ""):
            _log("  캡차"); break
        if "/verify/traffic" in (page.url or ""):
            mint._wait_traffic_pass(page, DOM, secs=20)
        try: page.wait_for_timeout(400)
        except Exception: time.sleep(0.4)
    ck_final = {c["name"]: c["value"] for c in ctx.cookies()}
    new_cert = ck_final.get("AC_CERT_D", "")
    err = keys = None
    if "body" in holder:
        try:
            pl = json.loads(holder["body"]); err = pl.get("error")
            keys = len((pl.get("data") or {}).get("item") or {})
        except Exception: err = "nonjson"
    _log(f"결과: get_pc.error={err} item_keys={keys}  AC_CERT_D(new)={new_cert[:24]!r}")
    _log(f"판정: {'✅ 새 지문 전용 인증서 발급 + get_pc 성공' if (keys and err in (0,None)) else '❌ 실패/차단'}")
    try: ctx.close(); p.stop()
    except Exception: pass


if __name__ == "__main__":
    if sys.platform == "win32":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    main()
