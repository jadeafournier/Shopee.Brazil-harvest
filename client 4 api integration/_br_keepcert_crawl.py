# -*- coding: utf-8 -*-
"""Keep existing AC_CERT jars; crawl until 1000. Slow, single browser, no cert strip."""
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

DOM = "shopee.com.br"
DELIV = HERE / "br_deliverable_getpc"
DELIV.mkdir(exist_ok=True)
URLS = HERE / "br_active_product_urls_100k.txt"
SESS = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
LOG = HERE / "br_keepcert_run.log"
TARGET = 1000
MIN_KEYS = 40

CITIES = [
    ("São Paulo", "São Paulo"),
    ("São Paulo", "Campinas"),
    ("Rio de Janeiro", "Rio de Janeiro"),
    ("Minas Gerais", "Belo Horizonte"),
    ("Paraná", "Curitiba"),
    ("Bahia", "Salvador"),
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


def rotate_proxies(n: int = 30) -> None:
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


def cert_jars() -> list[str]:
    out = []
    for p in sorted(SESS.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        if "pool_state" in p.name:
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            ck = doc.get("cookies") or doc
            if ck.get("AC_CERT_D"):
                out.append(p.name)
        except Exception:
            pass
    # also login jars even without cert
    for p in SESS.glob("br_*_live.json"):
        if p.name not in out:
            out.append(p.name)
    return out


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
    path = DELIV / f"output_{shop}_{it}.json"
    if path.is_file():
        return False
    payload = {
        "task_link": fallback_url,
        "token": "br_keepcert",
        "api_key": "br_keepcert",
        "bff_meta": {"extract_method": "browser_get_pc"},
        "error": None,
        "error_msg": None,
        "data": data,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return True


def run_shift(jar_name: str, urls: list[str], *, strip_cert: bool, quota: int = 8) -> dict:
    from patchright.sync_api import sync_playwright

    doc = json.loads((SESS / jar_name).read_text(encoding="utf-8"))
    jar = dict(doc.get("cookies") or doc)
    if strip_cert:
        for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
            jar.pop(k, None)
    jar = enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
    proxy, _, sub = mint._proxy_dict("BR")
    stats = {"ok": 0, "fail": 0, "fatal": False}
    got = {"n": 0}
    log(f"SHIFT {jar_name} strip={strip_cert} SPC_U={jar.get('SPC_U')} cert={'Y' if jar.get('AC_CERT_D') else 'N'} sub=...{(sub or '')[-36:]}")

    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="brkc_")
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            extra_http_headers=cx["extra_http_headers"],
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
        page = ctx.new_page()

        def on_resp(r):
            try:
                if "/api/v4/pdp/get_pc" not in r.url or r.status != 200:
                    return
                j = r.json()
                item = ((j.get("data") or {}).get("item") if isinstance(j.get("data"), dict) else None) or {}
                err = j.get("error")
                keys = len(item) if isinstance(item, dict) else 0
                log(f"get_pc err={err} keys={keys}")
                if save_full(j, r.url):
                    got["n"] += 1
                    stats["ok"] += 1
                    log(f"OK +1 shift={got['n']} total={n_ok()}")
            except Exception as e:
                log(f"parse {type(e).__name__}")

        page.on("response", on_resp)
        try:
            human.warm_home(page, DOM)
        except Exception as e:
            log(f"warm {e}")
        for _ in range(5):
            u = page.url or ""
            if "/verify/traffic/error" in u:
                stats["fatal"] = True
                log("FATAL traffic/error home")
                return stats
            if "/verify/traffic" in u:
                mint._wait_traffic_pass(page, DOM, secs=30)
            else:
                break
            page.wait_for_timeout(1000)

        for i, url in enumerate(urls):
            if n_ok() >= TARGET or got["n"] >= quota or stats["fatal"]:
                break
            before = got["n"]
            log(f"goto[{i}] …{url[-48:]}")
            try:
                page.goto(url, wait_until="commit", timeout=50000)
            except Exception as e:
                log(f"goto {type(e).__name__}")
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
                    mint._wait_traffic_pass(page, DOM, secs=22)
                    try:
                        page.goto(url, wait_until="commit", timeout=50000)
                    except Exception:
                        pass
                if got["n"] > before:
                    break
                page.wait_for_timeout(400)
            if got["n"] == before:
                stats["fail"] += 1
                log(f"miss fail={stats['fail']}")
            else:
                stats["fail"] = 0
            if stats["fail"] >= 4:
                break
            time.sleep(random.uniform(2.0, 4.0))

        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        jar.update(ck)
        (SESS / jar_name).write_text(
            json.dumps({"cookies": jar, "ts": time.time()}, ensure_ascii=False), encoding="utf-8"
        )
        log(f"done ok={stats['ok']} fail={stats['fail']} fatal={stats['fatal']} total={n_ok()} cert={'Y' if ck.get('AC_CERT_D') else 'N'}")
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
    log(f"KEEP start n={n_ok()}/{TARGET}")
    rotate_proxies(30)
    jars = cert_jars()
    log(f"jars={jars[:10]}")
    have = have_ids()
    pending = []
    for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        ids = parse_ids(line)
        if ids and ids not in have:
            pending.append(line)
    log(f"pending={len(pending)}")

    cursor = 0
    jar_i = 0
    idle = 0
    while n_ok() < TARGET and cursor < len(pending) and idle < 60:
        jar = jars[jar_i % len(jars)]
        jar_i += 1
        chunk = pending[cursor : cursor + 8]
        cursor += 8
        # Prefer keep-cert first; alternate strip every 3rd
        strip = (jar_i % 3 == 0)
        stats = run_shift(jar, chunk, strip_cert=strip, quota=8)
        if stats["ok"] > 0:
            idle = 0
            # stay on this jar next
            jar_i -= 1
            time.sleep(12)
        else:
            idle += 1
            rotate_proxies(30)
            time.sleep(25 + idle * 3)
        log(f"progress {n_ok()}/{TARGET} idle={idle} cursor={cursor}")

    final = n_ok()
    log(f"DONE n={final}")
    print(f"FINAL_COUNT={final}")
    return 0 if final >= TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
