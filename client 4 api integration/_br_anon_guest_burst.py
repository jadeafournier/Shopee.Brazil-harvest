# -*- coding: utf-8 -*-
"""Anonymous guest SPA get_pc probe — no SPC_U login jars. CapSolver if captcha."""
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

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(HERE))

from br_cookies import br_context_kwargs
import br_human as human
import captcha_mint as mint
import proxy_pool

DELIV = HERE / "br_deliverable_getpc"
DELIV.mkdir(exist_ok=True)
URLS = HERE / "br_active_product_urls_100k.txt"
LOG = HERE / "br_anon_guest_run.log"
MIN_KEYS = 40
KEY_FILE = HERE / ".capsolver_key.local"


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def ensure_capsolver() -> None:
    if os.environ.get("CAPSOLVER_API_KEY"):
        return
    if KEY_FILE.is_file():
        os.environ["CAPSOLVER_API_KEY"] = KEY_FILE.read_text(encoding="utf-8").strip()


def rotate() -> None:
    def sid():
        return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))

    cities = [
        ("Rio de Janeiro", "Rio de Janeiro"),
        ("São Paulo", "Barueri"),
        ("São Paulo", "Campinas"),
    ]
    lines = []
    for i in range(20):
        st, city = cities[i % len(cities)]
        user = f"gdxc12191-region-BR-st-{st}-city-{city}-sid-{sid()}-t-120"
        lines.append(f"us.1024proxy.io:3000:{user}:qadnrubc")
    (ROOT / "proxy(BR shopee).txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()


def parse_ids(u: str):
    m = re.search(r"i\.(\d+)\.(\d+)", u) or re.search(r"/product/(\d+)/(\d+)", u)
    return (m.group(1), m.group(2)) if m else None


def have() -> set:
    s = set()
    for f in DELIV.glob("output_*.json"):
        m = re.match(r"output_(\d+)_(\d+)\.json", f.name)
        if m:
            s.add((m.group(1), m.group(2)))
    return s


def save_full(j: dict, fallback_url: str) -> bool:
    data = j.get("data")
    if not isinstance(data, dict) or j.get("error") not in (0, None):
        return False
    item = data.get("item")
    if not isinstance(item, dict) or len(item) < MIN_KEYS:
        return False
    shop = str(item.get("shop_id") or item.get("shopid") or "")
    it = str(item.get("item_id") or item.get("itemid") or "")
    if not shop or not it:
        return False
    out = DELIV / f"output_{shop}_{it}.json"
    if out.is_file():
        return False
    payload = {
        "task_link": fallback_url,
        "token": "br_anon_guest",
        "api_key": "br_anon_guest",
        "bff_meta": {"extract_method": "browser_get_pc_guest"},
        "error": None,
        "error_msg": None,
        "data": data,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return True


def main() -> int:
    ensure_capsolver()
    log(f"ANON guest start cap={'Y' if os.environ.get('CAPSOLVER_API_KEY') else 'N'}")
    have_ids = have()
    pending = []
    for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        ids = parse_ids(line)
        if ids and ids not in have_ids:
            pending.append(line)
        if len(pending) >= 12:
            break
    if not pending:
        log("no pending")
        return 1

    from patchright.sync_api import sync_playwright

    rotate()
    proxy, _, sub = mint._proxy_dict("BR")
    log(f"proxy …{(sub or '')[-40:]}")

    stats = {"ok": 0, "fail": 0}
    got = {"n": 0}
    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="branon_")
    cx = br_context_kwargs()
    ctx = None
    try:
        ctx = p.chromium.launch_persistent_context(
            udd,
            channel="chrome",
            headless=False,
            proxy=proxy,
            viewport={"width": 1366, "height": 768},
            locale=cx["locale"],
            timezone_id=cx["timezone_id"],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            extra_http_headers=cx["extra_http_headers"],
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()

        def on_resp(r):
            try:
                if "/api/v4/pdp/get_pc" not in r.url or r.status != 200:
                    return
                j = r.json()
                item = ((j.get("data") or {}).get("item") if isinstance(j.get("data"), dict) else None) or {}
                err = j.get("error")
                keys = len(item) if isinstance(item, dict) else 0
                log(f"get_pc err={err} keys={keys} guest")
                if err in (0, None) and keys >= MIN_KEYS:
                    got["n"] += 1
                    if save_full(j, r.url):
                        stats["ok"] += 1
                        log(f"OK guest +1 total={len(list(DELIV.glob('output_*.json')))}")
            except Exception:
                pass

        page.on("response", on_resp)
        human.warm_home(page, "shopee.com.br")
        for _ in range(6):
            u = page.url or ""
            if "/verify/captcha" in u:
                try:
                    import captcha_playwright as cpw
                    sol = cpw.solve_captcha_page(page, dom="shopee.com.br", gen_flag=None)
                    log(f"captcha {sol}")
                except Exception as e:
                    log(f"captcha_err {e}")
                    break
            elif "/verify/traffic" in u and "/error" not in u:
                mint._wait_traffic_pass(page, "shopee.com.br", secs=30)
            else:
                break
            page.wait_for_timeout(800)

        ck0 = {c["name"]: c["value"] for c in ctx.cookies()}
        log(f"guest keys={sorted(ck0.keys())} SPC_U={'Y' if ck0.get('SPC_U') else 'N'} CERT={'Y' if ck0.get('AC_CERT_D') else 'N'}")

        for i, url in enumerate(pending):
            if stats["ok"] >= 5 or stats["fail"] >= 4:
                break
            before = got["n"]
            log(f"goto[{i}] …{url[-48:]}")
            try:
                page.goto(url, wait_until="commit", timeout=50000)
            except Exception as e:
                log(f"goto {type(e).__name__}")
            deadline = time.time() + 48
            while time.time() < deadline:
                u = page.url or ""
                if "/verify/captcha" in u:
                    try:
                        import captcha_playwright as cpw
                        cpw.solve_captcha_page(page, dom="shopee.com.br", gen_flag=None)
                    except Exception as e:
                        log(f"cap {e}")
                        break
                if "/verify/traffic/error" in u:
                    log("traffic/error")
                    stats["fail"] += 1
                    break
                if "/verify/traffic" in u:
                    mint._wait_traffic_pass(page, "shopee.com.br", secs=22)
                if got["n"] > before:
                    break
                page.wait_for_timeout(400)
            if got["n"] == before:
                stats["fail"] += 1
                log(f"miss fail={stats['fail']}")
            try:
                human.idle(page, 0.6, 1.2)
            except Exception:
                time.sleep(0.8)

        log(f"ANON done ok={stats['ok']} fail={stats['fail']}")
    finally:
        try:
            if ctx:
                ctx.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
    return 0 if stats["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
