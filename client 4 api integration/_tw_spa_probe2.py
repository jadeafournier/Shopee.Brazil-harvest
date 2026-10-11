# -*- coding: utf-8 -*-
"""TW SPA probe with optional login jar + verify/captcha diagnostics."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(r"D:\Great Scraper\Great Scraper")
sys.path[:0] = [str(ROOT / "shopee_getpc_crawler"), str(ROOT)]

import captcha_mint as mint
import cookie_guard
import proxy_pool
from session_pool import _apply_live_overlay

OUT = ROOT / "tw_deliverable_getpc"
CANARY = "https://shopee.tw/product/3109018/51908008002"
JAR = ROOT / "shopee_getpc_crawler" / "sessions" / "gm28.json"


def _load_cookies() -> dict:
    raw = json.loads(JAR.read_text(encoding="utf-8"))
    ck = raw.get("cookies") or raw
    if isinstance(ck, list):
        ck = {c["name"]: c["value"] for c in ck if isinstance(c, dict) and c.get("name")}
    ck = dict(ck)
    try:
        ck = _apply_live_overlay("gm28", ck, region=None)
    except Exception:
        pass
    return {k: str(v) for k, v in ck.items() if v}


def _pw_cookies(ck: dict) -> list:
    out = []
    for name, value in ck.items():
        out.append(
            {
                "name": name,
                "value": value,
                "domain": ".shopee.tw",
                "path": "/",
            }
        )
    return out


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    OUT.mkdir(parents=True, exist_ok=True)
    proxy_pool.set_region("TW")
    proxy_pool._POOL = []
    proxy_pool.load()
    proxy, raw, _ = mint._proxy_dict("TW")
    print(f"proxy={raw}", flush=True)

    ck = _load_cookies()
    print(
        f"jar keys={len(ck)} EC={bool(ck.get('SPC_EC'))} ST={bool(ck.get('SPC_ST'))} "
        f"U={ck.get('SPC_U','')[:12]} CERT={bool(ck.get('AC_CERT_D'))}",
        flush=True,
    )

    from patchright.sync_api import sync_playwright

    hits = []
    apis = []

    def on_resp(r):
        u = r.url
        short = u.split("?")[0]
        if "/api/" in u:
            apis.append(short[-80:])
        if "/api/v4/pdp/get_pc" not in u and "/api/v4/pdp/get_rw" not in u:
            return
        try:
            j = json.loads(r.text())
        except Exception as e:
            hits.append({"url": short, "err": f"parse:{e}", "ok": False})
            print(f"  PARSE {short}", flush=True)
            return
        err = j.get("error")
        d = j.get("data") or {}
        item = d.get("item") or {}
        ok = err in (0, None) and bool(item.get("title") or item.get("itemid"))
        rec = {
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
                json.dumps(j, ensure_ascii=False)[:800000], encoding="utf-8"
            )

    p = sync_playwright().start()
    ctx = p.chromium.launch_persistent_context(
        tempfile.mkdtemp(prefix="twspa2_"),
        channel="chrome",
        headless=False,
        proxy=proxy,
        viewport={"width": 1280, "height": 900},
        locale="zh-TW",
        timezone_id="Asia/Taipei",
        extra_http_headers={"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    if ck:
        try:
            ctx.add_cookies(_pw_cookies(ck))
            print("cookies injected", flush=True)
        except Exception as e:
            print(f"cookie inject fail: {e}", flush=True)

    page = ctx.new_page()
    page.on("response", on_resp)

    def snap(tag: str):
        try:
            title = page.title()
        except Exception:
            title = "?"
        url = page.url
        print(f"[{tag}] url={url[:140]} title={title[:80]!r}", flush=True)
        try:
            (OUT / f"_spa_{tag}.png").write_bytes(page.screenshot(full_page=False))
        except Exception:
            pass

    print("goto home...", flush=True)
    try:
        page.goto("https://shopee.tw/", wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"  home exc: {e}", flush=True)
    time.sleep(5)
    snap("home")

    # try pass traffic if needed
    if "/verify/" in page.url:
        print("waiting verify pass...", flush=True)
        try:
            mint._wait_traffic_pass(page, "shopee.tw", secs=20)
        except Exception as e:
            print(f"  verify wait: {e}", flush=True)
        snap("after_verify")

    print(f"goto canary...", flush=True)
    try:
        page.goto(CANARY, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"  pdp exc: {e}", flush=True)
    for i in range(6):
        time.sleep(5)
        try:
            page.mouse.wheel(0, 800)
        except Exception:
            pass
        snap(f"pdp_{i}")
        if hits:
            break
        if "/verify/" in page.url:
            try:
                mint._wait_traffic_pass(page, "shopee.tw", secs=15)
            except Exception:
                pass

    print(f"unique api paths seen: {len(set(apis))}", flush=True)
    for a in sorted(set(apis))[:30]:
        print(f"  API {a}", flush=True)

    try:
        ctx.close()
        p.stop()
    except Exception:
        pass

    ok = [h for h in hits if h.get("ok")]
    (OUT / "_spa_probe2_hits.json").write_text(
        json.dumps({"hits": hits, "apis": sorted(set(apis))}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"RESULT ok={len(ok)}/{len(hits)}", flush=True)
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
