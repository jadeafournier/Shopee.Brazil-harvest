# -*- coding: utf-8 -*-
"""Same-session Unidbg SHPS: live /v3/config apply (65532) then get_pc with full URL."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue
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
DF_HOST = "https://df.infra.sz.shopee.co.id"
DF_CONFIG = f"{DF_HOST}/v3/config"


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
                user_enc = quote(user, safe="-_")
                proxies.append(f"http://{user_enc}:{pwd}@{host}:{port}")
    return cookie, proxies


def parse_ids(url: str) -> tuple[str, str]:
    m = re.search(r"i\.(\d+)\.(\d+)", url) or re.search(r"/product/(\d+)/(\d+)", url)
    if not m:
        raise ValueError(url)
    return m.group(1), m.group(2)


def pick_urls(n: int = 8) -> list[str]:
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
            line = line.strip()
            if line.startswith("http") and "shopee.com.br" in line:
                urls.append(line)
            if len(urls) >= n + 20:
                break
    # de-dupe preserve order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= n:
            break
    return out


class SignerSession:
    def __init__(self, retries: int = 3):
        last_err: Exception | None = None
        self.proc = None  # type: ignore
        for attempt in range(1, retries + 1):
            try:
                env = {**dict(**{k: v for k, v in __import__("os").environ.items()})}
                # Prevent stale cfg / xspa leaking into session JVM
                for k in ("SIGNER_CFG", "SIGNER_CFG_DATA", "SIGNER_XSPA", "SIGNER_BODY_B64", "SIGNER_BODY_TEXT"):
                    env.pop(k, None)
                env["SIGNER_SKIP_STALE_CFG"] = "1"
                env["JAVA_TOOL_OPTIONS"] = (env.get("JAVA_TOOL_OPTIONS") or "") + " -Dfile.encoding=UTF-8"
                self.proc = subprocess.Popen(
                    [str(JDK), "-jar", str(JAR), "session", str(SO), str(APK)],
                    cwd=str(JAR.parent),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=env,
                )
                self.q: Queue[str] = Queue()
                self._t = threading.Thread(target=self._reader, daemon=True)
                self._t.start()
                self._wait_marker("SESSION_READY", timeout=150)
                print(f"[sess] signer ready (attempt {attempt})", flush=True)
                return
            except Exception as e:
                last_err = e
                print(f"[sess] signer start fail attempt {attempt}: {e}", flush=True)
                try:
                    if self.proc and self.proc.poll() is None:
                        self.proc.kill()
                except Exception:
                    pass
                time.sleep(2)
        raise RuntimeError(f"signer session failed after {retries}: {last_err}")

    def _reader(self):
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.q.put(line)
            if line.startswith("[signer] RESULT=") or line.startswith("[signer] CONFIG_BODY=") or "CMD_ERR" in line:
                print(line[:220], flush=True)
            elif "[signer]" in line or "[jni-bridge]" in line:
                if any(x in line for x in ("6553", "2048", "APPLY", "SESSION", "skip", "loaded", "vuwuuwvv")):
                    print(line[:220], flush=True)

    def _wait_marker(self, marker: str, timeout: float = 90) -> list[str]:
        lines = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self.q.get(timeout=0.5)
            except Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"signer died rc={self.proc.returncode}")
                continue
            lines.append(line)
            if marker in line:
                return lines
        raise TimeoutError(f"timeout waiting for {marker}; last={lines[-5:]}")

    def cmd(self, command: str, wait_for: str, timeout: float = 90) -> list[str]:
        assert self.proc.stdin is not None
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()
        return self._wait_marker(wait_for, timeout=timeout)

    def config_body(self) -> str:
        lines = self.cmd("CONFIG_BODY", "CONFIG_BODY=", timeout=60)
        for line in reversed(lines):
            if "[signer] CONFIG_BODY=" in line:
                return line.split("CONFIG_BODY=", 1)[1].strip()
        raise RuntimeError("no CONFIG_BODY")

    def apply_cfg(self, data: str) -> int:
        path = JAR.parent / "_session_cfg_data.txt"
        path.write_text(data, encoding="utf-8")
        lines = self.cmd(f"APPLY_CFG @{path}", "APPLY_CFG status=", timeout=60)
        for line in reversed(lines):
            if "APPLY_CFG status=" in line:
                m = re.search(r"status=(-?\d+)", line)
                return int(m.group(1)) if m else -9
        return -9

    def xspa(self, token: str) -> None:
        path = JAR.parent / "_session_xspa.txt"
        path.write_text(token, encoding="utf-8")
        self.cmd(f"XSPA @{path}", "XSPA_OK", timeout=30)

    def sign(self, url: str, body_text: str | None = None) -> dict:
        if body_text is not None:
            path = JAR.parent / "_session_sign_body.txt"
            path.write_text(body_text, encoding="utf-8")
            lines = self.cmd(f"SIGN {url} @{path}", "RESULT=", timeout=90)
        else:
            lines = self.cmd(f"SIGN {url}", "RESULT=", timeout=90)
        for line in reversed(lines):
            if "[signer] RESULT=" in line:
                raw = line.split("RESULT=", 1)[1].strip()
                return json.loads(raw)
        raise RuntimeError("no RESULT")

    def close(self):
        try:
            if self.proc.stdin and self.proc.poll() is None:
                self.proc.stdin.write("QUIT\n")
                self.proc.stdin.flush()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()


def map_headers(sig: dict, mode: str) -> dict:
    """mode: raw | sap | dual | shortmap"""
    out = dict(sig)
    longest_k = max(sig, key=lambda k: len(str(sig[k])))
    shorts = sorted(
        [(k, v) for k, v in sig.items() if k != "x-sap-ri" and k != longest_k],
        key=lambda kv: len(str(kv[1])),
    )
    if mode == "raw":
        return out
    if mode == "sap":
        return {"x-sap-ri": sig.get("x-sap-ri", ""), "x-sap-sec": sig[longest_k]}
    if mode == "dual":
        out["x-sap-sec"] = sig[longest_k]
        if shorts:
            out["af-ac-enc-dat"] = str(shorts[0][1])
        return out
    if mode == "shortmap":
        # Map short values onto known web header names; keep hashed too
        out["x-sap-sec"] = sig[longest_k]
        if len(shorts) >= 1:
            out["af-ac-enc-dat"] = str(shorts[0][1])
        if len(shorts) >= 2:
            out["af-ac-enc-sz-token"] = str(shorts[1][1])
        return out
    return out


def companion() -> dict:
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


def save_ok(task_link: str, shop: str, item: str, data: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"output_{shop}_{item}.json"
    env = {
        "task_link": task_link,
        "data": data,
        "error": None,
    }
    path.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cookie, proxies = load_proxy_cookie()
    proxy = proxies[0] if proxies else None
    proxies_kw = {"http": proxy, "https": proxy} if proxy else None
    print("[sess] proxy=", (proxy or "")[:100], "cookie_len=", len(cookie), flush=True)

    before = len(list(OUT.glob("output_*.json")))
    sess = SignerSession()
    error_codes = []
    saved = []
    cfg_ok = False
    xspa = None

    try:
        # --- same-session live config ---
        body_b64 = sess.config_body()
        print("[sess] config body b64 len", len(body_b64), flush=True)
        sig_cfg = sess.sign(DF_CONFIG, body_text=body_b64)
        print("[sess] cfg sig keys", list(sig_cfg.keys()), {k: len(str(v)) for k, v in sig_cfg.items()}, flush=True)

        hdrs = {
            **companion(),
            "Content-Type": "text/plain",
            "postType": "0",
            "szdet": str(int(time.time() * 1000)),
            **sig_cfg,
        }
        r = requests.post(
            DF_CONFIG,
            data=body_b64.encode("utf-8"),
            headers=hdrs,
            proxies=proxies_kw,
            timeout=45,
        )
        print("[sess] /v3/config st=", r.status_code, "n=", len(r.content), "body=", r.text[:180], flush=True)
        xspa = r.headers.get("X-Sp-A") or r.headers.get("x-sp-a")
        cfg_data = None
        try:
            j = r.json()
            print("[sess] config code=", j.get("code"), "msg=", j.get("msg"), flush=True)
            if j.get("code") == 0 and j.get("data"):
                cfg_data = j["data"] if isinstance(j["data"], str) else json.dumps(j["data"])
                cfg_ok = True
        except Exception as e:
            print("[sess] config json fail", e, flush=True)

        apply_st = -1
        if cfg_data:
            apply_st = sess.apply_cfg(cfg_data)
            print("[sess] APPLY_CFG status=", apply_st, flush=True)
            (OUT / "_session_cfg_apply.json").write_text(
                json.dumps({"code": 0 if cfg_ok else None, "apply_status": apply_st, "data_len": len(cfg_data)}, indent=2),
                encoding="utf-8",
            )
        if xspa:
            print("[sess] X-Sp-A len", len(xspa), flush=True)
            sess.xspa(xspa)

        # --- get_pc matrix ---
        urls = pick_urls(6)
        hosts = [
            ("https://shopee.co.id", "id", False),
            ("https://shopee.com.br", "br", True),
        ]
        modes = ["raw", "sap", "dual", "shortmap"]

        for ui, product_url in enumerate(urls):
            try:
                shop, item = parse_ids(product_url)
            except Exception:
                continue
            # Match real app/browser query shapes
            path_variants = [
                f"/api/v4/pdp/get_pc?shop_id={shop}&item_id={item}",
                f"/api/v4/pdp/get_pc?item_id={item}&shop_id={shop}&tz_offset_in_minutes=-180&detail_level=0",
            ]
            for host, region, use_cookie in hosts:
                for path in path_variants:
                    full = host + path
                    try:
                        sig = sess.sign(full)
                    except Exception as e:
                        print("[sess] sign fail", e, flush=True)
                        continue
                    for mode in modes:
                        headers = {
                            **companion(),
                            "Referer": host + "/",
                            "X-Shopee-Language": "pt-BR" if region == "br" else "id",
                            "X-Requested-With": "com.shopee.br" if region == "br" else "com.shopee.id",
                            **map_headers(sig, mode),
                        }
                        if use_cookie and cookie:
                            headers["Cookie"] = cookie
                            if "csrftoken=" in cookie:
                                headers["X-CSRFToken"] = cookie.split("csrftoken=")[1].split(";")[0]
                        try:
                            resp = requests.get(full, headers=headers, proxies=proxies_kw, timeout=45)
                            try:
                                body = resp.json()
                            except Exception:
                                body = {"_raw": resp.text[:400]}
                            err = body.get("error") if isinstance(body, dict) else None
                            data = body.get("data") if isinstance(body, dict) else None
                            item_obj = data.get("item") if isinstance(data, dict) else None
                            nkeys = len(item_obj) if isinstance(item_obj, dict) else 0
                            ok = err in (0, None) and nkeys >= 40
                            rec = {
                                "host": host,
                                "region": region,
                                "mode": mode,
                                "path": path[:80],
                                "st": resp.status_code,
                                "err": err,
                                "nkeys": nkeys,
                                "ok": ok,
                                "shop": shop,
                                "item": item,
                            }
                            error_codes.append(rec)
                            print(
                                f"[sess] {region} m={mode} st={resp.status_code} err={err} keys={nkeys} "
                                f"i.{shop}.{item}",
                                flush=True,
                            )
                            xa = resp.headers.get("X-Sp-A") or resp.headers.get("x-sp-a")
                            if xa:
                                sess.xspa(xa)
                            if ok:
                                p = save_ok(product_url, shop, item, data)
                                saved.append(str(p))
                                print("[sess] SAVED", p, flush=True)
                        except Exception as e:
                            error_codes.append({"host": host, "mode": mode, "err": f"EXC:{e}", "ok": False})
                            print("[sess] EXC", e, flush=True)
            # after first URL full matrix, if nothing works keep trying more URLs with best guess
            if saved:
                break
            if ui == 0:
                # shrink matrix for remaining URLs: BR + raw/dual only, richer path
                hosts = [("https://shopee.com.br", "br", True)]
                modes = ["raw", "dual"]
                path_variants = path_variants[-1:]

    finally:
        sess.close()

    after = len(list(OUT.glob("output_*.json")))
    summary = {
        "cfg_ok": cfg_ok,
        "apply_status_note": "see _session_cfg_apply.json",
        "saved_count": len(saved),
        "saved": saved,
        "new_files": after - before,
        "total_deliverables": after,
        "error_codes": error_codes[:40],
        "unique_errs": sorted({str(e.get("err")) for e in error_codes}),
        "notes": "same-session 65533->POST native body->65532 then full-URL get_pc sign",
    }
    (OUT / "_session_probe_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("[sess] DONE saved=", len(saved), "new=", after - before, "total=", after, "errs=", summary["unique_errs"], flush=True)
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
