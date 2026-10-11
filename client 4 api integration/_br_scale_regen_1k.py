# -*- coding: utf-8 -*-
"""Scale the proven cert-regen get_pc path until 1000 deliverables."""
from __future__ import annotations

import json
import random
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies, enforce_br_locale
import br_human as human
import captcha_mint as mint

DOM = "shopee.com.br"
HERE = Path(__file__).resolve().parent
DELIV = HERE / "br_deliverable_getpc"
DELIV.mkdir(exist_ok=True)
URLS = HERE / "br_active_product_urls_100k.txt"
SESS = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
LOG = HERE / "br_scale_regen.log"
TARGET = 1000

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def n_ok() -> int:
    return len(list(DELIV.glob("output_*.json")))


def have_ids() -> set[tuple[str, str]]:
    s = set()
    for f in DELIV.glob("output_*.json"):
        m = re.match(r"output_(\d+)_(\d+)\.json", f.name)
        if m:
            s.add((m.group(1), m.group(2)))
    return s


def parse_ids(u: str):
    m = re.search(r"i\.(\d+)\.(\d+)", u) or re.search(r"/product/(\d+)/(\d+)", u)
    return (m.group(1), m.group(2)) if m else None


def load_jar() -> dict:
    # Prefer backup accounts with SPC_U (proven get_pc in cert_regen)
    for name in [
        "br_1706801030_ydvUxsIK_live.json",
        "br_1706785017_CoOHjjSQ_live.json",
        "br_1706784820_OUk7hm0A_live.json",
        "br_scale_live.json",
        "br_client4_live.json",
    ]:
        p = SESS / name
        if p.is_file():
            doc = json.loads(p.read_text(encoding="utf-8"))
            ck = doc.get("cookies") or doc
            if ck.get("SPC_U") or ck.get("AC_CERT_D"):
                log(f"jar {name}")
                return dict(ck)
    mints = sorted(SESS.glob("br_mint_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in mints[:3]:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            ck = doc.get("cookies") or doc
            if ck.get("AC_CERT_D") or ck.get("SPC_F"):
                log(f"jar {p.name}")
                return dict(ck)
        except Exception:
            pass
    text = (HERE / "BR TEST.txt").read_text(encoding="utf-8", errors="replace")
    line = text.split("Cookie:")[1].split("Proxy:")[0].strip().splitlines()[0]
    jar = {}
    for part in line.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            jar[k] = v
    return jar


def save_payload(j: dict, fallback_url: str) -> bool:
    data = j.get("data") or {}
    if not isinstance(data, dict) or "item" not in data:
        return False
    if j.get("error") not in (0, None):
        return False
    item = data["item"]
    shop = str(item.get("shop_id") or item.get("shopid") or "")
    it = str(item.get("item_id") or item.get("itemid") or "")
    if not shop or not it:
        return False
    out = DELIV / f"output_{shop}_{it}.json"
    if "task_link" not in j:
        j = {**j, "task_link": fallback_url}
    out.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    return True


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    log(f"start n={n_ok()}")
    urls = []
    have = have_ids()
    for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        ids = parse_ids(line)
        if not ids or ids in have:
            continue
        urls.append(line)
    log(f"pending {len(urls)}")

    from patchright.sync_api import sync_playwright

    idx = 0
    while n_ok() < TARGET and idx < len(urls):
        jar = load_jar()
        # Keep login if present — proven path for get_pc (cert_regen).
        keep_login = bool(jar.get("SPC_U"))
        if not keep_login:
            for k in ("SPC_U",):
                jar.pop(k, None)
        # Only strip certs when we have login to remint
        if keep_login and random.random() < 0.4:
            for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
                jar.pop(k, None)
        jar = enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
        log(f"login={'Y' if jar.get('SPC_U') else 'N'} cert={'Y' if jar.get('AC_CERT_D') else 'N'}")
        proxy, _, _ = mint._proxy_dict("BR")
        ua = random.choice(UAS)
        chunk = urls[idx : idx + 25]
        idx += 25
        log(f"shift idx={idx} chunk={len(chunk)} proxy={str(proxy)[:40]} n={n_ok()}")
        p = sync_playwright().start()
        udd = tempfile.mkdtemp(prefix="br1k_")
        cx = br_context_kwargs()
        try:
            ctx = p.chromium.launch_persistent_context(
                udd,
                channel="chrome",
                headless=False,
                proxy=proxy,
                viewport={"width": random.choice([1366, 1440, 1280]), "height": random.choice([768, 900, 800])},
                locale=cx["locale"],
                timezone_id=cx["timezone_id"],
                user_agent=ua,
                extra_http_headers=cx["extra_http_headers"],
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
            page = ctx.new_page()
            saved = 0
            risk = False

            def on_resp(r):
                nonlocal saved, risk
                try:
                    u = r.url
                    if "verify/traffic" in u:
                        risk = True
                    if "get_pc" not in u or r.status != 200:
                        return
                    j = r.json()
                    if save_payload(j, u):
                        saved += 1
                        log(f"OK saved={saved} total={n_ok()}")
                except Exception:
                    pass

            page.on("response", on_resp)
            try:
                human.warm_home(page, DOM)
            except Exception as e:
                log(f"warm {e}")
            for _ in range(3):
                if "/verify/traffic" in (page.url or ""):
                    mint._wait_traffic_pass(page, DOM, secs=25)
                else:
                    break
            for u in chunk:
                if n_ok() >= TARGET or risk:
                    break
                try:
                    page.goto(u, wait_until="commit", timeout=45000)
                except Exception:
                    pass
                deadline = time.time() + 28
                while time.time() < deadline:
                    if "/verify/captcha" in (page.url or ""):
                        risk = True
                        break
                    if "/verify/traffic" in (page.url or ""):
                        mint._wait_traffic_pass(page, DOM, secs=18)
                        risk = True
                        break
                    try:
                        page.wait_for_timeout(400)
                    except Exception:
                        time.sleep(0.4)
                try:
                    human.idle(page, 0.8, 2.2)
                except Exception:
                    time.sleep(1.2)
            # persist cookies for next shift
            ck = {c["name"]: c["value"] for c in ctx.cookies()}
            live = SESS / "br_scale_live.json"
            live.write_text(json.dumps({"cookies": ck, "ts": time.time()}, ensure_ascii=False), encoding="utf-8")
            log(f"shift done saved={saved} risk={risk} cert={'Y' if ck.get('AC_CERT_D') else 'N'} n={n_ok()}")
            try:
                ctx.close()
            except Exception:
                pass
        except Exception as e:
            log(f"shift exc {e}")
        finally:
            try:
                p.stop()
            except Exception:
                pass
        if risk:
            time.sleep(8)
        else:
            time.sleep(3)
    log(f"DONE n={n_ok()}")
    return 0 if n_ok() >= TARGET else 1


if __name__ == "__main__":
    raise SystemExit(main())
