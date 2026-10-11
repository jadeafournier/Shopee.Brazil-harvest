# -*- coding: utf-8 -*-
"""Anonymous full get_pc: KEEP AC_CERT_D, STRIP SPC_U. Proven unlock path."""
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
DELIV.mkdir(exist_ok=True)
DEV = HERE / "br_device_sessions"
DEV.mkdir(exist_ok=True)
SESS = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
URLS = HERE / "br_active_product_urls_100k.txt"
LOG = HERE / "br_anon_cert_run.log"
TARGET = 1000
MIN_KEYS = 40
LOGIN_KEYS = ("SPC_U",)
KEY_FILE = HERE / ".capsolver_key.local"

CITIES = [
    ("Rio de Janeiro", "Rio de Janeiro"),
    ("São Paulo", "Barueri"),
    ("São Paulo", "Campinas"),
    ("Minas Gerais", "Belo Horizonte"),
]


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def ensure_cap() -> None:
    if not os.environ.get("CAPSOLVER_API_KEY") and KEY_FILE.is_file():
        os.environ["CAPSOLVER_API_KEY"] = KEY_FILE.read_text(encoding="utf-8").strip()


def n_ok() -> int:
    return sum(1 for p in DELIV.glob("output_*.json") if re.match(r"output_\d+_\d+\.json", p.name))


