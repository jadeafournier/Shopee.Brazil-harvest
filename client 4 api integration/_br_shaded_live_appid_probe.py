# -*- coding: utf-8 -*-
"""Live mode (appId + same-session config) via shaded jar; probe get_pc and save successes."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "br_deliverable_getpc"
JAR = Path(r"D:\emu\shopee_signer\target\shopee-signer-1.0.0-shaded.jar")
SO = Path(r"D:\emu\shopee_signer\libs\libshpssdk.so")
APK = Path(r"D:\emu\shopee_id_x\com.shopee.id.apk")
JDK = Path(r"D:\emu\jdk\jdk-17.0.19+10\bin\java.exe")
BR_TEST = ROOT / "BR TEST.txt"
URLS = ROOT / "br_active_product_urls_100k.txt"
WORKDIR = JAR.parent
URL_FILE = WORKDIR / "_live_urls.txt"


def load_proxy_cookie():
    text = BR_TEST.read_text(encoding="utf-8", errors="replace")
    cookie = ""
    m = re.search(r"Cookie:\s*\n(.+?)(?:\n\s*\n|\nProxy:)", text, re.S | re.I)
    if m:
        cookie = m.group(1).strip().splitlines()[0].strip()
    proxies = []
    in_proxy = False
    for line in text.splitlines():
        if line.strip().lower().startswith("proxy:"):
            in_proxy = True
            continue
        if in_proxy:
            if not line.strip() or line.strip().lower().startswith("test"):
                break
            parts = line.strip().split(":")
            if len(parts) >= 4:
                host, port, user, pwd = parts[0], parts[1], parts[2], ":".join(parts[3:])
                proxies.append(f"http://{quote(user, safe='-_')}:{pwd}@{host}:{port}")
    return cookie, proxies


def parse_ids(url: str) -> tuple[str, str]:
    m = re.search(r"i\.(\d+)\.(\d+)", url) or re.search(r"/product/(\d+)/(\d+)", url)
    if not m:
        raise ValueError(url)
    return m.group(1), m.group(2)


def pick_products(n: int = 3) -> list[str]:
    urls = []
    text = BR_TEST.read_text(encoding="utf-8", errors="replace")
    in_url = False
    for line in text.splitlines():
        if line.strip().lower().startswith("test url"):
            in_url = True
            continue
        if in_url and line.strip().startswith("http"):
            urls.append(line.strip())
    if URLS.is_file():
        for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("http") and "shopee.com.br" in line:
                urls.append(line.strip())
            if len(urls) >= n + 5:
                break
    out, seen = [], set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= n:
            break
    return out


def companion():
    return {
        "sdkVersion": "6200",
        "os": "android",
        "osver": "30",
        "model": "Pixel 4",
        "brand": "google",
        "appId": "1001",
        "shopee-traceid": str(uuid.uuid4()),
        "User-Agent": "okhttp/4.9.2",
        "Accept": "application/json",
        "X-API-SOURCE": "rn",
    }


def hdr_modes(sig: dict):
    longest = max(sig, key=lambda k: len(str(sig[k])))
    shorts = sorted([(k, v) for k, v in sig.items() if k not in ("x-sap-ri", longest)], key=lambda kv: len(str(kv[1])))
    dual = dict(sig)
    dual["x-sap-sec"] = sig[longest]
    if shorts:
        dual["af-ac-enc-dat"] = str(shorts[0][1])
    return [
        ("raw", dict(sig)),
        ("sap", {"x-sap-ri": sig.get("x-sap-ri", ""), "x-sap-sec": sig[longest]}),
        ("dual", dual),
    ]


def run_live(sign_urls: list[str]) -> tuple[dict, dict[str, dict]]:
    """Returns (meta, url->sig)."""
    URL_FILE.write_text("\n".join(sign_urls) + "\n", encoding="utf-8")
    env = {**dict(**{k: v for k, v in __import__("os").environ.items()})}
    for k in ("SIGNER_CFG", "SIGNER_CFG_DATA", "SIGNER_XSPA", "SIGNER_SKIP_APPID", "SIGNER_SOFTTOKEN"):
        env.pop(k, None)
    env["SIGNER_SKIP_STALE_CFG"] = "1"  # live mode applies fresh cfg itself
    cmd = [str(JDK), "-jar", str(JAR), "live", str(SO), str(APK), str(URL_FILE)]
    print("[live] starting", flush=True)
    p = subprocess.run(
        cmd,
        cwd=str(WORKDIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=240,
    )
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    (WORKDIR / "_live_run.log").write_text(out, encoding="utf-8")
    meta = {"rc": p.returncode, "apply": None, "cfg_code": None, "xspa": False}
    for line in out.splitlines():
        if "LIVE_APPLY status=" in line:
            m = re.search(r"status=(-?\d+)", line)
            meta["apply"] = int(m.group(1)) if m else None
        if "CONFIG_CODE=" in line:
            meta["cfg_code"] = line.split("CONFIG_CODE=", 1)[1].strip()
        if "LIVE_XSPA" in line:
            meta["xspa"] = True
        if "CONFIG_HTTP" in line or "LIVE_" in line or "vuwuuwvv" in line:
            print(line[:220].encode("ascii", "replace").decode(), flush=True)

    sigs: dict[str, dict] = {}
    cur_url = None
    for line in out.splitlines():
        if line.startswith("[signer] URL="):
            cur_url = line.split("URL=", 1)[1].strip()
        elif line.startswith("[signer] RESULT=") and cur_url:
            try:
                sigs[cur_url] = json.loads(line.split("RESULT=", 1)[1].strip())
            except Exception:
                pass
            cur_url = None
    return meta, sigs


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    before = len(list(OUT.glob("output_*.json")))
    cookie, proxies = load_proxy_cookie()
    proxy = proxies[0] if proxies else None
    px = {"http": proxy, "https": proxy} if proxy else None
    products = pick_products(3)
    sign_urls = []
    product_by_api = {}
    for p in products:
        shop, item = parse_ids(p)
        for host in ("https://shopee.co.id", "https://shopee.com.br"):
            for path in (
                f"/api/v4/pdp/get_pc?shop_id={shop}&item_id={item}",
                f"/api/v4/pdp/get_pc?item_id={item}&shop_id={shop}&tz_offset_in_minutes=-180&detail_level=0",
            ):
                u = host + path
                sign_urls.append(u)
                product_by_api[u] = (p, shop, item, host)

    meta, sigs = run_live(sign_urls)
    print("[live] meta", meta, "sigs", len(sigs), flush=True)

    saved, errors = [], []
    for api_url, sig in sigs.items():
        product, shop, item, host = product_by_api[api_url]
        region = "br" if "shopee.com.br" in host else "id"
        for mode, extra in hdr_modes(sig):
            headers = {
                **companion(),
                "Referer": host + "/",
                "X-Shopee-Language": "pt-BR" if region == "br" else "id",
                **extra,
            }
            if region == "br" and cookie:
                headers["Cookie"] = cookie
                if "csrftoken=" in cookie:
                    headers["X-CSRFToken"] = cookie.split("csrftoken=")[1].split(";")[0]
            try:
                r = requests.get(api_url, headers=headers, proxies=px if region == "br" else None, timeout=40)
                body = r.json() if r.content else {}
                err = body.get("error") if isinstance(body, dict) else None
                data = body.get("data") if isinstance(body, dict) else None
                item_obj = data.get("item") if isinstance(data, dict) else None
                nkeys = len(item_obj) if isinstance(item_obj, dict) else 0
                ok = err in (0, None) and nkeys >= 40
                errors.append({"region": region, "mode": mode, "err": err, "st": r.status_code, "nkeys": nkeys, "ok": ok})
                print(f"[live] {region}/{mode} st={r.status_code} err={err} keys={nkeys}", flush=True)
                if ok:
                    outp = OUT / f"output_{shop}_{item}.json"
                    outp.write_text(json.dumps({"task_link": product, "data": data, "error": None}, ensure_ascii=False), encoding="utf-8")
                    saved.append(str(outp))
                    print("[live] SAVED", outp, flush=True)
            except Exception as e:
                errors.append({"err": f"EXC:{e}", "ok": False})

    after = len(list(OUT.glob("output_*.json")))
    summary = {
        "meta": meta,
        "saved_count": len(saved),
        "new_files": after - before,
        "total": after,
        "unique_errs": sorted({str(e.get("err")) for e in errors}),
        "sample": errors[:25],
        "jar": str(JAR),
    }
    (OUT / "_live_appid_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("[live] DONE", {k: summary[k] for k in ("saved_count", "new_files", "total", "unique_errs", "meta")}, flush=True)
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
