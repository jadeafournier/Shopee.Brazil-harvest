# -*- coding: utf-8 -*-
"""HTTP scale worker — curl_cffi + signer_service → full get_pc envelopes.

Throughput path for 1M/day AFTER signer returns valid x-sap-* / af-ac-* headers.
Without a live signer, this documents the pipeline and exits non-zero on 90309999.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "shopee_getpc_crawler"), str(HERE)]

from _br_probe import parse_br_test, BR_TEST

DELIV = HERE / "br_deliverable_getpc"
DELIV.mkdir(exist_ok=True)
URLS = HERE / "br_active_product_urls_100k.txt"
LOG = Path(__file__).resolve().parent / "worker_http.log"
MIN_KEYS = 40
API = "https://shopee.com.br/api/v4/pdp/get_pc"


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


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


def proxy_url(line: str) -> Optional[str]:
    parts = line.strip().split(":")
    if len(parts) < 4:
        return None
    host, port, user = parts[0], parts[1], parts[2]
    pwd = ":".join(parts[3:])
    return f"http://{quote(user)}:{quote(pwd)}@{host}:{port}"


def ask_sign(signer: str, url: str) -> dict:
    import urllib.request
    req = urllib.request.Request(
        signer.rstrip("/") + "/sign",
        data=json.dumps({"url": url, "method": "GET"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "reason": str(e), "headers": {}}


def save_full(j: dict, task_link: str) -> bool:
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
        "task_link": task_link,
        "token": "br_scale_1m",
        "api_key": "br_scale_1m",
        "bff_meta": {"extract_method": "signed_http_get_pc"},
        "error": None,
        "error_msg": None,
        "data": data,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return True


def fetch_one(shop: str, item: str, *, signer: str, proxy: Optional[str], cookies: dict) -> dict:
    from curl_cffi import requests as creq

    path = f"/api/v4/pdp/get_pc?shop_id={shop}&item_id={item}"
    full = "https://shopee.com.br" + path
    sig = ask_sign(signer, path)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "X-API-SOURCE": "pc",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
        "Referer": f"https://shopee.com.br/product/{shop}/{item}",
    }
    headers.update(sig.get("headers") or {})
    try:
        r = creq.get(
            API,
            params={"shop_id": shop, "item_id": item},
            cookies=cookies,
            headers=headers,
            proxy=proxy,
            impersonate="chrome131",
            timeout=35,
        )
        j = r.json()
    except Exception as e:
        return {"ok": False, "reason": f"http:{e}"}
    err = j.get("error")
    item_o = ((j.get("data") or {}) if isinstance(j.get("data"), dict) else {}).get("item")
    keys = len(item_o) if isinstance(item_o, dict) else 0
    if err in (0, None) and keys >= MIN_KEYS:
        saved = save_full(j, f"https://shopee.com.br/product/{shop}/{item}")
        return {"ok": True, "keys": keys, "saved": saved, "signer_ok": sig.get("ok")}
    return {
        "ok": False,
        "reason": f"err={err} keys={keys} signer={sig.get('ok')}/{sig.get('reason')}",
        "error": err,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signer", default="http://127.0.0.1:8765")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--no-cookies", action="store_true", help="truly anonymous jar")
    args = ap.parse_args()

    try:
        from curl_cffi import requests as creq  # noqa: F401
    except ImportError:
        log("pip install curl_cffi required")
        return 2

    ck, proxies, _ = parse_br_test(BR_TEST)
    if args.no_cookies:
        ck = {}
    else:
        ck = {k: v for k, v in ck.items() if k != "SPC_U"}  # login-less

    px = proxy_url(proxies[0]) if proxies else None
    done = have()
    pending = []
    for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        ids = parse_ids(line)
        if ids and ids not in done:
            pending.append(ids)
        if len(pending) >= args.limit:
            break

    log(f"worker_http n_pending={len(pending)} workers={args.workers} signer={args.signer}")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(fetch_one, s, i, signer=args.signer, proxy=px, cookies=ck): (s, i)
            for s, i in pending
        }
        for fut in as_completed(futs):
            r = fut.result()
            if r.get("ok"):
                ok += 1
                log(f"OK keys={r.get('keys')} saved={r.get('saved')}")
            else:
                fail += 1
                log(f"FAIL {r.get('reason')}")
    log(f"DONE ok={ok} fail={fail} deliverable={len(list(DELIV.glob('output_*.json')))}")
    print(f"OK={ok} FAIL={fail}")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
