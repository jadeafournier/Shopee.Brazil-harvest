# -*- coding: utf-8 -*-
"""Continuous BR get_pc harvest loop until 1000 unique deliverables."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DELIV = ROOT / "br_deliverable_getpc"
DELIV.mkdir(exist_ok=True)
LOG = ROOT / "br_continuous_1k_run.log"
JAR = Path(r"D:\emu\shopee_signer\target\shopee-signer-1.0.0.jar")
SO = Path(r"D:\emu\shopee_signer\libs\libshpssdk.so")
JDK = Path(r"D:\emu\jdk\jdk-17.0.19+10\bin\java.exe")
URLS = ROOT / "br_active_product_urls_100k.txt"
BR_TEST = ROOT / "BR TEST.txt"
TARGET = 1000


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def count_deliv() -> int:
    return len(list(DELIV.glob("output_*.json")))


def existing_ids() -> set[tuple[str, str]]:
    out = set()
    for f in DELIV.glob("output_*.json"):
        m = re.match(r"output_(\d+)_(\d+)\.json", f.name)
        if m:
            out.add((m.group(1), m.group(2)))
    return out


def load_proxies() -> list[str]:
    text = BR_TEST.read_text(encoding="utf-8", errors="replace")
    proxies = []
    for line in text.split("Proxy:")[-1].split("Test")[0].splitlines():
        line = line.strip()
        if not line or "us.1024" not in line:
            continue
        h, po, u, pw = line.split(":", 3)
        proxies.append(f"http://{u}:{pw}@{h}:{po}")
    return proxies


def parse_url(u: str) -> tuple[str, str] | None:
    m = re.search(r"i\.(\d+)\.(\d+)", u) or re.search(r"/product/(\d+)/(\d+)", u)
    if not m:
        return None
    return m.group(1), m.group(2)


def sign(path: str) -> dict | None:
    if not JAR.is_file():
        return None
    p = subprocess.run(
        [str(JDK), "-jar", str(JAR), str(SO), path],
        cwd=str(JAR.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    m = re.search(r"\[signer\] RESULT=(.+)", p.stdout)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def try_signed(shop: str, item: str, proxy: str) -> bool:
    import requests

    path = f"/api/v4/pdp/get_pc?shop_id={shop}&item_id={item}"
    sig = sign(path)
    if not sig:
        return False
    headers = {
        "User-Agent": "okhttp/4.9.2",
        "Accept": "application/json",
        "X-API-SOURCE": "rn",
        "X-Shopee-Language": "pt-BR",
        "sdkVersion": "6200",
        "os": "android",
        "osver": "30",
        "model": "Pixel 4",
        "brand": "google",
        "appId": "1001",
        "shopee-traceid": str(uuid.uuid4()),
        **sig,
    }
    longest = max(sig, key=lambda k: len(str(sig[k])))
    headers["x-sap-sec"] = sig[longest]
    try:
        r = requests.get(
            "https://shopee.com.br" + path,
            headers=headers,
            proxies={"http": proxy, "https": proxy},
            timeout=40,
        )
        j = r.json()
    except Exception as e:
        log(f"req fail {shop}.{item} {e}")
        return False
    data = j.get("data") if isinstance(j, dict) else None
    if isinstance(data, dict) and "item" in data and j.get("error") in (0, None):
        out = DELIV / f"output_{shop}_{item}.json"
        out.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
        log(f"OK {out.name} n={count_deliv()}")
        return True
    log(f"no {shop}.{item} err={j.get('error') if isinstance(j, dict) else None}")
    return False


def try_browser_batch(urls: list[str], proxy_server: str) -> int:
    """Patchright SPA intercept; return number saved."""
    try:
        from patchright.sync_api import sync_playwright
    except Exception as e:
        log(f"no patchright {e}")
        return 0
    saved = 0
    have = existing_ids()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            proxy={"server": proxy_server},
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
        page = ctx.new_page()
        bag: dict = {}

        def on_resp(r):
            try:
                if "get_pc" not in r.url or r.status != 200:
                    return
                j = r.json()
                data = j.get("data") or {}
                if not isinstance(data, dict) or "item" not in data:
                    return
                if j.get("error") not in (0, None):
                    return
                item = data["item"]
                shop = str(item.get("shop_id") or item.get("shopid") or "")
                it = str(item.get("item_id") or item.get("itemid") or "")
                if not shop or not it or (shop, it) in have:
                    return
                out = DELIV / f"output_{shop}_{it}.json"
                payload = {
                    "task_link": r.url,
                    "error": j.get("error"),
                    "error_msg": j.get("error_msg"),
                    "data": data,
                }
                # keep full envelope if present
                for k in ("token", "api_key", "bff_meta"):
                    if k in j:
                        payload[k] = j[k]
                out.write_text(json.dumps({**j, **payload}, ensure_ascii=False), encoding="utf-8")
                have.add((shop, it))
                bag["n"] = bag.get("n", 0) + 1
                log(f"SPA_OK {out.name} n={count_deliv()}")
            except Exception:
                pass

        page.on("response", on_resp)
        try:
            page.goto("https://shopee.com.br/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
        except Exception as e:
            log(f"home fail {e}")
        for u in urls:
            if count_deliv() >= TARGET:
                break
            try:
                page.goto(u, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(8000)
            except Exception as e:
                log(f"goto fail {e}")
                continue
        saved = bag.get("n", 0)
        browser.close()
    return saved


def main() -> int:
    log(f"start n={count_deliv()} target={TARGET}")
    proxies = load_proxies()
    urls = []
    have = existing_ids()
    for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        ids = parse_url(line)
        if not ids or ids in have:
            continue
        urls.append((line, ids[0], ids[1]))
    log(f"pending urls {len(urls)} proxies {len(proxies)}")
    pi = 0
    i = 0
    while count_deliv() < TARGET:
        # rotate: browser chunk then signed probes
        chunk = [u[0] for u in urls[i : i + 5]]
        if chunk:
            proxy = proxies[pi % len(proxies)]
            # patchright wants server without auth embedded sometimes — use full URL form
            m = re.match(r"http://([^:]+):([^@]+)@([^:]+):(\d+)", proxy)
            if m:
                user, pwd, host, port = m.groups()
                server = f"http://{host}:{port}"
                # playwright proxy auth
                try:
                    from patchright.sync_api import sync_playwright

                    have = existing_ids()
                    with sync_playwright() as p:
                        browser = p.chromium.launch(
                            headless=True,
                            proxy={"server": server, "username": user, "password": pwd},
                        )
                        ctx = browser.new_context(locale="pt-BR", timezone_id="America/Sao_Paulo")
                        page = ctx.new_page()
                        bag = {"n": 0}

                        def on_resp(r):
                            try:
                                if "get_pc" not in r.url or r.status != 200:
                                    return
                                j = r.json()
                                data = j.get("data") or {}
                                if not isinstance(data, dict) or "item" not in data:
                                    return
                                if j.get("error") not in (0, None):
                                    return
                                item = data["item"]
                                shop = str(item.get("shop_id") or item.get("shopid") or "")
                                it = str(item.get("item_id") or item.get("itemid") or "")
                                if not shop or (shop, it) in have:
                                    return
                                out = DELIV / f"output_{shop}_{it}.json"
                                out.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
                                have.add((shop, it))
                                bag["n"] += 1
                                log(f"SPA_OK {out.name} n={count_deliv()}")
                            except Exception:
                                pass

                        page.on("response", on_resp)
                        for u in chunk:
                            if count_deliv() >= TARGET:
                                break
                            try:
                                page.goto(u, wait_until="domcontentloaded", timeout=45000)
                                page.wait_for_timeout(10000)
                            except Exception as e:
                                log(f"spa goto {e}")
                        browser.close()
                    log(f"spa chunk saved={bag['n']} n={count_deliv()}")
                except Exception as e:
                    log(f"spa err {e}")
            # Unidbg signed attempts on same chunk
            for u, shop, item in urls[i : i + 5]:
                if count_deliv() >= TARGET:
                    break
                if (shop, item) in existing_ids():
                    continue
                try_signed(shop, item, proxies[pi % len(proxies)])
                time.sleep(0.5)
        i += 5
        pi += 1
        if i >= len(urls):
            i = 0
            log("url wrap")
            time.sleep(5)
        time.sleep(1)
    log(f"DONE n={count_deliv()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
