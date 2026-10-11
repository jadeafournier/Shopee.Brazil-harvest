# -*- coding: utf-8 -*-
"""Lean sequential BR get_pc harvest to 1000. Step-logged, short timeouts, rotate jars."""
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
import captcha_mint as mint
import proxy_pool

DOM = "shopee.com.br"
DELIV = HERE / "br_deliverable_getpc"
DELIV.mkdir(exist_ok=True)
URLS = HERE / "br_active_product_urls_100k.txt"
SESS = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
LOG = HERE / "br_lean_1k_run.log"
TARGET = 1000
MIN_KEYS = 40

JARS = [
    "br_5196720216_hsRjFTxp_live.json",
    "br_1707407334_mRNUQARQ_live.json",
    "br_1706801030_ydvUxsIK_live.json",
    "br_1706785017_CoOHjjSQ_live.json",
    "br_1706784820_OUk7hm0A_live.json",
    "br_1705283243_F3zrbhjv_live.json",
    "br_1707139756_0EAyNQaW_live.json",
    "br_1707408898_LAxNlL5a_live.json",
]

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

CITIES = [
    ("São Paulo", "Barueri"),
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


def have_ids():
    s = set()
    for f in DELIV.glob("output_*.json"):
        m = re.match(r"output_(\d+)_(\d+)\.json", f.name)
        if m:
            s.add((m.group(1), m.group(2)))
    return s


def parse_ids(u: str):
    m = re.search(r"i\.(\d+)\.(\d+)", u) or re.search(r"/product/(\d+)/(\d+)", u)
    return (m.group(1), m.group(2)) if m else None


def rotate_proxies(n: int = 40) -> None:
    def sid():
        return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))

    lines = []
    for i in range(n):
        st, city = CITIES[i % len(CITIES)]
        user = f"gdxc12191-region-BR-st-{st}-city-{city}-sid-{sid()}-t-120"
        lines.append(f"us.1024proxy.io:3000:{user}:qadnrubc")
    (ROOT / "proxy(BR shopee).txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()
    log(f"proxies rotated {n}")


def load_jar(name: str) -> dict:
    p = SESS / name
    doc = json.loads(p.read_text(encoding="utf-8"))
    return dict(doc.get("cookies") or doc)


def save_full(j: dict, fallback_url: str) -> bool:
    data = j.get("data")
    if not isinstance(data, dict):
        return False
    if j.get("error") not in (0, None):
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
        "token": "br_lean_1k",
        "api_key": "br_lean_1k",
        "bff_meta": {"extract_method": "browser_get_pc"},
        "error": None,
        "error_msg": None,
        "data": data,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return True


def run_shift(jar_name: str, urls: list[str], quota: int = 12) -> dict:
    from patchright.sync_api import sync_playwright

    jar = load_jar(jar_name)
    # Keep cert ~half the time; remint when stripped (matches parallel_1k)
    if random.random() < 0.45:
        for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
            jar.pop(k, None)
    jar = enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
    if not jar.get("SPC_U"):
        log(f"skip {jar_name} no SPC_U")
        return {"ok": 0, "fail": 0, "fatal": True}

    proxy, _, sub = mint._proxy_dict("BR")
    ua = random.choice(UAS)
    stats = {"ok": 0, "fail": 0, "fatal": False}
    log(f"SHIFT {jar_name} SPC_U={jar.get('SPC_U')} n={len(urls)} sub=...{(sub or '')[-40:]}")

    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="brlean_")
    cx = br_context_kwargs()
    ctx = None
    try:
        log("launch chrome…")
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
        got = {"n": 0}

        def on_resp(r):
            try:
                u = r.url
                if "/api/v4/" in u and ("get_pc" in u or "pdp" in u or "recommend" in u):
                    log(f"api {r.status} {u.split('shopee.com.br')[-1][:90]}")
                if "/api/v4/pdp/get_pc" not in u:
                    return
                if r.status != 200:
                    log(f"get_pc HTTP {r.status}")
                    return
                j = r.json()
                data = j.get("data") if isinstance(j, dict) else None
                item = (data or {}).get("item") if isinstance(data, dict) else None
                keys = len(item) if isinstance(item, dict) else 0
                err = j.get("error") if isinstance(j, dict) else "?"
                log(f"get_pc err={err} keys={keys}")
                if save_full(j, u):
                    got["n"] += 1
                    stats["ok"] += 1
                    log(f"OK +1 shift={got['n']} total={n_ok()}")
            except Exception as e:
                log(f"get_pc parse {type(e).__name__}")

        page.on("response", on_resp)

        # Short warm — timeout hard
        log("warm home…")
        try:
            page.goto(f"https://{DOM}/", wait_until="domcontentloaded", timeout=35000)
        except Exception as e:
            log(f"warm goto {type(e).__name__}")
        try:
            page.wait_for_timeout(2500)
        except Exception:
            time.sleep(2.5)
        log(f"home url={(page.url or '')[:80]} title={(page.title() or '')[:40]}")
        for _ in range(3):
            u = page.url or ""
            if "/verify/traffic/error" in u:
                stats["fatal"] = True
                log("FATAL traffic/error on home")
                return stats
            if "/verify/traffic" in u:
                log("traffic challenge — wait")
                try:
                    mint._wait_traffic_pass(page, DOM, secs=22)
                except Exception as e:
                    log(f"traffic wait {e}")
            else:
                break
            time.sleep(1)

        for i, url in enumerate(urls):
            if n_ok() >= TARGET or got["n"] >= quota or stats["fatal"]:
                break
            before = got["n"]
            log(f"goto[{i}] {url[-50:]}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=40000)
            except Exception as e:
                log(f"goto err {type(e).__name__}")
            # nudge SPA
            try:
                page.mouse.wheel(0, 600)
                page.wait_for_timeout(800)
                page.mouse.wheel(0, -200)
            except Exception:
                pass
            deadline = time.time() + 55
            while time.time() < deadline:
                u = page.url or ""
                if "/verify/captcha" in u:
                    stats["fatal"] = True
                    log("FATAL captcha")
                    break
                if "/verify/traffic/error" in u:
                    stats["fatal"] = True
                    log("FATAL traffic/error")
                    break
                if "/verify/traffic" in u:
                    log(f"traffic mid {u[:70]}")
                    try:
                        mint._wait_traffic_pass(page, DOM, secs=18)
                    except Exception:
                        pass
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=40000)
                    except Exception:
                        pass
                if got["n"] > before:
                    break
                time.sleep(0.4)
            if got["n"] == before:
                stats["fail"] += 1
                try:
                    log(f"miss fail={stats['fail']} url={(page.url or '')[:70]} title={(page.title() or '')[:40]}")
                except Exception:
                    log(f"miss fail={stats['fail']}")
            else:
                stats["fail"] = 0
            if stats["fail"] >= 5:
                log("5 misses — end shift")
                break
            time.sleep(random.uniform(1.0, 2.5))

        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        src = SESS / jar_name
        if src.is_file() and ck.get("AC_CERT_D"):
            try:
                doc = json.loads(src.read_text(encoding="utf-8"))
                doc.setdefault("cookies", {}).update(
                    {k: ck[k] for k in ck if k.startswith("AC_CERT") or k in ("SPC_SI", "SPC_SEC_SI")}
                )
                src.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        log(f"shift done ok={stats['ok']} fail={stats['fail']} fatal={stats['fatal']} total={n_ok()} cert={'Y' if ck.get('AC_CERT_D') else 'N'}")
    except Exception as e:
        log(f"EXC {e}")
        stats["fatal"] = True
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
    return stats


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    LOG.write_text("", encoding="utf-8")
    log(f"LEAN start n={n_ok()}/{TARGET}")
    rotate_proxies(40)

    have = have_ids()
    pending = []
    for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        ids = parse_ids(line)
        if ids and ids not in have:
            pending.append(line)
    log(f"pending {len(pending)}")

    cursor = 0
    jar_i = 0
    idle = 0
    while n_ok() < TARGET and cursor < len(pending) and idle < 40:
        jar = JARS[jar_i % len(JARS)]
        jar_i += 1
        chunk = pending[cursor : cursor + 8]
        cursor += 8
        if not chunk:
            break
        stats = run_shift(jar, chunk, quota=8)
        if stats["ok"] > 0:
            idle = 0
            time.sleep(10)
        else:
            idle += 1
            rotate_proxies(40)
            time.sleep(20 + idle * 4)
        log(f"progress {n_ok()}/{TARGET} idle={idle} cursor={cursor}")

    final = n_ok()
    log(f"DONE n={final} REACHED={final >= TARGET}")
    print(f"FINAL_COUNT={final}")
    return 0 if final >= TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
