# -*- coding: utf-8 -*-
"""Quick probe: does browser SPA still intercept search_items / recommend without
need_app_sign? If yes, listing-card harvest can still scale without get_pc."""
from __future__ import annotations
import json, sys, tempfile, time
from pathlib import Path
ROOT = Path(r"D:\Great Scraper\Great Scraper")
sys.path[:0] = [str(ROOT), str(ROOT/"shopee_getpc_crawler"), str(ROOT/"client 4 api integration")]
from _br_probe import BR_TEST, parse_br_test
from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies
import captcha_mint as mint, proxy_pool

STRIP = ("AC_CERT_D","AC_CERT_S","AC_CERT_A","SPC_U","SPC_ST","SPC_EC")

def main():
    if sys.platform=="win32":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except: pass
    ck,proxies,_=parse_br_test(BR_TEST)
    (ROOT/"proxy(BR shopee).txt").write_text("\n".join(proxies)+"\n",encoding="utf-8")
    proxy_pool.set_region("BR"); proxy_pool._POOL=[]; proxy_pool.load()
    jar=normalize_br_cookies(ck, fresh_cert=False)
    for k in STRIP: jar.pop(k, None)
    proxy,_,_=mint._proxy_dict("BR")
    from patchright.sync_api import sync_playwright
    seen=[]
    def on_resp(r):
        u=r.url
        if any(x in u for x in ("search_items","recommend","rcmd_items","get_pc","get_rw","item/get")):
            try: j=json.loads(r.text()); err=j.get("error"); d=j.get("data")
            except: err="parse"; d=None
            n=None
            if isinstance(d,dict):
                for k in ("items","item","sections"):
                    if k in d and hasattr(d[k],"__len__"): n=f"{k}:{len(d[k])}"; break
            seen.append({"url":u.split("?")[0][-60:], "err":err, "n":n, "signed":bool((r.request.headers or {}).get("x-sap-sec"))})
            print(f"  API err={err} signed={seen[-1]['signed']} n={n} {seen[-1]['url']}", flush=True)
    p=sync_playwright().start()
    ctx_kw=br_context_kwargs()
    ctx=p.chromium.launch_persistent_context(
        tempfile.mkdtemp(prefix="brlist_"), channel="chrome", headless=False, proxy=proxy,
        viewport={"width":1280,"height":800}, locale=ctx_kw["locale"], timezone_id=ctx_kw["timezone_id"],
        extra_http_headers=ctx_kw["extra_http_headers"],
        args=["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled"])
    ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
    page=ctx.new_page(); page.on("response", on_resp)
    print("goto home...", flush=True)
    try: page.goto("https://shopee.com.br/", wait_until="commit", timeout=60000)
    except: pass
    time.sleep(8)
    print("goto search...", flush=True)
    try: page.goto("https://shopee.com.br/search?keyword=fone", wait_until="commit", timeout=60000)
    except: pass
    time.sleep(18)
    print("goto product...", flush=True)
    try: page.goto("https://shopee.com.br/product/395237078/22498391636", wait_until="commit", timeout=60000)
    except: pass
    time.sleep(20)
    print(f"FINAL url={page.url[:80]} apis={len(seen)}", flush=True)
    for s in seen: print(s, flush=True)
    try: ctx.close(); p.stop()
    except: pass
    ok=[s for s in seen if s["err"] in (0,None) and s["n"]]
    print(f"RESULT ok_apis={len(ok)}/{len(seen)}", flush=True)
    return 0 if ok else 3

if __name__=="__main__":
    raise SystemExit(main())
