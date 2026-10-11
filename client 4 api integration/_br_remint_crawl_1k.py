# -*- coding: utf-8 -*-
"""Mint fresh AC_CERT per backup account, then crawl to 1000."""
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

HERE = Path(__file__).resolve().parent
DELIV = HERE / "br_deliverable_getpc"
DELIV.mkdir(exist_ok=True)
URLS = HERE / "br_active_product_urls_100k.txt"
SESS = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
LOG = HERE / "br_remint_crawl_run.log"
TARGET = 1000

ACCOUNTS = [
    "br_1706801030_ydvUxsIK_live.json",
    "br_1706785017_CoOHjjSQ_live.json",
    "br_1706784820_OUk7hm0A_live.json",
    "br_1707139756_0EAyNQaW_live.json",
    "br_1707408898_LAxNlL5a_live.json",
    "br_5196720216_hsRjFTxp_live.json",
    "br_1707407334_mRNUQARQ_live.json",
]

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def n_ok() -> int:
    return len(list(DELIV.glob("output_*.json")))


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


def save_payload(j, fallback_url) -> bool:
    data = j.get("data") or {}
    if not isinstance(data, dict) or "item" not in data:
        return False
    if j.get("error") not in (0, None):
        return False
    item = data["item"]
    shop = str(item.get("shop_id") or item.get("shopid") or "")
    it = str(item.get("item_id") or item.get("itemid") or "")
    if not shop:
        return False
    out = DELIV / f"output_{shop}_{it}.json"
    if out.exists():
        return False
    if "task_link" not in j:
        j = {**j, "task_link": fallback_url}
    out.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    return True


