# -*- coding: utf-8 -*-
"""curl_cffi TLS-impersonation probe — proves JA3 alone cannot unlock BR get_pc.

Industry finding (tail-fin-shopee 2026): Chrome TLS + cookies still yield 90309999.
This probe documents that for our BR stack.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "shopee_getpc_crawler"), str(HERE)]

from _br_probe import parse_br_test, BR_TEST, parse_url_ids

URL = "https://shopee.com.br/api/v4/pdp/get_pc"


def main() -> int:
    try:
        from curl_cffi import requests as creq
    except ImportError:
        print("INSTALL: pip install curl_cffi")
        return 2

    ck, proxies, urls = parse_br_test(BR_TEST)
    shop, item = parse_url_ids(urls[0] if urls else "https://shopee.com.br/product/980944158/26142163515")
    params = {"shop_id": shop, "item_id": item}
    # strip login optional variants
    variants = {
        "full_jar": ck,
        "no_spc_u": {k: v for k, v in ck.items() if k != "SPC_U"},
        "minimal": {k: ck[k] for k in ("SPC_F", "csrftoken", "AC_CERT_D") if k in ck},
    }
    proxy = None
    if proxies:
        # host:port:user:pass → URL
        parts = proxies[0].split(":")
        if len(parts) >= 4:
            host, port, user, pwd = parts[0], parts[1], parts[2], ":".join(parts[3:])
            from urllib.parse import quote
            proxy = f"http://{quote(user)}:{quote(pwd)}@{host}:{port}"

    print("=== curl_cffi BR get_pc probe (TLS impersonate) ===")
    for name, jar in variants.items():
        try:
            r = creq.get(
                URL,
                params=params,
                cookies=jar,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "X-API-SOURCE": "pc",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"https://shopee.com.br/product/{shop}/{item}",
                    "Accept": "application/json",
                },
                proxy=proxy,
                impersonate="chrome131",
                timeout=40,
            )
            try:
                j = r.json()
            except Exception:
                j = {}
            err = j.get("error")
            item_o = ((j.get("data") or {}) if isinstance(j.get("data"), dict) else {}).get("item")
            keys = len(item_o) if isinstance(item_o, dict) else 0
            print(f"[{name}] http={r.status_code} error={err} item_keys={keys}")
        except Exception as e:
            print(f"[{name}] EXC {type(e).__name__}: {e}")

    print("EXPECTED: error=90309999 — signatures missing (not TLS).")
    print("NEXT: br_scale_1m/signer_service.py + worker_http.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
