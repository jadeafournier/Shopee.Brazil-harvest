# -*- coding: utf-8 -*-
"""결정적 실험: 로그인 쿠키 0개(게스트)로 BR get_pc 가 완전 데이터를 주는가?

브라우저가 스스로 만든 게스트 세션(SPC_F/csrftoken/AC_CERT 등, 로그인 SPC_U 없음)
으로 PDP 를 열고 페이지가 쏘는 get_pc 응답을 가로챈다. error==0 + 완전 data 면
'무쿠키 경로'가 성립 → 로그인 계정을 태울 일이 없다.
"""
from __future__ import annotations

import json
import sys
import time
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from br_cookies import br_context_kwargs  # noqa: E402
import br_human as human  # noqa: E402
import captcha_mint as mint  # noqa: E402

DOM = "shopee.com.br"
BASE = f"https://{DOM}"


def _log(m): print(m, flush=True)


def probe(urls: list[str], *, use_proxy: bool = True) -> None:
    from patchright.sync_api import sync_playwright

    proxy = None
    if use_proxy:
        proxy, _, _ = mint._proxy_dict("BR")
        _log(f"proxy={'yes' if proxy else 'none'}")

    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="brguest_")
    ck = br_context_kwargs()
    ctx = p.chromium.launch_persistent_context(
        udd, channel="chrome", headless=False, proxy=proxy,
        viewport={"width": mint.VIEW_W, "height": mint.VIEW_H},
        locale=ck["locale"], timezone_id=ck["timezone_id"],
        extra_http_headers=ck["extra_http_headers"],
        args=["--no-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"],
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    )
    # NOTE: no add_cookies — pure guest.
    page = ctx.new_page()

    holder = {}

    def on_resp(resp):
        try:
            if "get_pc" in resp.url and resp.status == 200:
                holder["url"] = resp.url
                holder["body"] = resp.text()
        except Exception:
            pass

    page.on("response", on_resp)

    _log("게스트 세션 — 홈 워밍업(브라우저가 게스트 쿠키/서명 자체 생성)…")
    human.warm_home(page, DOM)
    try:
        page.wait_for_timeout(3500)
    except Exception:
        time.sleep(3)
    # 게스트 쿠키 확인
    guest_cookies = {c["name"]: c["value"] for c in ctx.cookies()}
    _log(f"게스트 쿠키 키: {sorted(guest_cookies.keys())}")
    _log(f"  SPC_U(로그인)={'있음!' if guest_cookies.get('SPC_U') else '없음(정상: 게스트)'}")

    for i, url in enumerate(urls):
        holder.clear()
        _log(f"\n[{i+1}] {url}")
        try:
            page.goto(url, wait_until="commit", timeout=45000)
        except Exception as e:
            _log(f"  goto 예외: {e}")
        deadline = time.time() + 35
        while time.time() < deadline and "body" not in holder:
            cur = page.url or ""
            if "/verify/captcha" in cur:
                _log("  → /verify/captcha (게스트 세션 캡차 — 폐기 후 새 세션)")
                break
            try:
                page.wait_for_timeout(300)
            except Exception:
                time.sleep(0.3)
        body = holder.get("body")
        if not body:
            _log(f"  get_pc 미수신 (현재 URL: {page.url})")
            continue
        try:
            payload = json.loads(body)
        except Exception:
            _log("  get_pc 비JSON")
            continue
        err = payload.get("error")
        data = payload.get("data") or {}
        item = data.get("item") or {}
        _log(f"  error={err} data_keys={len(data)} item_keys={len(item)} "
             f"title={str(item.get('title') or '')[:40]!r}")
        if err in (0, None) and item.get("item_id"):
            _log("  ✅ 게스트 무쿠키 get_pc 성공 — 완전 구조 수신")

    try:
        ctx.close(); p.stop()
    except Exception:
        pass


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    url_file = Path(__file__).resolve().parent / "br_active_product_urls_100k.txt"
    urls = [l.strip() for l in url_file.read_text(encoding="utf-8").splitlines() if l.strip()][:4]
    probe(urls, use_proxy=True)
