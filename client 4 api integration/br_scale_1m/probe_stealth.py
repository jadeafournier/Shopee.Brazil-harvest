# -*- coding: utf-8 -*-
"""Optional Camoufox / Patchright stealth smoke (fingerprint layer only).

Does NOT solve 90309999 by itself — documents that engine-level stealth ≠ SAP signing.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "shopee_getpc_crawler"), str(HERE)]

URL = "https://shopee.com.br/product/980944158/26142163515"


def try_camoufox() -> dict:
    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        return {"engine": "camoufox", "ok": False, "reason": "pip install camoufox && camoufox fetch"}
    holder = {}
    try:
        with Camoufox(headless=True) as browser:
            page = browser.new_page()

            def on_resp(r):
                try:
                    if "/api/v4/pdp/get_pc" in r.url and r.status == 200:
                        j = r.json()
                        item = ((j.get("data") or {}).get("item") if isinstance(j.get("data"), dict) else None) or {}
                        holder["err"] = j.get("error")
                        holder["keys"] = len(item) if isinstance(item, dict) else 0
                except Exception:
                    pass

            page.on("response", on_resp)
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            t0 = time.time()
            while time.time() - t0 < 25 and "err" not in holder:
                page.wait_for_timeout(400)
        return {"engine": "camoufox", "ok": holder.get("err") in (0, None) and holder.get("keys", 0) >= 40,
                "result": holder}
    except Exception as e:
        return {"engine": "camoufox", "ok": False, "reason": str(e)[:120]}


def try_patchright() -> dict:
    import captcha_mint as mint
    from br_cookies import br_context_kwargs
    from patchright.sync_api import sync_playwright

    holder = {}
    p = sync_playwright().start()
    proxy, _, _ = mint._proxy_dict("BR")
    cx = br_context_kwargs()
    udd = tempfile.mkdtemp(prefix="stealth_")
    try:
        ctx = p.chromium.launch_persistent_context(
            udd, channel="chrome", headless=True, proxy=proxy,
            locale=cx["locale"], timezone_id=cx["timezone_id"],
            extra_http_headers=cx["extra_http_headers"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()

        def on_resp(r):
            try:
                if "/api/v4/pdp/get_pc" in r.url and r.status == 200:
                    j = r.json()
                    item = ((j.get("data") or {}).get("item") if isinstance(j.get("data"), dict) else None) or {}
                    holder["err"] = j.get("error")
                    holder["keys"] = len(item) if isinstance(item, dict) else 0
            except Exception:
                pass

        page.on("response", on_resp)
        page.goto(URL, wait_until="commit", timeout=50000)
        t0 = time.time()
        while time.time() - t0 < 25 and "err" not in holder:
            page.wait_for_timeout(400)
        ctx.close()
        return {"engine": "patchright", "ok": holder.get("err") in (0, None) and holder.get("keys", 0) >= 40,
                "result": holder}
    except Exception as e:
        return {"engine": "patchright", "ok": False, "reason": str(e)[:120]}
    finally:
        try:
            p.stop()
        except Exception:
            pass


def main() -> int:
    out = {"camoufox": try_camoufox(), "patchright_guest": try_patchright()}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("NOTE: stealth browser alone typically yields 90309999 on BR — need SAP signing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
