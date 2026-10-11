# -*- coding: utf-8 -*-
"""TW guest SPA probe: intercept get_pc from page-native requests (no BR touch)."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(r"D:\Great Scraper\Great Scraper")
sys.path[:0] = [str(ROOT / "shopee_getpc_crawler"), str(ROOT)]

import captcha_mint as mint
import proxy_pool

OUT = ROOT / "tw_deliverable_getpc"
CANARY = "https://shopee.tw/product/3109018/51908008002"


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    OUT.mkdir(parents=True, exist_ok=True)
    proxy_pool.set_region("TW")
    proxy_pool._POOL = []
    # Prefer crawler proxies (already enlarged to TW residential)
    proxy_pool.load()
    proxy, raw, _ = mint._proxy_dict("TW")
    print(f"proxy={raw}", flush=True)

    from patchright.sync_api import sync_playwright

    hits = []

    def on_resp(r):
        u = r.url
        if "/api/v4/pdp/get_pc" not in u and "/api/v4/pdp/get_rw" not in u:
            return
        try:
            body = r.text()
            j = json.loads(body)
        except Exception as e:
            hits.append({"url": u[:120], "err": f"parse:{e}", "ok": False})
            print(f"  PARSE fail {u[:80]}", flush=True)
            return
        err = j.get("error")
        d = j.get("data") or {}
        item = d.get("item") or {}
        ok = err in (0, None) and bool(item.get("itemid") or item.get("item_id") or item.get("title"))
        rec = {
            "url": u.split("?")[0],
            "err": err,
            "ok": ok,
            "data_keys": len(d) if isinstance(d, dict) else 0,
            "item_keys": len(item) if isinstance(item, dict) else 0,
            "title": (item.get("title") or "")[:80],
            "signed": bool((r.request.headers or {}).get("x-sap-sec")),
            "http": r.status,
        }
        hits.append(rec)
        print(
            f"  get_pc http={rec['http']} err={err} ok={ok} "
            f"data={rec['data_keys']} item={rec['item_keys']} signed={rec['signed']} "
            f"title={rec['title']!r}",
            flush=True,
        )
        if ok:
            (OUT / "_spa_probe_ok.json").write_text(
                json.dumps(j, ensure_ascii=False)[:500000], encoding="utf-8"
            )

    p = sync_playwright().start()
    ctx = p.chromium.launch_persistent_context(
        tempfile.mkdtemp(prefix="twspa_"),
        channel="chrome",
        headless=False,
        proxy=proxy,
        viewport={"width": 1280, "height": 800},
        locale="zh-TW",
        timezone_id="Asia/Taipei",
        extra_http_headers={"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    page = ctx.new_page()
    page.on("response", on_resp)

    print("goto home...", flush=True)
    try:
        page.goto("https://shopee.tw/", wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"  home exc: {e}", flush=True)
    time.sleep(6)
    print(f"  url={page.url[:100]}", flush=True)

    print(f"goto canary {CANARY}...", flush=True)
    try:
        page.goto(CANARY, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"  pdp exc: {e}", flush=True)
    time.sleep(22)
    print(f"  url={page.url[:120]}", flush=True)

    # second product from seed list
    urls = (ROOT / "tw_product_urls.txt").read_text(encoding="utf-8").splitlines()
    if urls:
        u2 = urls[0].strip()
        print(f"goto seed {u2}...", flush=True)
        try:
            page.goto(u2, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"  seed exc: {e}", flush=True)
        time.sleep(18)
        print(f"  url={page.url[:120]}", flush=True)

    try:
        ctx.close()
        p.stop()
    except Exception:
        pass

    ok = [h for h in hits if h.get("ok")]
    (OUT / "_spa_probe_hits.json").write_text(
        json.dumps(hits, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"RESULT ok={len(ok)}/{len(hits)}", flush=True)
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
