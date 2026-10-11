# -*- coding: utf-8 -*-
"""TW loginless full get_pc hunter.

Hard requirement: no user login cookies.
Strategy (proven on BR): rotate sticky residential IPs → headed Chrome SPA
intercepts page-native signed get_pc. CapSolver when /verify/captcha appears.
Success = error in (0,None) + item_keys >= 40. Persist AC_CERT guest jar.
"""
from __future__ import annotations

import json
import os
import random
import re
import string
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(r"D:\Great Scraper\Great Scraper")
HERE = ROOT / "client 4 api integration"
CRAWLER = ROOT / "shopee_getpc_crawler"
sys.path[:0] = [str(CRAWLER), str(HERE), str(ROOT)]

import captcha_mint as mint
import proxy_pool

OUT = ROOT / "tw_deliverable_getpc"
OUT.mkdir(parents=True, exist_ok=True)
SESS_TW = CRAWLER / "sessions" / "TW"
SESS_TW.mkdir(parents=True, exist_ok=True)
LOG = OUT / "guest_force.log"
URLS = ROOT / "tw_product_urls.txt"
KEY_FILE = HERE / ".capsolver_key.local"
MIN_KEYS = 40
ATTEMPTS = int(os.environ.get("TW_FORCE_ATTEMPTS", "24"))
CANARY = "https://shopee.tw/product/3109018/51908008002"
DOM = "shopee.tw"


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def ensure_capsolver() -> None:
    if os.environ.get("CAPSOLVER_API_KEY"):
        return
    if KEY_FILE.is_file():
        os.environ["CAPSOLVER_API_KEY"] = KEY_FILE.read_text(encoding="utf-8").strip()


