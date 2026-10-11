# -*- coding: utf-8 -*-
"""Parallel BR get_pc harvest to 1000 — soft risk, multi-account, multi-proxy."""
from __future__ import annotations

import json
import random
import re
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

HERE = Path(__file__).resolve().parent
DELIV = HERE / "br_deliverable_getpc"
DELIV.mkdir(exist_ok=True)
URLS = HERE / "br_active_product_urls_100k.txt"
SESS = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
LOG = HERE / "br_parallel_1k.log"
TARGET = 1000
WORKERS = 3

ACCOUNTS = [
    "br_1706801030_ydvUxsIK_live.json",
    "br_1706785017_CoOHjjSQ_live.json",
    "br_1706784820_OUk7hm0A_live.json",
]

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
    if out.exists():
        return False
    if "task_link" not in j:
        j = {**j, "task_link": fallback_url}
    out.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    return True


def worker(wid: int, account: str, url_slice: list[str]) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies, enforce_br_locale
    import br_human as human
    import captcha_mint as mint
    from patchright.sync_api import sync_playwright

    saved_total = 0
    p_acc = SESS / account
    if not p_acc.is_file():
        log(f"w{wid} missing {account}")
        return 0
    base = json.loads(p_acc.read_text(encoding="utf-8"))
    base_jar = dict(base.get("cookies") or base)
    i = 0
    while n_ok() < TARGET and i < len(url_slice):
        jar = dict(base_jar)
        if random.random() < 0.5:
            for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
                jar.pop(k, None)
        jar = enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
        proxy, _, _ = mint._proxy_dict("BR")
        ua = random.choice(UAS)
        chunk = url_slice[i : i + 12]
        i += 12
        log(f"w{wid} shift i={i} n={n_ok()} acc={account[:20]}")
        p = sync_playwright().start()
        udd = tempfile.mkdtemp(prefix=f"brw{wid}_")
        cx = br_context_kwargs()
        shift_saved = 0
        fails = 0
        try:
            ctx = p.chromium.launch_persistent_context(
                udd,
                channel="chrome",
                headless=False,
                proxy=proxy,
                viewport={"width": random.choice([1366, 1440, 1280]), "height": random.choice([768, 900])},
                locale=cx["locale"],
                timezone_id=cx["timezone_id"],
                user_agent=ua,
                extra_http_headers=cx["extra_http_headers"],
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
            page = ctx.new_page()

            def on_resp(r):
                nonlocal shift_saved, saved_total
                try:
                    if "get_pc" not in r.url or r.status != 200:
                        return
                    j = r.json()
                    if save_payload(j, r.url):
                        shift_saved += 1
                        saved_total += 1
                        log(f"w{wid} OK total={n_ok()}")
                except Exception:
                    pass

            page.on("response", on_resp)
            try:
                human.warm_home(page, "shopee.com.br")
            except Exception:
                pass
            for _ in range(2):
                if "/verify/traffic" in (page.url or ""):
                    try:
                        mint._wait_traffic_pass(page, "shopee.com.br", secs=20)
                    except Exception:
                        break
                else:
                    break
            for u in chunk:
                if n_ok() >= TARGET:
                    break
                if fails >= 3:
                    break
                before = shift_saved
                try:
                    page.goto(u, wait_until="commit", timeout=40000)
                except Exception:
                    fails += 1
                    continue
                deadline = time.time() + 22
                while time.time() < deadline:
                    if "/verify/captcha" in (page.url or ""):
                        fails = 99
                        break
                    if "/verify/traffic" in (page.url or ""):
                        try:
                            mint._wait_traffic_pass(page, "shopee.com.br", secs=15)
                        except Exception:
                            pass
                        fails += 1
                        break
                    if shift_saved > before:
                        fails = 0
                        break
                    try:
                        page.wait_for_timeout(350)
                    except Exception:
                        time.sleep(0.35)
                else:
                    if shift_saved == before:
                        fails += 1
                try:
                    human.idle(page, 0.4, 1.2)
                except Exception:
                    time.sleep(0.6)
            ck = {c["name"]: c["value"] for c in ctx.cookies()}
            if ck.get("SPC_U"):
                base_jar.update(ck)
                p_acc.write_text(json.dumps({"cookies": base_jar, "ts": time.time()}, ensure_ascii=False), encoding="utf-8")
            log(f"w{wid} done saved={shift_saved} fails={fails} n={n_ok()}")
            try:
                ctx.close()
            except Exception:
                pass
        except Exception as e:
            log(f"w{wid} exc {e}")
        finally:
            try:
                p.stop()
            except Exception:
                pass
        time.sleep(2 + random.random() * 3)
    return saved_total


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    log(f"parallel start n={n_ok()} workers={WORKERS}")
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
    # shard urls
    shards = [urls[i::WORKERS] for i in range(WORKERS)]
    accs = (ACCOUNTS * WORKERS)[:WORKERS]
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(worker, i, accs[i], shards[i]) for i in range(WORKERS)]
        for f in as_completed(futs):
            try:
                log(f"worker finished saved={f.result()}")
            except Exception as e:
                log(f"worker err {e}")
    log(f"DONE n={n_ok()}")
    return 0 if n_ok() >= TARGET else 1


if __name__ == "__main__":
    raise SystemExit(main())
