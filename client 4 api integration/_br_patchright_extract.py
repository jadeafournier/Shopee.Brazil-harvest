# -*- coding: utf-8 -*-
"""BR get_pc — patchright headed, passive SPA intercept (passes 90309999).

Only the PDP JavaScript's own get_pc request carries x-sap-sec (browser signing).
curl and page.evaluate(fetch) always get 90309999 — do not use them.

Flow that passes 90309999:
  1. Headed patchright + login cookies + timezone America/Sao_Paulo
  2. Open /product/{shop}/{item} — intercept passive get_pc
  3. First hit may be 90309999 while /verify/traffic runs — wait for traffic pass
  4. After pass, SPA refires signed get_pc with error 0/null → save data
  5. Fresh proxy per URL; carry AC_CERT_D forward after first captcha solve
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_probe import BR_TEST, parse_br_test, parse_url_ids, summarize_data
from br_cookies import br_context_kwargs, enforce_br_locale, normalize_br_cookies, pw_cookie_list
import captcha_mint as mint
import captcha_playwright as cpw
import proxy_pool

OUT = Path(__file__).resolve().parent / "br_probe_output"
DOM = "shopee.com.br"
VIEW_W, VIEW_H = mint.VIEW_W, mint.VIEW_H
GETPC_WAIT_S = 35.0
VERIFY_WAIT_S = 25.0
STRIP_CERT_KEYS = ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _save(url: str, data: dict) -> None:
    sid, iid = parse_url_ids(url)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"output_{sid}_{iid}.json").write_text(json.dumps({
        "task_link": url, "token": "br_client4_test", "api_key": "br_client4_test",
        "bff_meta": None, "error": None, "error_msg": None, "data": data,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _on_pdp(page_url: str, shop_id: int, item_id: int) -> bool:
    u = page_url or ""
    return f"/product/{shop_id}/{item_id}" in u or f"-i.{shop_id}.{item_id}" in u


def fetch_one(
    url: str,
    cookies: dict,
    *,
    manual_captcha_ok: bool = True,
) -> tuple[dict | None, dict, dict | None]:
    """One URL, one fresh proxy+context. Intercept SPA-signed get_pc only."""
    from patchright.sync_api import sync_playwright

    shop_id, item_id = parse_url_ids(url)
    pdp = f"https://{DOM}/product/{shop_id}/{item_id}"
    proxy, proxy_raw, sub_key = mint._proxy_dict("BR")
    host = (proxy_raw or "?").split("@")[-1].split(":")[0]
    holder: dict = {"attempts": []}
    meta: dict = {"proxy": host, "shop_id": shop_id, "item_id": item_id}
    gen: dict = {}
    harvested: dict | None = None

    def on_getpc(r):
        if "/api/v4/pdp/get_pc" not in r.url or str(item_id) not in r.url:
            return
        try:
            j = json.loads(r.text())
            tz_m = None
            if "tz_offset_in_minutes=" in r.url:
                try:
                    tz_m = int(r.url.split("tz_offset_in_minutes=")[1].split("&")[0])
                except Exception:
                    pass
            signed = bool((r.request.headers or {}).get("x-sap-sec"))
            err = j.get("error")
            holder["attempts"].append({"err": err, "tz": tz_m, "signed": signed})
            if err in (0, None):
                item = (j.get("data") or {}).get("item") or {}
                if int(item.get("item_id") or 0) == item_id:
                    holder["data"] = j["data"]
        except Exception:
            pass

    with sync_playwright() as p:
        udd = tempfile.mkdtemp(prefix="br1_")
        cookies = enforce_br_locale(cookies)
        ctx_kw = br_context_kwargs()
        ctx = p.chromium.launch_persistent_context(
            udd, channel="chrome", headless=False, proxy=proxy,
            viewport={"width": VIEW_W, "height": VIEW_H},
            locale=ctx_kw["locale"],
            timezone_id=ctx_kw["timezone_id"],
            extra_http_headers=ctx_kw["extra_http_headers"],
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            ctx.add_cookies(pw_cookie_list(cookies, fresh_cert=False))
            page = ctx.new_page()
            page.on("response", on_getpc)

            page.goto(f"https://{DOM}/", wait_until="load", timeout=60000)
            page.wait_for_timeout(random.randint(2500, 4000))
            page.mouse.move(random.randint(200, 900), random.randint(180, 520))
            page.mouse.wheel(0, random.randint(900, 2200))
            page.wait_for_timeout(random.randint(800, 1500))

            try:
                page.goto(pdp, wait_until="load", timeout=90000)
            except Exception:
                pass

            deadline = time.time() + GETPC_WAIT_S + VERIFY_WAIT_S
            captcha_tried = False
            manual_used = False
            reloaded = 0

            while time.time() < deadline and not holder.get("data"):
                u = page.url or ""

                if "/verify/captcha" in u:
                    meta["status"] = "captcha"
                    if not captcha_tried:
                        captcha_tried = True
                        sol = cpw.solve_captcha_page(page, dom=DOM, gen_flag=gen)
                        if not sol.get("ok") and manual_captcha_ok and not manual_used:
                            manual_used = True
                            _log("  solve captcha in browser (90s)")
                            cpw.wait_for_captcha_clear(page, 90)
                        if _on_pdp(page.url or "", shop_id, item_id) and not holder.get("data"):
                            try:
                                page.reload(wait_until="load", timeout=60000)
                            except Exception:
                                pass
                    elif not holder.get("data"):
                        break

                elif "/verify/traffic" in u and "/error" not in u:
                    _log("  traffic verify — waiting pass")
                    cpw.wait_traffic_pass(page, DOM, secs=int(VERIFY_WAIT_S))
                    if not holder.get("data") and reloaded < 2:
                        reloaded += 1
                        if _on_pdp(page.url or "", shop_id, item_id):
                            try:
                                page.reload(wait_until="load", timeout=60000)
                            except Exception:
                                pass
                        else:
                            try:
                                page.goto(pdp, wait_until="load", timeout=60000)
                            except Exception:
                                pass
                        page.wait_for_timeout(1500)

                elif "/verify/traffic/error" in u:
                    meta["status"] = "traffic_error"
                    break

                elif _on_pdp(u, shop_id, item_id):
                    page.mouse.wheel(0, random.randint(300, 800))
                    page.wait_for_timeout(600)

                else:
                    page.wait_for_timeout(400)

            ck = {c["name"]: c["value"] for c in ctx.cookies()}
            if ck.get("AC_CERT_D"):
                harvested = {k: v for k, v in ck.items() if v and v not in ("-", "deleted")}
            meta.update({
                "has_data": bool(holder.get("data")),
                "final_url": (page.url or "")[:100],
                "getpc_attempts": holder.get("attempts", [])[-6:],
                "ac_cert": bool(ck.get("AC_CERT_D")),
                "status": meta.get("status") or ("ok" if holder.get("data") else "no_data"),
            })
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    return holder.get("data"), meta, harvested


def run_urls(urls: list[str], cookies: dict) -> tuple[dict[str, dict], dict]:
    results: dict[str, dict] = {}
    run_meta: dict = {"urls": {}}
    work = normalize_br_cookies(cookies, fresh_cert=False)
    manual_used = False

    for idx, url in enumerate(urls):
        shop_id, item_id = parse_url_ids(url)
        key = f"{shop_id}_{item_id}"
        out_path = OUT / f"output_{shop_id}_{item_id}.json"
        if out_path.is_file():
            _log(f"skip existing {key}")
            results[key] = json.loads(out_path.read_text(encoding="utf-8"))["data"]
            continue

        _log("=" * 70)
        _log(f"[{idx + 1}/{len(urls)}] i.{shop_id}.{item_id}")

        data, meta, harvested = fetch_one(
            url, work, manual_captcha_ok=not manual_used,
        )
        if harvested:
            for k, v in harvested.items():
                if k == "AC_CERT_D" or k not in STRIP_CERT_KEYS:
                    work[k] = v
            if harvested.get("AC_CERT_D"):
                manual_used = True

        run_meta["urls"][key] = meta
        _log(f"  {meta}")
        if data:
            results[key] = data
            _save(url, data)
            _log(f"  OK {summarize_data(data)}")
        else:
            _log("  FAILED")

    return results, run_meta


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--only-missing", action="store_true")
    args = ap.parse_args()

    cookies, _, urls = parse_br_test(BR_TEST)
    proxy_pool.set_region("BR")
    proxy_pool.load()
    OUT.mkdir(parents=True, exist_ok=True)

    targets = list(urls)
    if args.only_missing:
        targets = [
            u for u in urls
            if not (OUT / f"output_{parse_url_ids(u)[0]}_{parse_url_ids(u)[1]}.json").is_file()
        ]
    if not targets:
        _log("Nothing to do.")
        return 0

    _log(f"BR extract: {len(targets)} URL(s) | SPA-signed get_pc intercept | per-URL proxy")
    results, meta = run_urls(targets, cookies)
    saved = len(results)
    OUT.joinpath("probe_summary.json").write_text(json.dumps({
        "saved": saved,
        "total_urls": len(targets),
        "method": "patchright_passive_spa_signed_getpc",
        "meta": meta,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"\nDone: {saved}/{len(targets)}")
    return 0 if saved == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
