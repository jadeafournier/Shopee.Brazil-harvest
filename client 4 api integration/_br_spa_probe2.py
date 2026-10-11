# -*- coding: utf-8 -*-
"""Deeper SPA probe: capture ALL responses, page state, and try mobile UA."""
from __future__ import annotations
import json, sys, tempfile, time
from pathlib import Path
ROOT = Path(r"D:\Great Scraper\Great Scraper")
sys.path[:0] = [str(ROOT), str(ROOT/"shopee_getpc_crawler"), str(ROOT/"client 4 api integration")]
from _br_probe import BR_TEST, parse_br_test
from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies
import captcha_mint as mint, proxy_pool

STRIP = ("AC_CERT_D","AC_CERT_S","AC_CERT_A","SPC_U","SPC_ST","SPC_EC")

def run(mode="desktop"):
    ck,proxies,_=parse_br_test(BR_TEST)
    (ROOT/"proxy(BR shopee).txt").write_text("\n".join(proxies)+"\n",encoding="utf-8")
    proxy_pool.set_region("BR"); proxy_pool._POOL=[]; proxy_pool.load()
    jar=normalize_br_cookies(ck, fresh_cert=False)
    for k in STRIP: jar.pop(k, None)
    proxy,_,sub=mint._proxy_dict("BR")
    print(f"MODE={mode} sub=...{(sub or '')[-40:]}", flush=True)
    from patchright.sync_api import sync_playwright
    apis=[]; all_api=[]
    def on_resp(r):
        u=r.url
        if "/api/" in u or "shopee.com.br" in u and any(x in u for x in ("get_pc","search","recommend","item")):
            st=r.status
            err=None; n=None; signed=bool((r.request.headers or {}).get("x-sap-sec"))
            try:
                if "json" in (r.headers.get("content-type") or ""):
                    j=json.loads(r.text()); err=j.get("error"); d=j.get("data")
                    if isinstance(d,dict):
                        for k in ("items","item","sections","item_cards"):
                            if k in d and hasattr(d[k],"__len__"): n=f"{k}:{len(d[k])}"; break
            except Exception as e:
                err=f"parse:{e}"
            rec={"status":st,"err":err,"n":n,"signed":signed,"url":u[:140]}
            all_api.append(rec)
            if "/api/" in u:
                apis.append(rec)
                print(f"  API {st} err={err} signed={signed} n={n} {u.split('?')[0][-70:]}", flush=True)
    p=sync_playwright().start()
    ctx_kw=br_context_kwargs()
    kwargs=dict(
        user_data_dir=tempfile.mkdtemp(prefix=f"br{mode}_"), channel="chrome", headless=False, proxy=proxy,
        viewport={"width":390,"height":844} if mode=="mobile" else {"width":1280,"height":800},
        locale=ctx_kw["locale"], timezone_id=ctx_kw["timezone_id"],
        extra_http_headers=ctx_kw["extra_http_headers"],
        args=["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled"],
    )
    if mode=="mobile":
        kwargs["user_agent"]="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        kwargs["is_mobile"]=True
        kwargs["has_touch"]=True
    ctx=p.chromium.launch_persistent_context(**kwargs)
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
    page=ctx.new_page(); page.on("response", on_resp)
    for label,url,wait in [
        ("home","https://shopee.com.br/",10),
        ("search","https://shopee.com.br/search?keyword=fone%20bluetooth",20),
        ("pdp","https://shopee.com.br/product/395237078/22498391636",25),
    ]:
        print(f"goto {label}...", flush=True)
        try: page.goto(url, wait_until="domcontentloaded", timeout=90000)
        except Exception as e: print(f"  nav: {e}", flush=True)
        time.sleep(wait)
        print(f"  now url={page.url[:90]} title={(page.title() or '')[:60]!r}", flush=True)
    # dump a snippet of body text
    try:
        txt=page.inner_text("body")[:400]
        print(f"BODY: {txt!r}", flush=True)
    except Exception as e:
        print(f"BODY err {e}", flush=True)
    page.screenshot(path=str(ROOT/"client 4 api integration"/f"_spa_{mode}.png"))
    try: ctx.close(); p.stop()
    except: pass
    ok=[a for a in apis if a["err"] in (0,None) and a["n"]]
    print(f"RESULT mode={mode} apis={len(apis)} ok={len(ok)}", flush=True)
    return ok

def main():
    if sys.platform=="win32":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except: pass
    for mode in ("desktop","mobile"):
        ok=run(mode)
        if ok:
            print("SUCCESS path found", flush=True)
            return 0
    return 3

if __name__=="__main__":
    raise SystemExit(main())
