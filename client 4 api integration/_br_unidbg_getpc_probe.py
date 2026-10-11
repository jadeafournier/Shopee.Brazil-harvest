# -*- coding: utf-8 -*-
"""Probe BR get_pc using Unidbg-generated SHPS signing headers."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
JAR = Path(r"D:\emu\shopee_signer\target\shopee-signer-1.0.0.jar")
SO = Path(r"D:\emu\shopee_signer\libs\libshpssdk.so")
JDK = Path(r"D:\emu\jdk\jdk-17.0.19+10\bin\java.exe")
BR_TEST = ROOT / "BR TEST.txt"

# Known mappings when native returns hashed header names.
HEADER_ALIASES = {
    "e7d31292": "x-sap-sec",
    "e8221518": "x-sap-security",
    "2f": "x-sz-sdk-version",
}


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
                proxies.append(f"http://{user}:{pwd}@{host}:{port}")
    return cookie, proxies


def sign(url_path: str) -> dict:
    cmd = [str(JDK), "-jar", str(JAR), str(SO), url_path]
    p = subprocess.run(cmd, cwd=str(JAR.parent), capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    m = re.search(r"\[signer\] RESULT=(.+)", out)
    if not m:
        raise RuntimeError(f"signer failed rc={p.returncode}\n{out[-2000:]}")
    return json.loads(m.group(1).strip())


def build_header_variants(sig: dict) -> list[dict]:
    raw = {k: v for k, v in sig.items()}
    mapped = {}
    for k, v in sig.items():
        mapped[HEADER_ALIASES.get(k, k)] = v
    # Longest value is almost certainly x-sap-sec
    longest_k = max(sig, key=lambda k: len(str(sig[k])))
    forced = dict(mapped)
    forced["x-sap-sec"] = sig[longest_k]
    if "x-sap-ri" in sig:
        forced["x-sap-ri"] = sig["x-sap-ri"]
    return [
        ("raw_keys", raw),
        ("alias_map", mapped),
        ("force_x_sap_sec", forced),
    ]


def main():
    cookie, proxies = load_proxy_cookie()
    proxy = proxies[0] if proxies else None
    shop_id, item_id = "1397458631", "26420453879"
    paths = [
        f"/api/v4/pdp/get_pc?shop_id={shop_id}&item_id={item_id}",
        f"/api/v4/item/get?shopid={shop_id}&itemid={item_id}",
        f"/api/v4/pdp/get?shop_id={shop_id}&item_id={item_id}",
        f"/api/v4/pdp/get_rw?shop_id={shop_id}&item_id={item_id}",
    ]
    hosts = [
        "https://shopee.com.br",
        "https://mall.shopee.com.br",
    ]
    ua_variants = {
        "app": "okhttp/4.9.2",
        "app2": "Android app Shopee appver=35328 language=pt-BR app_type=1 platform=native_android os_ver=30",
        "web": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    proxies_kw = {"http": proxy, "https": proxy} if proxy else None

    for path in paths:
        print("[probe] signing", path)
        try:
            sig = sign(path)
        except Exception as e:
            print("[probe] sign fail", e)
            continue
        print("[probe] sig keys:", list(sig.keys()), "lens:", {k: len(str(v)) for k, v in sig.items()})
        for host in hosts:
            url = host + path
            for ua_name, ua in ua_variants.items():
                base_headers = {
                    "User-Agent": ua,
                    "Accept": "application/json",
                    "Referer": host + "/",
                    "X-Shopee-Language": "pt-BR",
                    "X-Requested-With": "com.shopee.br" if "app" in ua_name else "XMLHttpRequest",
                    "X-API-SOURCE": "rn" if "app" in ua_name else "pc",
                    "sdkVersion": "0101",
                    "os": "android",
                    "client-info": "device_model=Pixel_4;os=android;os_version=30",
                }
                if cookie:
                    base_headers["Cookie"] = cookie
                    if "csrftoken=" in cookie:
                        base_headers["X-CSRFToken"] = cookie.split("csrftoken=")[1].split(";")[0]
                for name, extra in build_header_variants(sig):
                    headers = {**base_headers, **extra}
                    try:
                        r = requests.get(url, headers=headers, proxies=proxies_kw, timeout=40)
                        j = {}
                        try:
                            j = r.json()
                        except Exception:
                            pass
                        err = j.get("error") if isinstance(j, dict) else None
                        data = j.get("data") if isinstance(j, dict) else None
                        has_item = isinstance(data, dict) and (
                            "item" in data or "itemid" in data or "name" in data
                        )
                        print(
                            f"[probe] {host.split('//')[1][:16]} {path.split('?')[0].split('/')[-1]} "
                            f"ua={ua_name} var={name} st={r.status_code} err={err} item={has_item} n={len(r.content)}"
                        )
                        if has_item or err in (0, None) and isinstance(data, dict) and len(data) > 3:
                            out = ROOT / "br_signed_probe_ok.json"
                            out.write_text(json.dumps({"url": url, "variant": name, "ua": ua_name, "body": j}, ensure_ascii=False)[:250000], encoding="utf-8")
                            print("[probe] SUCCESS wrote", out)
                            return 0
                    except Exception as e:
                        print(f"[probe] EXC {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