def sid(n: int = 8) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def rotate_proxies(n: int = 40) -> None:
    """Fresh sticky TW SIDs — prefer proxy001/grass over burned 1024 pool."""
    lines: list[str] = []

    # Mint fresh proxy001 TW sticky sessions
    for _ in range(max(20, n // 2)):
        user = f"ojxsll758984_custom_zone_TW_sid_{sid(8)}_time_15"
        lines.append(f"us.proxy001.com:7878:{user}:pwd893517")

    # Mint fresh grass TW sticky sessions
    for _ in range(max(15, n // 3)):
        user = f"grass_sanzang-zone-custom-region-TW-session-{sid(9)}-sessTime-10"
        lines.append(f"{user}:qwe123@aproxy.grassdata.net:2333")

    # Sample audited pools (skip comments)
    for src_name, take in (
        ("proxy_tw.txt", 120),
        ("TW_new proxies.txt", 60),
        ("tw_residential_proxies.txt", 40),
    ):
        src = ROOT / src_name
        if not src.is_file():
            continue
        pool = [
            ln.strip()
            for ln in src.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        random.shuffle(pool)
        lines.extend(pool[:take])

    # Light 1024 fallback only
    for _ in range(8):
        user = f"gdxc12191-region-TW-sid-{sid()}-t-15"
        lines.append(f"us.1024proxy.io:3000:{user}:qadnrubc")

    random.shuffle(lines)
    (CRAWLER / "proxies.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    proxy_pool.set_region("TW")
    proxy_pool._POOL = []
    proxy_pool.load()
    log(f"proxy pool refreshed lines={len(lines)}")


def seed_urls(limit: int = 8) -> list[str]:
    out = [CANARY]
    if URLS.is_file():
        for ln in URLS.read_text(encoding="utf-8-sig").splitlines():
            ln = ln.strip()
            if ln.startswith("https://shopee.tw/"):
                out.append(ln)
            if len(out) >= limit:
                break
    # known historically live
    out.extend(
        [
            "https://shopee.tw/product/3892560/182787875",
            "https://shopee.tw/product/72724/29750673165",
            "https://shopee.tw/product/154059682/27679388213",
        ]
    )
    # dedup preserve order
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def save_full(j: dict, task_url: str) -> Path | None:
    data = j.get("data")
    if not isinstance(data, dict) or j.get("error") not in (0, None):
        return None
    item = data.get("item")
    if not isinstance(item, dict) or len(item) < MIN_KEYS:
        return None
    shop = str(item.get("shop_id") or item.get("shopid") or "")
    it = str(item.get("item_id") or item.get("itemid") or "")
    if not shop or not it:
        return None
    path = OUT / f"output_{shop}_{it}.json"
    payload = {
        "task_link": task_url or f"https://shopee.tw/product/{shop}/{it}",
        "token": "tw_guest_force",
        "api_key": "tw_guest_force",
        "bff_meta": {"extract_method": "browser_get_pc_guest_loginless"},
        "error": None,
        "error_msg": None,
        "data": data,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def persist_guest_jar(cookies: dict, tag: str) -> Path:
    # strip login identity if any (loginless requirement)
    jar = {
        k: v
        for k, v in cookies.items()
        if k not in ("SPC_U", "SPC_EC", "SPC_ST") and v and v not in ("-", "deleted")
    }
    name = f"tw_guest_{tag}_{int(time.time())}"
    doc = {
        "account_name": name,
        "region": "TW",
        "domain": "shopee.tw",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "loginless": True,
        "cookies": jar,
    }
    path = SESS_TW / f"{name}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def attempt_once(p, urls: list[str], idx: int) -> dict:
    proxy, raw, sub = mint._proxy_dict("TW")
    host = (raw or "").split("@")[-1][:48] if raw else "?"
    udd = tempfile.mkdtemp(prefix=f"twforce{idx}_")
    result = {"ok": False, "reason": "init", "host": host, "saved": None}
    hits = []

    from patchright.sync_api import sync_playwright  # noqa: F401 — already started outside

    ctx = p.chromium.launch_persistent_context(
        udd,
        channel="chrome",
        headless=False,
        proxy=proxy,
        viewport={"width": 1366, "height": 800},
        locale="zh-TW",
        timezone_id="Asia/Taipei",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        extra_http_headers={"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    try:
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = ctx.new_page()

        def on_resp(r):
            try:
                if "/api/v4/pdp/get_pc" not in r.url:
                    return
                j = r.json()
                item = ((j.get("data") or {}).get("item") if isinstance(j.get("data"), dict) else {}) or {}
                err = j.get("error")
                keys = len(item) if isinstance(item, dict) else 0
                signed = bool((r.request.headers or {}).get("x-sap-sec"))
                hits.append({"err": err, "keys": keys, "signed": signed, "http": r.status})
                log(f"  get_pc http={r.status} err={err} keys={keys} signed={signed}")
                if err in (0, None) and keys >= MIN_KEYS:
                    path = save_full(j, page.url)
                    if path:
                        result.update(ok=True, reason="get_pc_ok", saved=str(path), body=j)
            except Exception:
                pass

        page.on("response", on_resp)

        # warm home
        try:
            page.goto(f"https://{DOM}/", wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            result["reason"] = f"home:{type(e).__name__}"
        page.wait_for_timeout(random.randint(2500, 4500))
        try:
            page.mouse.move(random.randint(200, 900), random.randint(200, 500))
        except Exception:
            pass

        def handle_gates(secs: float = 28.0) -> str:
            deadline = time.time() + secs
            while time.time() < deadline:
                u = page.url or ""
                if result["ok"]:
                    return "ok"
                if "/verify/captcha" in u:
                    try:
                        import captcha_playwright as cpw

                        sol = cpw.solve_captcha_page(page, dom=DOM, gen_flag=None)
                        log(f"  captcha_sol={sol.get('ok')} {sol.get('reason', sol)[:80] if isinstance(sol.get('reason'), str) else sol}")
                    except Exception as e:
                        log(f"  captcha_exc={e}")
                        return "captcha_fail"
                    page.wait_for_timeout(1500)
                    continue
                if "/verify/traffic/error" in u:
                    return "traffic_error"
                if "/verify/traffic" in u:
                    mint._wait_traffic_pass(page, DOM, secs=18)
                    page.wait_for_timeout(800)
                    continue
                page.wait_for_timeout(500)
            return "timeout"

        gate = handle_gates(20)
        if gate == "traffic_error":
            result["reason"] = "traffic_error_home"
            return result

        # visit products
        for url in urls[:4]:
            if result["ok"]:
                break
            before = len([h for h in hits if h.get("keys", 0) >= MIN_KEYS and h.get("err") in (0, None)])
            log(f"  goto {url[-55:]}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=50000)
            except Exception as e:
                log(f"  goto_exc {type(e).__name__}")
            # scroll to encourage SPA fetch
            for _ in range(4):
                try:
                    page.mouse.wheel(0, 700)
                except Exception:
                    pass
                page.wait_for_timeout(1200)
                if result["ok"]:
                    break
                u = page.url or ""
                if "/verify/captcha" in u:
                    handle_gates(25)
                elif "/verify/traffic/error" in u:
                    result["reason"] = "traffic_error_pdp"
                    return result
                elif "/verify/traffic" in u:
                    mint._wait_traffic_pass(page, DOM, secs=18)

            # if we saw 90309999 on product shell, force reload → captcha
            blocked = any(h.get("err") == 90309999 for h in hits[-3:])
            if blocked and not result["ok"] and "/product/" in (page.url or ""):
                log("  force reload for captcha")
                for _ in range(2):
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=40000)
                    except Exception:
                        break
                    handle_gates(22)
                    if result["ok"] or "/verify/captcha" in (page.url or ""):
                        break

            after = len([h for h in hits if h.get("keys", 0) >= MIN_KEYS and h.get("err") in (0, None)])
            if after == before and not result["ok"]:
                continue

        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        result["cookies"] = ck
        result["hits"] = hits
        result["cert"] = bool(ck.get("AC_CERT_D"))
        result["url"] = (page.url or "")[:120]
        if result["ok"]:
            jar_path = persist_guest_jar(ck, "ok")
            result["jar"] = str(jar_path)
            result["reason"] = "get_pc_ok"
        elif ck.get("AC_CERT_D"):
            jar_path = persist_guest_jar(ck, "cert")
            result["jar"] = str(jar_path)
            result["reason"] = result.get("reason") or "cert_only"
        elif not result.get("reason") or result["reason"] == "init":
            result["reason"] = f"no_unlock hits={len(hits)} url={result.get('url','')[:40]}"
    finally:
        try:
            ctx.close()
        except Exception:
            pass
    return result


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ensure_capsolver()
    log(f"TW guest FORCE start attempts={ATTEMPTS} cap={'Y' if os.environ.get('CAPSOLVER_API_KEY') else 'N'}")
    rotate_proxies(50)
    urls = seed_urls(10)
    log(f"urls={len(urls)} first={urls[0]}")

    from patchright.sync_api import sync_playwright

    ok_n = 0
    with sync_playwright() as p:
        for i in range(ATTEMPTS):
            # refresh sticky pool every 6 attempts
            if i > 0 and i % 6 == 0:
                rotate_proxies(40)
            log(f"=== attempt {i+1}/{ATTEMPTS} ===")
            try:
                r = attempt_once(p, urls, i)
            except Exception as e:
                log(f"attempt_exc {type(e).__name__}: {e}")
                continue
            log(
                f"  result ok={r.get('ok')} reason={r.get('reason')} "
                f"cert={r.get('cert')} host={r.get('host')} saved={r.get('saved')}"
            )
            (OUT / f"_force_attempt_{i+1}.json").write_text(
                json.dumps({k: v for k, v in r.items() if k not in ("body", "cookies")}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if r.get("ok"):
                ok_n += 1
                # keep hunting a few more with same jar cookies? persist already done
                if ok_n >= 3:
                    break
            # brief pause between browsers
            time.sleep(random.uniform(1.2, 2.5))

    n_out = len(list(OUT.glob("output_*.json")))
    log(f"DONE ok_saves≈{ok_n} output_files={n_out}")
    return 0 if ok_n else 3


if __name__ == "__main__":
    raise SystemExit(main())