def rotate(n: int = 30) -> None:
    def sid():
        return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))

    lines = []
    for i in range(n):
        st, city = CITIES[i % len(CITIES)]
        user = f"gdxc12191-region-BR-st-{st}-city-{city}-sid-{sid()}-t-120"
        lines.append(f"us.1024proxy.io:3000:{user}:qadnrubc")
    # Prefer BR TEST proxies too
    try:
        _, proxies, _ = parse_br_test(BR_TEST)
        for px in proxies:
            if px not in lines:
                lines.insert(0, px)
    except Exception:
        pass
    (ROOT / "proxy(BR shopee).txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()


def strip_login(jar: dict) -> dict:
    return {k: v for k, v in jar.items() if k not in LOGIN_KEYS and v}


def collect_devices() -> list[tuple[str, dict]]:
    """Return (id, cookies_without_SPC_U) with AC_CERT_D present."""
    out: list[tuple[str, dict]] = []
    seen: set[str] = set()

    def add(name: str, jar: dict) -> None:
        if not jar.get("AC_CERT_D"):
            return
        key = jar["AC_CERT_D"][:56]
        if key in seen:
            return
        seen.add(key)
        dev = enforce_br_locale(normalize_br_cookies(strip_login(jar), fresh_cert=False))
        if not dev.get("AC_CERT_D"):
            return
        out.append((name, dev))

    try:
        ck, _, _ = parse_br_test(BR_TEST)
        add("br_test", ck)
    except Exception as e:
        log(f"br_test skip {e}")

    for p in sorted(DEV.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            add(p.stem, doc.get("cookies") or doc)
        except Exception:
            pass

    for p in sorted(SESS.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        if "pool_state" in p.name:
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            add(p.stem, doc.get("cookies") or doc)
        except Exception:
            pass
    return out


def persist_device(name: str, jar: dict) -> None:
    path = DEV / f"{name}.json"
    path.write_text(
        json.dumps({"cookies": strip_login(jar), "ts": time.time(), "anon": True}, ensure_ascii=False),
        encoding="utf-8",
    )


def mint_fresh() -> bool:
    """Mint new AC_CERT using a base jar, persist as anon device (no SPC_U)."""
    ensure_cap()
    base = None
    try:
        ck, _, _ = parse_br_test(BR_TEST)
        base = dict(ck)
    except Exception:
        pass
    if not base:
        for p in SESS.glob("br_*_live.json"):
            doc = json.loads(p.read_text(encoding="utf-8"))
            base = dict(doc.get("cookies") or {})
            if base.get("SPC_F"):
                break
    if not base:
        log("mint: no base jar")
        return False
    # mint with login present often helps traffic pass; strip after
    log("minting fresh AC_CERT…")
    res = mint.mint("BR", base, save_shots=False, verbose=True)
    log(f"mint ok={res.get('ok')} reason={res.get('reason')} cert={bool((res.get('cookies') or {}).get('AC_CERT_D'))}")
    if not res.get("ok"):
        return False
    jar = strip_login(res["cookies"])
    name = f"anon_mint_{int(time.time())}"
    persist_device(name, jar)
    log(f"persisted {name}")
    return True


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
        "token": "br_anon_cert",
        "api_key": "br_anon_cert",
        "bff_meta": {"extract_method": "browser_get_pc_anon_cert"},
        "error": None,
        "error_msg": None,
        "data": data,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return True


def burst(dev_id: str, jar: dict, urls: list[str], quota: int = 60) -> dict:
    from patchright.sync_api import sync_playwright

    # Anonymous: strip login. Remint cert on THIS browser fingerprint
    # (stale AC_CERT_D from another UA/IP → 90309999).
    jar = enforce_br_locale(normalize_br_cookies(strip_login(jar), fresh_cert=False))
    for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
        jar.pop(k, None)

    rotate(28)
    proxy, _, sub = mint._proxy_dict("BR")
    stats = {"ok": 0, "fail": 0, "fatal": False, "reason": None, "jar": dict(jar)}
    got = {"n": 0}
    log(f"BURST {dev_id} SPC_U=N remint_cert n={len(urls)} sub=...{(sub or '')[-36:]}")

    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="bran_")
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
                    log("FATAL need_app_sign")
                elif err in (0, None) and keys >= MIN_KEYS:
                    got["n"] += 1
                    if save_full(j, r.url):
                        stats["ok"] += 1
                        log(f"OK +1 shift={got['n']} total={n_ok()}")
                    else:
                        stats["ok"] += 1
                        log(f"DUP shift={got['n']} total={n_ok()}")
            except Exception:
                pass

        page.on("response", on_resp)
        try:
            human.warm_home(page, "shopee.com.br")
        except Exception as e:
            log(f"warm {e}")
        # Remint window: linger on home/search until AC_CERT_D appears
        remint_deadline = time.time() + 75
        while time.time() < remint_deadline:
            u = page.url or ""
            ck_now = {c["name"]: c["value"] for c in ctx.cookies()}
            if ck_now.get("AC_CERT_D"):
                jar.update(strip_login(ck_now))
                log("reminted AC_CERT_D on this fingerprint")
                break
            if "/verify/traffic/error" in u:
                stats["fatal"] = True
                stats["reason"] = "traffic_error_home"
                return stats
            if "/verify/captcha" in u:
                try:
                    import captcha_playwright as cpw
                    sol = cpw.solve_captcha_page(page, dom="shopee.com.br", gen_flag=None)
                    log(f"captcha {sol.get('ok')}")
                except Exception as e:
                    log(f"captcha {e}")
                    stats["fatal"] = True
                    stats["reason"] = "captcha"
                    return stats
            elif "/verify/traffic" in u:
                mint._wait_traffic_pass(page, "shopee.com.br", secs=28)
            else:
                # nudge SPA / anti-bot to issue cert
                try:
                    page.goto("https://shopee.com.br/", wait_until="commit", timeout=40000)
                except Exception:
                    pass
            page.wait_for_timeout(900)
        else:
            ck_now = {c["name"]: c["value"] for c in ctx.cookies()}
            log(f"no remint cert yet keys={sorted(ck_now.keys())[:12]}")
            # continue anyway — some sessions sign without cookie visible yet

        for i, url in enumerate(urls):
            if n_ok() >= TARGET or got["n"] >= quota or stats["fatal"]:
                break
            before = got["n"]
            log(f"goto[{i}] …{url[-48:]}")
            try:
                page.goto(url, wait_until="commit", timeout=50000)
            except Exception as e:
                log(f"goto {type(e).__name__}")
            deadline = time.time() + 50
            while time.time() < deadline:
                u = page.url or ""
                if "/verify/captcha" in u:
                    try:
                        import captcha_playwright as cpw
                        cpw.solve_captcha_page(page, dom="shopee.com.br", gen_flag=None)
                    except Exception:
                        stats["fatal"] = True
                        stats["reason"] = "captcha"
                        break
                if "/verify/traffic/error" in u:
                    stats["fatal"] = True
                    stats["reason"] = "traffic_error"
                    break
                if "/verify/traffic" in u:
                    mint._wait_traffic_pass(page, "shopee.com.br", secs=22)
                    try:
                        page.goto(url, wait_until="commit", timeout=50000)
                    except Exception:
                        pass
                if got["n"] > before:
                    break
                page.wait_for_timeout(350)
            if stats["fatal"]:
                break
            if got["n"] == before:
                stats["fail"] += 1
                log(f"miss fail={stats['fail']}")
                if stats["fail"] >= 3 and stats["ok"] == 0:
                    stats["fatal"] = True
                    stats["reason"] = stats.get("reason") or "cold"
                    break
                if stats["fail"] >= 8:
                    break
            else:
                stats["fail"] = 0
                if stats["ok"] >= 12:
                    quota = max(quota, 90)
            try:
                human.idle(page, 0.5, 1.3)
            except Exception:
                time.sleep(0.7)

        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        jar.update(strip_login(ck))
        stats["jar"] = jar
        persist_device(dev_id if not dev_id.startswith("br_") else f"live_{dev_id}", jar)
        log(f"burst done ok={stats['ok']} fail={stats['fail']} fatal={stats['fatal']}/{stats['reason']} total={n_ok()}")
    except Exception as e:
        log(f"EXC {e}")
        stats["fatal"] = True
        stats["reason"] = str(e)[:60]
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
    ensure_cap()
    log(f"ANON_CERT start n={n_ok()}/{TARGET}")
    devices = collect_devices()
    log(f"devices={len(devices)} {[d[0] for d in devices]}")
    if not devices:
        if not mint_fresh():
            return 3
        devices = collect_devices()
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
    di = 0
    cooldown: dict[str, float] = {}

    while n_ok() < TARGET and cursor < len(pending) and idle < 40:
        # refresh device list periodically
        if idle and idle % 3 == 0:
            devices = collect_devices() or devices
        # pick device
        pick = None
        for _ in range(max(len(devices), 1)):
            if not devices:
                break
            name, jar = devices[di % len(devices)]
            di += 1
            if time.time() >= cooldown.get(name, 0):
                pick = (name, jar)
                break
        if not pick:
            log("all devices cooling — mint or sleep 20m")
            mint_fresh()
            devices = collect_devices()
            time.sleep(1200)
            continue

        name, jar = pick
        chunk = pending[cursor : cursor + 80]
        cursor += 80
        stats = burst(name, jar, chunk, quota=60)
        if stats.get("jar") and stats["ok"] > 0:
            # update in-memory
            for i, (n, _) in enumerate(devices):
                if n == name:
                    devices[i] = (name, stats["jar"])
                    break
        if stats["ok"] > 0:
            idle = 0
            di -= 1  # stick
            time.sleep(10)
        else:
            idle += 1
            reason = stats.get("reason") or ""
            cool = 2400 if reason == "need_app_sign" else 900
            cooldown[name] = time.time() + cool
            log(f"cool {name} {cool}s ({reason})")
            if reason == "need_app_sign":
                # try mint a replacement device rather than thrash
                mint_fresh()
                devices = collect_devices() or devices
                time.sleep(180)
            else:
                time.sleep(60 + idle * 20)
        log(f"progress {n_ok()}/{TARGET} idle={idle} cursor={cursor}")

    final = n_ok()
    log(f"DONE n={final}")
    print(f"FINAL_COUNT={final}")
    return 0 if final >= TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
