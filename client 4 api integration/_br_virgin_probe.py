# -*- coding: utf-8 -*-
"""Virgin-browser multi-proxy probe: no cookies at all, networkidle, check
which SPA APIs still return data (search vs get_pc)."""
from __future__ import annotations
import json, sys, tempfile, time
from pathlib import Path
ROOT = Path(r"D:\Great Scraper\Great Scraper")
sys.path[:0] = [str(ROOT), str(ROOT/"shopee_getpc_crawler"), str(ROOT/"client 4 api integration")]
from _br_probe import BR_TEST, parse_br_test
from br_cookies import br_context_kwargs
import captcha_mint as mint, proxy_pool

def main():
    if sys.platform=="win32":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except: pass
    _,proxies,_=parse_br_test(BR_TEST)
    (ROOT/"proxy(BR shopee).txt").write_text("\n".join(proxies)+"\n",encoding="utf-8")
    proxy_pool.set_region("BR"); proxy_pool._POOL=[]; proxy_pool.load()
    from patchright.sync_api import sync_playwright
    results=[]
    for attempt in range(8):
        proxy,_,sub=mint._proxy_dict("BR")
        print(f"\n=== attempt {attempt+1} sub=...{(sub or '')[-36:]} ===", flush=True)
        apis=[]
        def on_resp(r):
            u=r.url
            if "/api/" not in u: return
            try: j=json.loads(r.text()); err=j.get("error"); d=j.get("data")
            except: return
            n=None
            if isinstance(d,dict):
                if "item" in d and isinstance(d["item"],dict): n=f"item_keys:{len(d['item'])}"
                elif "items" in d: n=f"items:{len(d['items']) if hasattr(d['items'],'__len__') else '?'}"
            kind="other"
            for k in ("get_pc","get_rw","search_items","recommend","rcmd_items","item/get"):
                if k in u: kind=k; break
            apis.append({"kind":kind,"err":err,"n":n,"signed":bool((r.request.headers or {}).get("x-sap-sec")),"status":r.status})
            if kind!="other" or err not in (0,None):
                print(f"  {kind} err={err} n={n} signed={apis[-1]['signed']}", flush=True)
        p=sync_playwright().start()
        kw=br_context_kwargs()
        ctx=p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix=f"virg{attempt}_"), channel="chrome", headless=True, proxy=proxy,
            viewport={"width":1280,"height":800}, locale=kw["locale"], timezone_id=kw["timezone_id"],
            extra_http_headers=kw["extra_http_headers"],
            args=["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled","--headless=new"])
        # NO cookies injected — virgin
        page=ctx.new_page(); page.on("response", on_resp)
        try:
            page.goto("https://shopee.com.br/", wait_until="domcontentloaded", timeout=90000)
            time.sleep(5)
            page.goto("https://shopee.com.br/search?keyword=fone%20bluetooth", wait_until="networkidle", timeout=120000)
            time.sleep(8)
            page.goto("https://shopee.com.br/product/395237078/22498391636", wait_until="networkidle", timeout=120000)
            time.sleep(8)
        except Exception as e:
            print(f"  nav err {e}", flush=True)
        print(f"  final={page.url[:90]}", flush=True)
        ok=[a for a in apis if a["err"] in (0,None) and a["n"] and a["kind"] in ("get_pc","get_rw","search_items","recommend","rcmd_items","item/get")]
        results.append({"sub":sub,"ok":ok,"apis":apis,"url":page.url})
        try: ctx.close(); p.stop()
        except: pass
        if any(a["kind"]=="get_pc" and a["err"] in (0,None) and a["n"] for a in apis):
            print("GET_PC SUCCESS", flush=True)
            break
        if ok:
            print(f"  partial success kinds={[a['kind'] for a in ok]}", flush=True)
    print("\n==== SUMMARY ====", flush=True)
    for r in results:
        kinds={}
        for a in r["apis"]:
            if a["kind"]=="other": continue
            key=f"{a['kind']}:err={a['err']}"
            kinds[key]=kinds.get(key,0)+1
        print(f"  {(r['sub'] or '')[-30:]} -> {kinds} final_traffic={'traffic' in (r['url'] or '')}", flush=True)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
