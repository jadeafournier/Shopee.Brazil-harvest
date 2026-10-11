# -*- coding: utf-8 -*-
"""BR-aware AC_CERT remint: pt-BR locale + Sao_Paulo TZ + SPA get_pc intercept.

Uses captcha_mint proxy rotation but proper BR browser context (unlike stock mint
which uses locale=en-BR). Strips old AC_CERT_*; keeps or drops SPC_U via --nologin.
Success = new AC_CERT_D minted OR full get_pc (error 0/None with item).
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(HERE))

from br_cookies import br_context_kwargs, enforce_br_locale, normalize_br_cookies, pw_cookie_list
import captcha_mint as mint
import captcha_playwright as cpw
import proxy_pool
from _br_probe import parse_br_test, BR_TEST

DOM = "shopee.com.br"
CANARY = (1397458631, 26420453879)
ATTEMPTS = int(__import__("os").environ.get("MINT_ATTEMPTS", "10"))
LOG = HERE / "br_brmint_out.log"


def _log(m: str) -> None:
    line = m
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _sync():
    ck, proxies, _ = parse_br_test(BR_TEST)
    # Prefer live BR TEST.txt
    text = (HERE / "BR TEST.txt").read_text(encoding="utf-8")
    proxies = []
    in_p = False
    jar = {}
    for line in text.splitlines():
        if "SPC_F=" in line or "AC_CERT_D=" in line:
            for part in line.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    jar[k.strip()] = v.strip()
        if line.strip().startswith("Proxy:"):
            in_p = True
            continue
        if in_p:
            if not line.strip() or line.strip().startswith("Test Url"):
                break
            proxies.append(line.strip())
    if proxies:
        (ROOT / "proxy(BR shopee).txt").write_text("\n".join(proxies) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()
    return jar or ck, proxies


def attempt(p, jar: dict, *, nologin: bool, idx: int) -> dict:
    from patchright.sync_api import sync_playwright  # noqa: F401 — p already started

    work = dict(jar)
    for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
        work.pop(k, None)
    if nologin:
        work.pop("SPC_U", None)
    work = enforce_br_locale(normalize_br_cookies(work, fresh_cert=False))

    proxy, raw, sub = mint._proxy_dict("BR")
    host = (raw or "?").split("@")[-1].split(":")[0]
    udd = tempfile.mkdtemp(prefix="brmint_")
    cx = br_context_kwargs()
    out = {"ok": False, "reason": "init", "host": host, "sub": (sub or "")[-40:], "cookies": None}

    ctx = p.chromium.launch_persistent_context(
        udd,
        channel="chrome",
        headless=False,
        proxy=proxy,
        viewport={"width": mint.VIEW_W, "height": mint.VIEW_H},
        locale=cx["locale"],
        timezone_id=cx["timezone_id"],
        extra_http_headers=cx["extra_http_headers"],
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
    )
    getpc = {"err": None, "seen": False, "keys": 0, "data": None}
    gen = {"ok": False}
    try:
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        ctx.add_cookies(pw_cookie_list(work, fresh_cert=False))
        page = ctx.new_page()

        def on_resp(r):
            try:
                if "anti_fraud/captcha/generate" in r.url:
                    gen["ok"] = True
                if "/api/v4/pdp/get_pc" in r.url:
                    j = json.loads(r.text())
                    getpc["err"] = j.get("error")
                    getpc["seen"] = True
                    item = (j.get("data") or {}).get("item") or {}
                    getpc["keys"] = len(item) if isinstance(item, dict) else 0
                    if getpc["err"] in (0, None) and getpc["keys"] > 20:
                        getpc["data"] = j.get("data")
            except Exception:
                pass

        page.on("response", on_resp)

        # warm home
        try:
            page.goto(f"https://{DOM}/", wait_until="commit", timeout=60000)
        except Exception as e:
            out["reason"] = f"home:{str(e)[:60]}"
            return out
        page.wait_for_timeout(random.randint(2200, 3800))
        page.mouse.move(random.randint(200, 900), random.randint(180, 520))
        page.mouse.wheel(0, random.randint(600, 1600))
        page.wait_for_timeout(800)

        if "/verify/traffic" in (page.url or ""):
            mint._wait_traffic_pass(page, DOM, secs=28)
        if "/verify/traffic/error" in (page.url or ""):
            out["reason"] = "traffic_error_home"
            out["state"] = "traffic_error"
            return out

        shop, item = CANARY
        try:
            page.goto(f"https://{DOM}/product/{shop}/{item}", wait_until="commit", timeout=60000)
        except Exception as e:
            out["reason"] = f"pdp:{str(e)[:60]}"

        deadline = time.time() + 45
        while time.time() < deadline:
            u = page.url or ""
            if "/verify/captcha" in u:
                out["state"] = "captcha"
                page.wait_for_timeout(4000)
                sol = cpw.solve_captcha_page(page, dom=DOM, gen_flag=gen)
                out["captcha"] = sol.get("ok")
                if not sol.get("ok"):
                    out["reason"] = f"captcha_fail:{sol.get('reason')}"
                    break
                try:
                    page.goto(f"https://{DOM}/product/{shop}/{item}", wait_until="commit", timeout=45000)
                except Exception:
                    pass
            elif "/verify/traffic/error" in u:
                out["reason"] = "traffic_error_pdp"
                out["state"] = "traffic_error"
                break
            elif "/verify/traffic" in u:
                mint._wait_traffic_pass(page, DOM, secs=20)
            else:
                if getpc.get("data"):
                    break
                page.mouse.move(random.randint(200, 900), random.randint(200, 500))
                page.wait_for_timeout(900)

        cur = {c["name"]: c["value"] for c in ctx.cookies()}
        new_cert = cur.get("AC_CERT_D") or ""
        old_prefix = (jar.get("AC_CERT_D") or "")[:40]
        is_new = bool(new_cert) and new_cert[:40] != old_prefix
        out["ac_cert_new"] = is_new
        out["ac_cert"] = bool(new_cert)
        out["getpc_err"] = getpc["err"]
        out["item_keys"] = getpc["keys"]
        out["state"] = out.get("state") or ("product" if "/product/" in (page.url or "") else (page.url or "")[:40])

        if is_new or (getpc.get("data") and getpc["keys"] >= 40):
            merged = dict(work)
            for k, v in cur.items():
                if v and v not in ("-", "deleted"):
                    merged[k] = v
            merged = enforce_br_locale(merged)
            out["ok"] = True
            out["cookies"] = merged
            out["reason"] = "new_cert" if is_new else "getpc_ok"
            out["data"] = getpc.get("data")
        else:
            out["reason"] = out.get("reason") or f"no_cert_getpc_err={getpc['err']}_keys={getpc['keys']}"
        return out
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    nologin = "--nologin" in sys.argv
    LOG.write_text("", encoding="utf-8")
    jar, proxies = _sync()
    _log(f"BR remint start nologin={nologin} proxies={len(proxies)} jar_keys={len(jar)}")
    _log(f"  old AC_CERT prefix={(jar.get('AC_CERT_D') or '')[:40]!r} SPC_U={jar.get('SPC_U')}")

    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        for i in range(ATTEMPTS):
            out = attempt(p, jar, nologin=nologin, idx=i)
            _log(
                f"  [{i+1}/{ATTEMPTS}] host={out.get('host')} state={out.get('state')} "
                f"ok={out.get('ok')} reason={out.get('reason')} "
                f"new_cert={out.get('ac_cert_new')} getpc_err={out.get('getpc_err')} "
                f"item_keys={out.get('item_keys')}"
            )
            if out.get("ok") and out.get("cookies"):
                name = f"br_remint_{int(time.time())}"
                path = ROOT / "shopee_getpc_crawler" / "sessions" / "BR" / f"{name}.json"
                path.write_text(
                    json.dumps(
                        {
                            "account_name": name,
                            "region": "BR",
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "cookies": out["cookies"],
                            "nologin": nologin,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                _log(f"SAVED {path.name} AC_CERT_new={out.get('ac_cert_new')}")
                # update BR TEST AC_CERT if new
                if out.get("ac_cert_new"):
                    brt = HERE / "BR TEST.txt"
                    text = brt.read_text(encoding="utf-8")
                    import re

                    text2 = re.sub(
                        r"AC_CERT_D=[^;\s]+",
                        f"AC_CERT_D={out['cookies']['AC_CERT_D']}",
                        text,
                        count=1,
                    )
                    brt.write_text(text2, encoding="utf-8")
                    _log("Updated BR TEST.txt AC_CERT_D")
                print("REMINT_OK=1")
                return 0
    _log("REMINT failed all attempts")
    print("REMINT_OK=0")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
