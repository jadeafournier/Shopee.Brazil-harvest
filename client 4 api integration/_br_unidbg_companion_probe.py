# -*- coding: utf-8 -*-
"""Quick ID/BR probe with companion SHPS headers + Unidbg defense blob."""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

import requests

JAR = Path(r"D:\emu\shopee_signer\target\shopee-signer-1.0.0.jar")
SO = Path(r"D:\emu\shopee_signer\libs\libshpssdk.so")
JDK = Path(r"D:\emu\jdk\jdk-17.0.19+10\bin\java.exe")
ROOT = Path(__file__).resolve().parent


def sign(path: str) -> dict:
    p = subprocess.run(
        [str(JDK), "-jar", str(JAR), str(SO), path],
        cwd=str(JAR.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    m = re.search(r"\[signer\] RESULT=(.+)", p.stdout)
    if not m:
        raise RuntimeError(p.stdout[-1500:] + p.stderr[-1500:])
    return json.loads(m.group(1))


def companion() -> dict:
    return {
        "sdkVersion": "6200",
        "os": "android",
        "osver": "30",
        "model": "Pixel 4",
        "brand": "google",
        "appId": "1001",
        "shopee-traceid": str(uuid.uuid4()),
        "Content-Type": "application/json",
        "User-Agent": "okhttp/4.9.2",
        "Accept": "application/json",
        "X-API-SOURCE": "rn",
    }


def main():
    text = (ROOT / "BR TEST.txt").read_text(encoding="utf-8", errors="replace")
    pline = text.split("Proxy:")[1].strip().splitlines()[0].strip()
    h, po, u, pw = pline.split(":", 3)
    br_proxy = f"http://{u}:{pw}@{h}:{po}"

    tests = [
        ("https://shopee.co.id", "/api/v4/pdp/get_pc?shop_id=58476421&item_id=23303228039", None, "id"),
        ("https://shopee.co.id", "/api/v4/item/get?shopid=58476421&itemid=23303228039", None, "id"),
        ("https://shopee.com.br", "/api/v4/pdp/get_pc?shop_id=1397458631&item_id=26420453879", br_proxy, "pt-BR"),
    ]
    for host, path, proxy, lang in tests:
        sig = sign(path)
        headers = {**companion(), **sig, "X-Shopee-Language": lang}
        # also map longest to x-sap-sec
        longest = max(sig, key=lambda k: len(str(sig[k])))
        headers2 = {**headers, "x-sap-sec": sig[longest]}
        for name, hdr in (("raw", headers), ("+x-sap-sec", headers2)):
            try:
                r = requests.get(
                    host + path,
                    headers=hdr,
                    proxies={"http": proxy, "https": proxy} if proxy else None,
                    timeout=35,
                )
                j = {}
                try:
                    j = r.json()
                except Exception:
                    pass
                data = j.get("data") if isinstance(j, dict) else None
                ok = isinstance(data, dict) and ("item" in data or "itemid" in data)
                print(
                    f"{host.split('//')[1]} {path.split('?')[0].split('/')[-1]} {name} "
                    f"st={r.status_code} err={j.get('error') if isinstance(j, dict) else None} "
                    f"ok={ok} keys={list(sig.keys())} n={len(r.content)}"
                )
                if ok:
                    (ROOT / "br_signed_probe_ok.json").write_text(
                        json.dumps({"url": host + path, "body": j}, ensure_ascii=False)[:200000],
                        encoding="utf-8",
                    )
                    return 0
            except Exception as e:
                print("EXC", e)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
