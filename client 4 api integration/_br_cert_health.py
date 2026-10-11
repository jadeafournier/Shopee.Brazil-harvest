# -*- coding: utf-8 -*-
"""단일 요청 인증서 건강검진 — 로그인 제거 + 단일 IP. get_pc OK 면 exit 0, 차단이면 exit 3."""
from __future__ import annotations
import json, sys, time, tempfile
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


def main() -> int:
    ck, _, _ = parse_br_test(BR_TEST)
    ck = enforce_br_locale(normalize_br_cookies(ck, fresh_cert=False))
    ck = {k: v for k, v in ck.items() if k != "SPC_U" and v}
    from patchright.sync_api import sync_playwright
    proxy, _, _ = mint._proxy_dict("BR")
    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="brhealth_")
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
    status = "no_getpc"
    while time.time() < deadline and "body" not in holder:
        u = page.url or ""
        if "/verify/traffic/error" in u: status = "traffic_error"; break
        if "/verify/captcha" in u: status = "captcha"; break
        try: page.wait_for_timeout(400)
        except Exception: time.sleep(0.4)
    keys = 0; err = None
    if "body" in holder:
        try:
            pl = json.loads(holder["body"]); err = pl.get("error")
            keys = len((pl.get("data") or {}).get("item") or {})
        except Exception: pass
    healthy = keys > 0 and err in (0, None)
    _log(f"HEALTH: {'OK' if healthy else 'BLOCKED'} (get_pc.error={err} item_keys={keys} status={status} url={page.url[:40]})")
    try: ctx.close(); p.stop()
    except Exception: pass
    return 0 if healthy else 3


if __name__ == "__main__":
    if sys.platform == "win32":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    raise SystemExit(main())
