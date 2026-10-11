# -*- coding: utf-8 -*-
"""BR Client 4 get_pc extraction — cookie-free first, cookie+oracle fallback."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))

BR_TEST = Path(__file__).resolve().parent / "BR TEST.txt"
OUT_DIR = Path(__file__).resolve().parent / "br_probe_output"
PROXY_FILE = ROOT / "proxy(BR shopee).txt"
SESSION_FILE = ROOT / "shopee_getpc_crawler" / "sessions" / "BR" / "br_client4_test.json"

UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UA_FB = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
UA_GBOT = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)
SHOPEE_BR = "https://shopee.com.br"


def parse_br_test(path: Path) -> Tuple[Dict[str, str], List[str], List[str]]:
    text = path.read_text(encoding="utf-8")
    cookies: Dict[str, str] = {}
    proxies: List[str] = []
    urls: List[str] = []
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower().rstrip(":")
        if low == "cookie":
            section = "cookie"
            rest = line.split(":", 1)
            if len(rest) > 1 and rest[1].strip():
                line = rest[1].strip()
            else:
                continue
        elif low == "proxy":
            section = "proxy"
            continue
        elif low.startswith("test url"):
            section = "url"
            continue
        if section == "cookie":
            for part in line.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookies[k.strip()] = v.strip()
        elif section == "proxy":
            proxies.append(line)
        elif section == "url":
            urls.append(line)
    return cookies, proxies, urls


def encode_proxy_url(line: str) -> str:
    """host:port:user:pass -> encoded http proxy URL."""
    host, port, user, pwd = line.split(":", 3)
    user_enc = quote(user, safe="")
    pwd_enc = quote(pwd, safe="")
    return f"http://{user_enc}:{pwd_enc}@{host}:{port}"


def parse_url_ids(url: str) -> Tuple[int, int]:
    m = re.search(r"-i\.(\d+)\.(\d+)", url)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"/product/(\d+)/(\d+)", url)
    if m:
        return int(m.group(1)), int(m.group(2))
    raise ValueError(f"cannot parse shop/item from {url}")


def summarize_data(data: dict) -> dict:
    item = (data or {}).get("item") or {}
    models = item.get("models") or []
    priced = sum(1 for m in models if isinstance(m, dict) and m.get("price"))
    return {
        "title": (item.get("title") or item.get("name") or "")[:80],
        "currency": item.get("currency"),
        "price": item.get("price"),
        "price_min": item.get("price_min"),
        "price_max": item.get("price_max"),
        "model_count": len(models),
        "priced_models": priced,
        "data_keys": len(data or {}),
        "item_keys": len(item),
    }


def get_pc_api_url(shop_id: int, item_id: int) -> str:
    return (
        f"{SHOPEE_BR}/api/v4/pdp/get_pc?display_model_id=0"
        f"&item_id={item_id}&model_selection_logic=3&shop_id={shop_id}"
        f"&tz_offset_in_minutes=180&detail_level=0"
    )


def get_pc_headers(shop_id: int, item_id: int) -> dict:
    return {
        "User-Agent": UA_CHROME,
        "Accept": "application/json",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Referer": f"{SHOPEE_BR}/product/{shop_id}/{item_id}",
        "x-api-source": "pc",
        "x-shopee-language": "pt",
        "x-requested-with": "XMLHttpRequest",
    }


def try_anon_get_pc(shop_id: int, item_id: int, proxy: Optional[str]) -> Tuple[dict, Optional[dict]]:
    from curl_cffi import requests as cr

    url = get_pc_api_url(shop_id, item_id)
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = cr.get(
            url,
            impersonate="chrome131",
            headers=get_pc_headers(shop_id, item_id),
            proxies=proxies,
            timeout=30,
        )
        body = r.json()
        err = body.get("error")
        data = body.get("data") or {}
        item = data.get("item") or {}
        ok = err in (0, None) and bool(item.get("item_id"))
        return {
            "method": "anon_get_pc_api",
            "http": r.status_code,
            "error": err,
            "error_msg": body.get("error_msg"),
            "ok": ok,
            "summary": summarize_data(data) if data else {},
        }, data if ok else None
    except Exception as exc:
        return {"method": "anon_get_pc_api", "ok": False, "exc": str(exc)[:200]}, None


def try_cookie_curl_getpc(
    shop_id: int, item_id: int, cookies: Dict[str, str], proxy: Optional[str]
) -> Tuple[dict, Optional[dict]]:
    from curl_cffi import requests as cr

    url = get_pc_api_url(shop_id, item_id)
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = cr.get(
            url,
            impersonate="chrome131",
            headers=get_pc_headers(shop_id, item_id),
            cookies=cookies,
            proxies=proxies,
            timeout=30,
        )
        body = r.json()
        err = body.get("error")
        data = body.get("data") or {}
        item = data.get("item") or {}
        ok = err in (0, None) and bool(item.get("item_id"))
        return {
            "method": "cookie_curl_getpc",
            "http": r.status_code,
            "error": err,
            "error_msg": body.get("error_msg"),
            "ok": ok,
            "summary": summarize_data(data) if data else {},
        }, data if ok else None
    except Exception as exc:
        return {"method": "cookie_curl_getpc", "ok": False, "exc": str(exc)[:200]}, None


def try_pdp_html(label: str, url: str, ua: str, proxy: Optional[str]) -> dict:
    from curl_cffi import requests as cr

    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = cr.get(
            url,
            impersonate="chrome131",
            headers={"User-Agent": ua, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
            proxies=proxies,
            timeout=30,
        )
        html = r.text or ""
        og_title = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        return {
            "method": label,
            "http": r.status_code,
            "len": len(html),
            "has_initialState": '"initialState":{' in html or "window.__INITIAL_STATE__" in html,
            "has_json_ld": "application/ld+json" in html,
            "has_offers": '"offers"' in html or '"price"' in html,
            "og_title": og_title.group(1)[:80] if og_title else None,
            "ok": r.status_code == 200 and len(html) > 5000,
        }
    except Exception as exc:
        return {"method": label, "ok": False, "exc": str(exc)[:200]}


def try_cookie_oracle(
    shop_id: int, item_id: int, cookies: Dict[str, str], proxy: Optional[str]
) -> Tuple[dict, Optional[dict]]:
    import getpc_client
    import proxy_pool

    proxy_pool.set_region("BR")
    proxy_pool.load()
    data, meta = getpc_client.fetch(
        shop_id, item_id, cookies, region="BR", proxy=proxy, retries=2
    )
    ok = meta.get("status") == "ok" and bool(data)
    return {
        "method": "cookie_sign_oracle",
        "meta": meta,
        "ok": ok,
        "summary": summarize_data(data) if data else {},
    }, data if ok else None


def extract_one(
    url: str,
    cookies: Dict[str, str],
    proxy_urls: List[str],
) -> Tuple[List[dict], Optional[dict], str]:
    shop_id, item_id = parse_url_ids(url)
    canon = f"{SHOPEE_BR}/product/{shop_id}/{item_id}"
    alt = f"{SHOPEE_BR}/-i.{shop_id}.{item_id}"
    results: List[dict] = []

    for pi, proxy in enumerate(proxy_urls[:5]):
        if pi:
            print(f"  retry proxy #{pi + 1} ...")

        r, data = try_anon_get_pc(shop_id, item_id, proxy)
        results.append(r)
        if data:
            return results, data, "anon_get_pc_api"

        for label, purl, ua in (
            ("curl_ssr_chrome", canon, UA_CHROME),
            ("og_ssr_facebook", canon, UA_FB),
            ("jsonld_googlebot", canon, UA_GBOT),
        ):
            results.append(try_pdp_html(label, purl, ua, proxy))

        r, data = try_cookie_curl_getpc(shop_id, item_id, cookies, proxy)
        results.append(r)
        if data:
            return results, data, "cookie_curl_getpc"

        r, data = try_cookie_oracle(shop_id, item_id, cookies, proxy)
        results.append(r)
        if data:
            return results, data, "cookie_sign_oracle"

    return results, None, ""


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    cookies, proxy_lines, urls = parse_br_test(BR_TEST)
    PROXY_FILE.write_text("\n".join(proxy_lines) + "\n", encoding="utf-8")
    proxy_urls = [encode_proxy_url(p) for p in proxy_lines]

    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(
        json.dumps(
            {"account_name": "br_client4_test", "region": "BR", "cookies": cookies},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(cookies)} cookies, {len(proxy_urls)} proxies, {len(urls)} URLs")
    print(f"Proxy file: {PROXY_FILE}")
    print(f"Session file: {SESSION_FILE}")
    print()

    saved = 0
    all_results = []

    for url in urls:
        shop_id, item_id = parse_url_ids(url)
        print("=" * 78)
        print(f"shop={shop_id} item={item_id}")
        print(f"URL: {url[:100]}...")

        results, data, method = extract_one(url, cookies, proxy_urls)
        for r in results:
            extra = {k: v for k, v in r.items() if k not in ("method", "ok")}
            print(f"  [{r.get('method')}] ok={r.get('ok')} {json.dumps(extra, ensure_ascii=False)[:320]}")

        if data:
            payload = {
                "task_link": url,
                "token": "br_client4_test",
                "api_key": "br_client4_test",
                "bff_meta": None,
                "error": None,
                "error_msg": None,
                "data": data,
            }
            out_path = OUT_DIR / f"output_{shop_id}_{item_id}.json"
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  SAVED via {method} -> {out_path}")
            saved += 1
        else:
            print("  FAILED all methods")

        all_results.append({
            "url": url, "shop_id": shop_id, "item_id": item_id,
            "method": method or None, "ok": bool(data), "results": results,
        })

    summary_path = OUT_DIR / "probe_summary.json"
    summary_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"Saved {saved}/{len(urls)} get_pc payloads to {OUT_DIR}")
    print(f"Summary -> {summary_path}")
    return 0 if saved == len(urls) else 1


if __name__ == "__main__":
    raise SystemExit(main())
