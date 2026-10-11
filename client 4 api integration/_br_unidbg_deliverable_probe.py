# -*- coding: utf-8 -*-
"""Probe ID/BR get_pc with deepened Unidbg signer; save successes to br_deliverable_getpc/."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
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
DF_HOST = "https://df.infra.sz.shopee.co.id"


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
                # Encode spaces / unicode in user (SÃ£o Paulo)
                user_enc = quote(user, safe="-_")
                proxies.append(f"http://{user_enc}:{pwd}@{host}:{port}")
    return cookie, proxies


def sign(url_path: str, xspa: str | None = None, cfg: str | None = None) -> dict:
    env = {**dict(**{k: v for k, v in __import__("os").environ.items()})}
    if xspa:
        env["SIGNER_XSPA"] = xspa
    if cfg:
        env["SIGNER_CFG"] = cfg
    cmd = [str(JDK), "-jar", str(JAR), str(SO), url_path, str(APK)]
    p = subprocess.run(
        cmd,
        cwd=str(JAR.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=90,
    )
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    m = re.search(r"\[signer\] RESULT=(.+)", out)
    if not m:
        raise RuntimeError(f"signer failed rc={p.returncode}\n{out[-2500:]}")
    return json.loads(m.group(1).strip()), out


def header_variants(sig: dict) -> list[tuple[str, dict]]:
    raw = dict(sig)
    longest = max(sig, key=lambda k: len(str(sig[k])))
    forced = dict(sig)
    forced["x-sap-sec"] = sig[longest]
    # mobile-style: keep hashed names + also mirror long to x-sap-sec
    dual = dict(sig)
    dual["x-sap-sec"] = sig[longest]
    if "x-sap-ri" in sig:
        dual["x-sap-ri"] = sig["x-sap-ri"]
    return [
        ("raw", raw),
        ("force_x_sap_sec", forced),
        ("dual", dual),
    ]


def parse_ids(url: str) -> tuple[str, str]:
    m = re.search(r"i\.(\d+)\.(\d+)", url)
    if not m:
        raise ValueError(url)
    return m.group(1), m.group(2)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cookie, proxies = load_proxy_cookie()
    proxy = proxies[0] if proxies else None
    proxies_kw = {"http": proxy, "https": proxy} if proxy else None
    print("[probe] proxy=", (proxy or "")[:90], "cookie_len=", len(cookie))

    # Try signed config fetch from df.infra
    xspa = None
    cfg_blob = None
    try:
        cfg_path = "/v3/config"
        sig_cfg, log = sign(f"https://df.infra.sz.shopee.co.id{cfg_path}")
        print("[probe] config-sign keys", list(sig_cfg.keys()), "lens", {k: len(str(v)) for k, v in sig_cfg.items()})
        hdrs = {
            "User-Agent": "okhttp/4.9.2",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "sdkVersion": "0101",
            "os": "android",
            "szdet": str(int(time.time() * 1000)),
            "postType": "0",
            **sig_cfg,
        }
        longest = max(sig_cfg, key=lambda k: len(str(sig_cfg[k])))
        hdrs["x-sap-sec"] = sig_cfg[longest]
        body = json.dumps({"appId": 1001, "cldNo": 484964812510, "sdk_version": "476"})
        r = requests.post(DF_HOST + cfg_path, data=body, headers=hdrs, proxies=proxies_kw, timeout=30)
        print("[probe] df config st=", r.status_code, "xspa=", bool(r.headers.get("X-Sp-A")), "body=", r.text[:200])
        xspa = r.headers.get("X-Sp-A") or r.headers.get("x-sp-a")
        if r.status_code == 200 and "code" not in (r.json() if r.content else {}):
            cfg_blob = r.text
        elif r.status_code == 200:
            try:
                j = r.json()
                if isinstance(j, dict) and j.get("data"):
                    cfg_blob = json.dumps(j.get("data"))
            except Exception:
                pass
        if xspa:
            print("[probe] got X-Sp-A len", len(xspa))
    except Exception as e:
        print("[probe] config fetch soft-fail:", e)

    text = BR_TEST.read_text(encoding="utf-8", errors="replace")
    urls = []
    in_url = False
    for line in text.splitlines():
        if line.strip().lower().startswith("test url"):
            in_url = True
            continue
        if in_url and line.strip().startswith("http"):
            urls.append(line.strip())
    if not urls:
        urls = [
            "https://shopee.com.br/i.1397458631.26420453879",
        ]

    hosts = [
        ("https://shopee.co.id", "id"),
        ("https://shopee.com.br", "br"),
    ]
    uas = {
        "app": "okhttp/4.9.2",
        "web": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }

    saved = []
    error_codes = []
    # First URL only for ID + BR matrix; then remaining BR URLs with best variant
    targets = []
    shop_id, item_id = parse_ids(urls[0])
    path = f"/api/v4/pdp/get_pc?shop_id={shop_id}&item_id={item_id}"
    targets.append(("seed", path, shop_id, item_id))

    print("[probe] signing", path)
    try:
        sig, slog = sign(
            path if path.startswith("/") else path,
            xspa=xspa,
            cfg=cfg_blob,
        )
        # Prefer signing full URL variants too later
        print("[probe] sig keys", list(sig.keys()), {k: len(str(v)) for k, v in sig.items()})
        if "vuwuuwvv" in slog:
            print("[probe] init lines:", "\n".join(l for l in slog.splitlines() if "[signer]" in l)[:500])
    except Exception as e:
        print("[probe] SIGN FAIL", e)
        return 2

    for host, region in hosts:
        for ua_name, ua in uas.items():
            for var_name, extra in header_variants(sig):
                url = host + path
                headers = {
                    "User-Agent": ua,
                    "Accept": "application/json",
                    "Referer": host + "/",
                    "X-Shopee-Language": "id" if region == "id" else "pt-BR",
                    "X-API-SOURCE": "rn" if ua_name == "app" else "pc",
                    "X-Requested-With": "com.shopee.id" if region == "id" else "com.shopee.br",
                    "sdkVersion": "0101",
                    "os": "android",
                    **extra,
                }
                if cookie and region == "br":
                    headers["Cookie"] = cookie
                    if "csrftoken=" in cookie:
                        headers["X-CSRFToken"] = cookie.split("csrftoken=")[1].split(";")[0]
                try:
                    r = requests.get(url, headers=headers, proxies=proxies_kw, timeout=45)
                    try:
                        j = r.json()
                    except Exception:
                        j = {"_raw": r.text[:500]}
                    err = j.get("error") if isinstance(j, dict) else None
                    data = j.get("data") if isinstance(j, dict) else None
                    has_item = isinstance(data, dict) and ("item" in data)
                    ok = (err in (0, None) and has_item)
                    error_codes.append({"host": host, "ua": ua_name, "var": var_name, "st": r.status_code, "err": err, "ok": ok})
                    print(f"[probe] {region} ua={ua_name} var={var_name} st={r.status_code} err={err} item={has_item} n={len(r.content)}")
                    # Feed X-Sp-A back if present
                    xa = r.headers.get("X-Sp-A") or r.headers.get("x-sp-a")
                    if xa and not xspa:
                        xspa = xa
                        print("[probe] captured X-Sp-A from get_pc len", len(xa))
                    if ok:
                        outp = OUT / f"output_{shop_id}_{item_id}_{region}_{ua_name}_{var_name}.json"
                        outp.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
                        saved.append(str(outp))
                        print("[probe] SAVED", outp)
                except Exception as e:
                    print(f"[probe] EXC {region}/{ua_name}/{var_name}: {e}")
                    error_codes.append({"host": host, "ua": ua_name, "var": var_name, "err": f"EXC:{e}"})

    # If we got X-Sp-A, resign and retry BR once
    if xspa:
        print("[probe] resign with X-Sp-Aâ€¦")
        try:
            sig2, _ = sign(path, xspa=xspa, cfg=cfg_blob)
            host = "https://shopee.com.br"
            for var_name, extra in header_variants(sig2):
                headers = {
                    "User-Agent": "okhttp/4.9.2",
                    "Accept": "application/json",
                    "Referer": host + "/",
                    "X-Shopee-Language": "pt-BR",
                    "X-API-SOURCE": "rn",
                    "Cookie": cookie,
                    **extra,
                }
                if "csrftoken=" in cookie:
                    headers["X-CSRFToken"] = cookie.split("csrftoken=")[1].split(";")[0]
                r = requests.get(host + path, headers=headers, proxies=proxies_kw, timeout=45)
                j = r.json() if r.content else {}
                err = j.get("error") if isinstance(j, dict) else None
                data = j.get("data") if isinstance(j, dict) else None
                has_item = isinstance(data, dict) and ("item" in data)
                ok = err in (0, None) and has_item
                error_codes.append({"host": host, "ua": "app", "var": f"xspa_{var_name}", "st": r.status_code, "err": err, "ok": ok})
                print(f"[probe] br+xspa var={var_name} st={r.status_code} err={err} item={has_item}")
                if ok:
                    outp = OUT / f"output_{shop_id}_{item_id}_br_xspa_{var_name}.json"
                    outp.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
                    saved.append(str(outp))
        except Exception as e:
            print("[probe] xspa resign fail", e)

    # Extra BR URLs if any success pattern found â€” still try remaining with best effort
    for u in urls[1:3]:
        try:
            sid, iid = parse_ids(u)
        except Exception:
            continue
        p2 = f"/api/v4/pdp/get_pc?shop_id={sid}&item_id={iid}"
        try:
            sig3, _ = sign(p2, xspa=xspa, cfg=cfg_blob)
        except Exception as e:
            print("[probe] sign fail", sid, e)
            continue
        host = "https://shopee.com.br"
        _, extra = header_variants(sig3)[1]  # force_x_sap_sec
        headers = {
            "User-Agent": "okhttp/4.9.2",
            "Accept": "application/json",
            "Referer": host + "/",
            "X-Shopee-Language": "pt-BR",
            "Cookie": cookie,
            **extra,
        }
        try:
            r = requests.get(host + p2, headers=headers, proxies=proxies_kw, timeout=45)
            j = r.json() if r.content else {}
            err = j.get("error") if isinstance(j, dict) else None
            data = j.get("data") if isinstance(j, dict) else None
            has_item = isinstance(data, dict) and ("item" in data)
            ok = err in (0, None) and has_item
            error_codes.append({"host": host, "item": iid, "st": r.status_code, "err": err, "ok": ok})
            print(f"[probe] extra br i.{sid}.{iid} st={r.status_code} err={err} item={has_item}")
            if ok:
                outp = OUT / f"output_{sid}_{iid}.json"
                outp.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
                saved.append(str(outp))
        except Exception as e:
            print("[probe] extra EXC", e)

    summary = {
        "saved_count": len(saved),
        "saved": saved,
        "error_codes": error_codes,
        "xspa": bool(xspa),
        "cfg": bool(cfg_blob),
    }
    (OUT / "_probe_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("[probe] DONE saved=", len(saved), "summary=", OUT / "_probe_summary.json")
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())