def remint(account: str) -> dict | None:
    from patchright.sync_api import sync_playwright

    p_acc = SESS / account
    if not p_acc.is_file():
        return None
    doc = json.loads(p_acc.read_text(encoding="utf-8"))
    jar = dict(doc.get("cookies") or doc)
    for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
        jar.pop(k, None)
    jar = enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
    proxy, _, _ = mint._proxy_dict("BR")
    ua = random.choice(UAS)
    log(f"remint {account} ua=...{ua[-18:]}")
    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="remint_")
    cx = br_context_kwargs()
    try:
        ctx = p.chromium.launch_persistent_context(
            udd,
            channel="chrome",
            headless=False,
            proxy=proxy,
            viewport={"width": random.choice([1366, 1440, 1536]), "height": random.choice([768, 864, 900])},
            locale=cx["locale"],
            timezone_id=cx["timezone_id"],
            user_agent=ua,
            extra_http_headers=cx["extra_http_headers"],
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
        page = ctx.new_page()
        try:
            human.warm_home(page, "shopee.com.br")
        except Exception as e:
            log(f"warm fail {e}")
        for _ in range(5):
            if "/verify/traffic" in (page.url or ""):
                try:
                    mint._wait_traffic_pass(page, "shopee.com.br", secs=28)
                except Exception:
                    break
            else:
                break
            try:
                page.wait_for_timeout(2000)
            except Exception:
                time.sleep(2)
        # poke a known product to force cert + validate get_pc
        test = "https://shopee.com.br/product/1006215031/24076819963"
        holder = {}

        def on_resp(r):
            try:
                if "get_pc" in r.url and r.status == 200:
                    holder["j"] = r.json()
            except Exception:
                pass

        page.on("response", on_resp)
        try:
            page.goto(test, wait_until="commit", timeout=45000)
        except Exception:
            pass
        deadline = time.time() + 30
        while time.time() < deadline and "j" not in holder:
            if "/verify/traffic" in (page.url or ""):
                try:
                    mint._wait_traffic_pass(page, "shopee.com.br", secs=20)
                except Exception:
                    break
            try:
                page.wait_for_timeout(400)
            except Exception:
                time.sleep(0.4)
        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        ok = False
        if "j" in holder:
            j = holder["j"]
            keys = len((j.get("data") or {}).get("item") or {})
            err = j.get("error")
            ok = keys > 0 and err in (0, None)
            if ok:
                save_payload(j, test)
            log(f"remint check err={err} keys={keys} cert={'Y' if ck.get('AC_CERT_D') else 'N'}")
        else:
            log(f"remint no get_pc cert={'Y' if ck.get('AC_CERT_D') else 'N'}")
        # save jar
        out = {**jar, **ck}
        p_acc.write_text(json.dumps({"cookies": out, "ts": time.time(), "reminted": True}, ensure_ascii=False), encoding="utf-8")
        try:
            ctx.close()
        except Exception:
            pass
        return out if (ok or ck.get("AC_CERT_D")) else None
    except Exception as e:
        log(f"remint exc {e}")
        return None
    finally:
        try:
            p.stop()
        except Exception:
            pass


def crawl_account(account: str, urls: list[str], quota: int = 80) -> int:
    from patchright.sync_api import sync_playwright

    p_acc = SESS / account
    doc = json.loads(p_acc.read_text(encoding="utf-8"))
    jar = dict(doc.get("cookies") or doc)
    jar = enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
    proxy, _, _ = mint._proxy_dict("BR")
    ua = random.choice(UAS)
    saved = 0
    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="crawl_")
    cx = br_context_kwargs()
    try:
        ctx = p.chromium.launch_persistent_context(
            udd,
            channel="chrome",
            headless=False,
            proxy=proxy,
            viewport={"width": 1366, "height": 768},
            locale=cx["locale"],
            timezone_id=cx["timezone_id"],
            user_agent=ua,
            extra_http_headers=cx["extra_http_headers"],
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
        page = ctx.new_page()
        fails = 0

        def on_resp(r):
            nonlocal saved
            try:
                if "get_pc" not in r.url or r.status != 200:
                    return
                if save_payload(r.json(), r.url):
                    saved += 1
                    log(f"OK {account[:18]} saved={saved} total={n_ok()}")
            except Exception:
                pass

        page.on("response", on_resp)
        try:
            human.warm_home(page, "shopee.com.br")
        except Exception:
            pass
        for u in urls:
            if n_ok() >= TARGET or saved >= quota or fails >= 4:
                break
            before = saved
            try:
                page.goto(u, wait_until="commit", timeout=40000)
            except Exception:
                fails += 1
                continue
            deadline = time.time() + 24
            while time.time() < deadline:
                if "/verify/captcha" in (page.url or ""):
                    fails = 99
                    break
                if "/verify/traffic" in (page.url or ""):
                    try:
                        mint._wait_traffic_pass(page, "shopee.com.br", secs=16)
                    except Exception:
                        pass
                    fails += 1
                    break
                if saved > before:
                    fails = 0
                    break
                try:
                    page.wait_for_timeout(350)
                except Exception:
                    time.sleep(0.35)
            else:
                if saved == before:
                    fails += 1
            try:
                human.idle(page, 0.5, 1.5)
            except Exception:
                time.sleep(0.8)
        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        jar.update(ck)
        p_acc.write_text(json.dumps({"cookies": jar, "ts": time.time()}, ensure_ascii=False), encoding="utf-8")
        try:
            ctx.close()
        except Exception:
            pass
    finally:
        try:
            p.stop()
        except Exception:
            pass
    return saved


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    log(f"remint-crawl start n={n_ok()}")
    have = have_ids()
    urls = []
    for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        ids = parse_ids(line)
        if not ids or ids in have:
            continue
        urls.append(line)
    log(f"pending {len(urls)}")
    round_i = 0
    while n_ok() >= 0 and n_ok() < TARGET:
        round_i += 1
        log(f"=== round {round_i} n={n_ok()} ===")
        live = []
        for acc in ACCOUNTS:
            if n_ok() >= TARGET:
                break
            jar = remint(acc)
            if jar:
                live.append(acc)
            time.sleep(3)
        log(f"live accounts {live}")
        # crawl each live account with a shard
        for i, acc in enumerate(live or ACCOUNTS):
            if n_ok() >= TARGET:
                break
            shard = urls[i :: max(1, len(live or ACCOUNTS))]
            # rotate offset each round
            off = (round_i * 50 + i * 17) % max(1, len(shard))
            shard = shard[off:] + shard[:off]
            saved = crawl_account(acc, shard[:60], quota=40)
            log(f"crawl {acc} saved={saved} n={n_ok()}")
            time.sleep(5)
        if not live:
            log("no live — sleep 60")
            time.sleep(60)
    log(f"DONE n={n_ok()}")
    return 0 if n_ok() >= TARGET else 1


if __name__ == "__main__":
    raise SystemExit(main())
