# -*- coding: utf-8 -*-
"""Force TW /verify/captcha → CapSolver → AC_CERT → SPA get_pc (loginless)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(r"D:\Great Scraper\Great Scraper")
sys.path[:0] = [str(ROOT / "shopee_getpc_crawler"), str(ROOT / "client 4 api integration"), str(ROOT)]

OUT = ROOT / "tw_deliverable_getpc"
OUT.mkdir(parents=True, exist_ok=True)
KEY = ROOT / "client 4 api integration" / ".capsolver_key.local"
CANARY = "https://shopee.tw/product/3109018/51908008002"
CAPTCHA = (
    "https://shopee.tw/verify/captcha?"
    "next=https%3A%2F%2Fshopee.tw%2Fproduct%2F3109018%2F51908008002"
)
MIN_KEYS = 40


def log(m: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def main() -> int:
    if KEY.is_file():
        os.environ["CAPSOLVER_API_KEY"] = KEY.read_text(encoding="utf-8").strip()
    log(f"cap={'Y' if os.environ.get('CAPSOLVER_API_KEY') else 'N'}")

    import captcha_mint as mint
    import captcha_playwright as cpw
    from patchright.sync_api import sync_playwright

    saved = None
    hits = []
    gen = {"ok": False, "n": 0}

    with sync_playwright() as p:
        # try direct first (captcha URL worked without proxy earlier)
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="twcapforce_"),
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="zh-TW",
            timezone_id="Asia/Taipei",
            extra_http_headers={"Accept-Language": "zh-TW,zh;q=0.9"},
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()

        def on_resp(r):
            nonlocal saved
            u = r.url
            if "anti_fraud/captcha/generate" in u:
                try:
                    j = json.loads(r.text())
                    puzzle = j.get("error") is None and "encrypted_data" in j
                    gen["n"] += 1
                    if puzzle:
                        gen["ok"] = True
                    log(f"  captcha/generate puzzle={puzzle} err={j.get('error')} keys={list(j)[:8]}")
                    (OUT / "_captcha_generate.json").write_text(
                        json.dumps(j, ensure_ascii=False)[:50000], encoding="utf-8"
                    )
                except Exception as e:
                    log(f"  generate_parse {e}")
            if "/api/v4/pdp/get_pc" in u:
                try:
                    j = r.json()
                    item = ((j.get("data") or {}).get("item") if isinstance(j.get("data"), dict) else {}) or {}
                    err = j.get("error")
                    keys = len(item) if isinstance(item, dict) else 0
                    hits.append({"err": err, "keys": keys})
                    log(f"  get_pc err={err} keys={keys}")
                    if err in (0, None) and keys >= MIN_KEYS:
                        shop = str(item.get("shop_id") or item.get("shopid"))
                        it = str(item.get("item_id") or item.get("itemid"))
                        path = OUT / f"output_{shop}_{it}.json"
                        path.write_text(
                            json.dumps(
                                {
                                    "task_link": CANARY,
                                    "token": "tw_captcha_force",
                                    "api_key": "tw_captcha_force",
                                    "bff_meta": {"extract_method": "captcha_then_get_pc"},
                                    "error": None,
                                    "error_msg": None,
                                    "data": j["data"],
                                },
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                        saved = str(path)
                        log(f"  SAVED {path}")
                except Exception as e:
                    log(f"  get_pc_parse {e}")

        page.on("response", on_resp)

        log("goto captcha...")
        try:
            page.goto(CAPTCHA, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"goto {e}")
        # wait for puzzle generate
        for i in range(20):
            page.wait_for_timeout(1000)
            if gen["ok"]:
                break
            if i in (5, 10, 15):
                try:
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
            log(f"  wait_puzzle i={i} gen_ok={gen['ok']} n={gen['n']} url={page.url[:80]}")

        try:
            (OUT / "_captcha_force.png").write_bytes(page.screenshot())
        except Exception:
            pass

        if "/verify/captcha" in (page.url or ""):
            log("solving captcha...")
            sol = cpw.solve_captcha_page(page, dom="shopee.tw", gen_flag=gen)
            log(f"solve={sol}")
            page.wait_for_timeout(3000)
            # if still on captcha, wait for manual/auto clear
            cpw.wait_for_captcha_clear(page, timeout_s=45)

        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        log(f"after_cap CERT={bool(ck.get('AC_CERT_D'))} url={page.url[:100]} cookies={len(ck)}")
        (OUT / "_after_captcha_cookies.json").write_text(
            json.dumps({k: (v[:40] + "…") if len(str(v)) > 40 else v for k, v in ck.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if ck.get("AC_CERT_D") or "/product/" in (page.url or "") or saved:
            log("goto canary for get_pc...")
            try:
                page.goto(CANARY, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log(f"pdp {e}")
            for _ in range(15):
                if saved:
                    break
                try:
                    page.mouse.wheel(0, 500)
                except Exception:
                    pass
                page.wait_for_timeout(1200)
            if not saved and any(h.get("err") == 90309999 for h in hits):
                try:
                    page.reload(wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    pass
                page.wait_for_timeout(10000)

        ck2 = {c["name"]: c["value"] for c in ctx.cookies()}
        if ck2.get("AC_CERT_D"):
            jar = {
                k: v
                for k, v in ck2.items()
                if k not in ("SPC_U", "SPC_EC", "SPC_ST") and v not in ("-", "deleted", None, "")
            }
            jpath = OUT / "tw_guest_cert.json"
            jpath.write_text(
                json.dumps({"domain": "shopee.tw", "loginless": True, "cookies": jar}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log(f"persisted cert jar {jpath}")

        try:
            ctx.close()
        except Exception:
            pass

    log(f"DONE saved={saved} hits={hits}")
    return 0 if saved else 3


if __name__ == "__main__":
    raise SystemExit(main())
