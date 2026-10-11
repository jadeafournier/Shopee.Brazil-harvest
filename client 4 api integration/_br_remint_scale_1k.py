# -*- coding: utf-8 -*-
"""Scale path: CapSolver/local-CV remint AC_CERT on fresh sticky IPs → login-less get_pc → deliverable.

Bypasses burned login jars. Writes full get_pc envelopes to br_deliverable_getpc.
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

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(HERE))

from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies, enforce_br_locale
from _br_probe import parse_br_test, BR_TEST
import br_human as human
import captcha_mint as mint
import proxy_pool

DELIV = HERE / "br_deliverable_getpc"
DEV = HERE / "br_device_sessions"
DEV.mkdir(exist_ok=True)
DELIV.mkdir(exist_ok=True)
URLS = HERE / "br_active_product_urls_100k.txt"
LOG = HERE / "br_remint_scale_run.log"
KEY = HERE / ".capsolver_key.local"
TARGET = 1000
MIN_KEYS = 40
CITIES = [
    ("Rio de Janeiro", "Rio de Janeiro"),
    ("São Paulo", "Barueri"),
    ("São Paulo", "Campinas"),
    ("Minas Gerais", "Belo Horizonte"),
    ("Paraná", "Curitiba"),
]


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def n_ok() -> int:
    return sum(1 for p in DELIV.glob("output_*.json") if re.match(r"output_\d+_\d+\.json", p.name))


def ensure_key() -> None:
    if not os.environ.get("CAPSOLVER_API_KEY") and KEY.is_file():
        os.environ["CAPSOLVER_API_KEY"] = KEY.read_text(encoding="utf-8").strip()


def rotate(n: int = 40) -> None:
    def sid():
        return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))

    lines = []
    try:
        _, proxies, _ = parse_br_test(BR_TEST)
        lines.extend(proxies)
    except Exception:
        pass
    for i in range(n):
        st, city = CITIES[i % len(CITIES)]
        user = f"gdxc12191-region-BR-st-{st}-city-{city}-sid-{sid()}-t-120"
        lines.append(f"us.1024proxy.io:3000:{user}:qadnrubc")
    (ROOT / "proxy(BR shopee).txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()


def base_jar() -> dict:
    ck, _, _ = parse_br_test(BR_TEST)
    # strip login + cert — virgin remint
    out = {k: v for k, v in ck.items() if k not in ("SPC_U", "AC_CERT_D", "AC_CERT_S", "AC_CERT_A") and v}
    return enforce_br_locale(normalize_br_cookies(out, fresh_cert=False))


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
        "token": "br_remint_scale",
        "api_key": "br_remint_scale",
        "bff_meta": {"extract_method": "browser_get_pc_remint"},
        "error": None,
        "error_msg": None,
        "data": data,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return True


def mint_device() -> dict | None:
    """Return login-less jar with fresh AC_CERT_D or None."""
    rotate(30)
    base = base_jar()
    log("mint attempt…")
    res = mint.mint("BR", base, save_shots=False, verbose=True)
    ck = res.get("cookies") or {}
    ok = bool(res.get("ok") and ck.get("AC_CERT_D"))
    # Also accept if get_pc passed during mint
    if not ok and ck.get("AC_CERT_D") and res.get("getpc_err") in (0, None) and res.get("ok"):
        ok = True
    log(f"mint ok={res.get('ok')} reason={res.get('reason')} cert={bool(ck.get('AC_CERT_D'))} getpc={res.get('getpc_err')}")
    if not ck.get("AC_CERT_D"):
        return None
    # strip login always
    jar = {k: v for k, v in ck.items() if k != "SPC_U" and v}
    name = f"remint_{int(time.time())}"
    (DEV / f"{name}.json").write_text(
        json.dumps({"cookies": jar, "ts": time.time(), "anon": True}, ensure_ascii=False),
        encoding="utf-8",
    )
    log(f"device saved {name}")
    return jar


def harvest(jar: dict, urls: list[str], quota: int = 50) -> dict:
    from patchright.sync_api import sync_playwright

    jar = enforce_br_locale(normalize_br_cookies({k: v for k, v in jar.items() if k != "SPC_U"}, fresh_cert=False))
    if not jar.get("AC_CERT_D"):
        return {"ok": 0, "fatal": True, "reason": "no_cert"}

    # Keep SAME proxy sticky — do not rotate mid-session
    proxy, _, sub = mint._proxy_dict("BR")
    stats = {"ok": 0, "fail": 0, "fatal": False, "reason": None}
    got = {"n": 0}
    log(f"HARVEST cert=Y SPC_U=N n={len(urls)} sub=...{(sub or '')[-36:]}")

    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="brrs_")
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
                if err == 90309999:
                    stats["fatal"] = True
                    stats["reason"] = "need_app_sign"
                elif err in (0, None) and keys >= MIN_KEYS:
                    got["n"] += 1
                    if save_full(j, r.url):
                        stats["ok"] += 1
                        log(f"OK +1 total={n_ok()}")
                    else:
                        stats["ok"] += 1
            except Exception:
                pass

        page.on("response", on_resp)
        human.warm_home(page, "shopee.com.br")
        for _ in range(5):
            u = page.url or ""
            if "/verify/traffic/error" in u:
                stats["fatal"] = True
                stats["reason"] = "traffic_error_home"
                return stats
            if "/verify/captcha" in u:
                try:
                    import captcha_playwright as cpw
                    cpw.solve_captcha_page(page, dom="shopee.com.br", gen_flag=None)
                except Exception as e:
                    log(f"cap {e}")
                    stats["fatal"] = True
                    stats["reason"] = "captcha"
                    return stats
            elif "/verify/traffic" in u:
                mint._wait_traffic_pass(page, "shopee.com.br", secs=28)
            else:
                break
            page.wait_for_timeout(700)

        for i, url in enumerate(urls):
            if n_ok() >= TARGET or got["n"] >= quota or stats["fatal"]:
                break
            before = got["n"]
            log(f"goto[{i}] …{url[-40:]}")
            try:
                page.goto(url, wait_until="commit", timeout=50000)
            except Exception as e:
                log(f"goto {type(e).__name__}")
            deadline = time.time() + 48
            while time.time() < deadline:
                u = page.url or ""
                if "/verify/traffic/error" in u or "/verify/captcha" in u:
                    stats["fatal"] = True
                    stats["reason"] = "wall"
                    break
                if "/verify/traffic" in u:
                    mint._wait_traffic_pass(page, "shopee.com.br", secs=20)
                if got["n"] > before:
                    break
                page.wait_for_timeout(350)
            if stats["fatal"]:
                break
            if got["n"] == before:
                stats["fail"] += 1
                if stats["fail"] >= 3:
                    stats["fatal"] = True
                    stats["reason"] = stats.get("reason") or "cold"
                    break
            else:
                stats["fail"] = 0
            time.sleep(random.uniform(0.5, 1.2))

        log(f"harvest done ok={stats['ok']} fatal={stats['fatal']}/{stats['reason']} total={n_ok()}")
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
    ensure_key()
    log(f"REMINT_SCALE start n={n_ok()}/{TARGET} cap={'Y' if os.environ.get('CAPSOLVER_API_KEY') else 'N'}")
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
    idle = 0
    while n_ok() < TARGET and cursor < len(pending) and idle < 25:
        jar = mint_device()
        if not jar:
            idle += 1
            log(f"mint fail idle={idle} — rotate & retry")
            time.sleep(15 + idle * 5)
            continue
        chunk = pending[cursor : cursor + 60]
        cursor += 60
        stats = harvest(jar, chunk, quota=45)
        if stats["ok"] > 0:
            idle = 0
            # same cert again if not fatal
            if not stats.get("fatal"):
                cursor -= max(0, 60 - stats["ok"])
                time.sleep(8)
            else:
                time.sleep(20)
        else:
            idle += 1
            time.sleep(25 + idle * 8)
        log(f"progress {n_ok()}/{TARGET} idle={idle}")
    final = n_ok()
    log(f"DONE n={final}")
    print(f"FINAL_COUNT={final}")
    return 0 if final >= TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
