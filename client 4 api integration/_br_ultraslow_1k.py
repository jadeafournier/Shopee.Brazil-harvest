# -*- coding: utf-8 -*-
"""Ultra-slow BR get_pc: 1 URL per browser session, long gaps, per-account cooldown."""
from __future__ import annotations

import json
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

from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies, enforce_br_locale
import br_human as human
import captcha_mint as mint
import proxy_pool

DELIV = HERE / "br_deliverable_getpc"
DELIV.mkdir(exist_ok=True)
SESS = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
URLS = HERE / "br_active_product_urls_100k.txt"
LOG = HERE / "br_ultraslow_run.log"
TARGET = 1000

ACCOUNTS = [p.name for p in SESS.glob("br_*_live.json")]
UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]
CITIES = [
    ("São Paulo", "São Paulo"),
    ("Rio de Janeiro", "Rio de Janeiro"),
    ("Minas Gerais", "Belo Horizonte"),
    ("Paraná", "Curitiba"),
    ("Bahia", "Salvador"),
    ("Pernambuco", "Recife"),
]


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def n_ok() -> int:
    return sum(1 for p in DELIV.glob("output_*.json") if re.match(r"output_\d+_\d+\.json", p.name))


def rotate() -> None:
    lines = []
    for i in range(40):
        st, city = CITIES[i % len(CITIES)]
        sid = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))
        user = f"gdxc12191-region-BR-st-{st}-city-{city}-sid-{sid}-t-120"
        lines.append(f"us.1024proxy.io:3000:{user}:qadnrubc")
    (ROOT / "proxy(BR shopee).txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    from patchright.sync_api import sync_playwright

    have = set()
    for f in DELIV.glob("output_*.json"):
        m = re.match(r"output_(\d+)_(\d+)\.json", f.name)
        if m:
            have.add((m.group(1), m.group(2)))
    pending = []
    for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.search(r"/product/(\d+)/(\d+)", line) or re.search(r"i\.(\d+)\.(\d+)", line)
        if m and (m.group(1), m.group(2)) not in have:
            pending.append(line)

    log(f"ULTRASLOW start n={n_ok()} pending={len(pending)} accounts={len(ACCOUNTS)}")
    cooldown: dict[str, float] = {}
    i = 0
    ai = 0

    while n_ok() < TARGET and i < len(pending):
        acc = None
        for _ in range(max(1, len(ACCOUNTS))):
            cand = ACCOUNTS[ai % len(ACCOUNTS)]
            ai += 1
            if time.time() >= cooldown.get(cand, 0):
                acc = cand
                break
        if not acc:
            wait = min(cooldown.values()) - time.time() if cooldown else 60
            log(f"all cooling sleep {int(max(wait, 30))}s")
            time.sleep(max(wait, 30))
            continue

        url = pending[i]
        i += 1
        rotate()
        doc = json.loads((SESS / acc).read_text(encoding="utf-8"))
        jar = dict(doc.get("cookies") or doc)
        if random.random() < 0.5:
            for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
                jar.pop(k, None)
        jar = enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
        if not jar.get("SPC_U"):
            continue

        proxy, _, sub = mint._proxy_dict("BR")
        ua = random.choice(UAS)
        log(f"try {acc[:22]} …{url[-40:]} sub=...{(sub or '')[-30:]}")
        p = sync_playwright().start()
        udd = tempfile.mkdtemp(prefix="brus_")
        cx = br_context_kwargs()
        ctx = None
        saved = False
        try:
            ctx = p.chromium.launch_persistent_context(
                udd,
                channel="chrome",
                headless=False,
                proxy=proxy,
                viewport={"width": random.choice([1366, 1440]), "height": random.choice([768, 900])},
                locale=cx["locale"],
                timezone_id=cx["timezone_id"],
                user_agent=ua,
                extra_http_headers=cx["extra_http_headers"],
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
            page = ctx.new_page()
            holder: dict = {}

            def on_resp(r):
                try:
                    if "/api/v4/pdp/get_pc" not in r.url or r.status != 200:
                        return
                    j = r.json()
                    item = (j.get("data") or {}).get("item") or {}
                    err = j.get("error")
                    keys = len(item) if isinstance(item, dict) else 0
                    holder["err"] = err
                    holder["keys"] = keys
                    holder["j"] = j
                    log(f"get_pc err={err} keys={keys}")
                except Exception:
                    pass

            page.on("response", on_resp)
            try:
                human.warm_home(page, "shopee.com.br")
            except Exception:
                pass
            burned = False
            for _ in range(4):
                u = page.url or ""
                if "/verify/traffic/error" in u:
                    cooldown[acc] = time.time() + 3600
                    log(f"cool {acc} 60m traffic_error")
                    burned = True
                    break
                if "/verify/traffic" in u:
                    mint._wait_traffic_pass(page, "shopee.com.br", secs=28)
                else:
                    break
                page.wait_for_timeout(800)

            if not burned:
                page.goto(url, wait_until="commit", timeout=50000)
                t0 = time.time()
                while time.time() - t0 < 55:
                    if "/verify/traffic/error" in (page.url or ""):
                        cooldown[acc] = time.time() + 3600
                        log(f"cool {acc} 60m")
                        burned = True
                        break
                    if "/verify/traffic" in (page.url or ""):
                        mint._wait_traffic_pass(page, "shopee.com.br", secs=22)
                    if "j" in holder:
                        break
                    page.wait_for_timeout(400)

                if not burned and holder.get("err") in (0, None) and holder.get("keys", 0) >= 40:
                    j = holder["j"]
                    item = j["data"]["item"]
                    shop = str(item.get("shop_id") or item.get("shopid"))
                    it = str(item.get("item_id") or item.get("itemid"))
                    out = DELIV / f"output_{shop}_{it}.json"
                    if not out.exists():
                        payload = {
                            "task_link": url,
                            "token": "br_ultraslow",
                            "api_key": "br_ultraslow",
                            "bff_meta": {"extract_method": "browser_get_pc"},
                            "error": None,
                            "error_msg": None,
                            "data": j["data"],
                        }
                        out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                        saved = True
                        log(f"OK total={n_ok()}")
                elif holder.get("err") == 90309999:
                    cooldown[acc] = time.time() + 2700
                    log(f"cool {acc} 45m need_app_sign")

            ck = {c["name"]: c["value"] for c in ctx.cookies()}
            jar.update(ck)
            (SESS / acc).write_text(
                json.dumps({"cookies": jar, "ts": time.time()}, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            log(f"EXC {e}")
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

        gap = 90 if saved else 300
        log(f"gap {gap}s n={n_ok()}")
        time.sleep(gap)

    log(f"DONE n={n_ok()}")
    print(f"FINAL_COUNT={n_ok()}")
    return 0 if n_ok() >= TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
