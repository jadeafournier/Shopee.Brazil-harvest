# -*- coding: utf-8 -*-
"""Boot shaded-jar session, same-session /v3/config apply, probe get_pc. SHADED JAR ONLY."""
from __future__ import annotations

import json
import os
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
DF_CONFIG = "https://df.infra.sz.shopee.co.id/v3/config"
WORKDIR = JAR.parent


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


def pick_urls(n: int = 5) -> list[str]:
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
            if len(urls) >= n + 10:
                break
    out, seen = [], set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= n:
            break
    return out


class SignerSession:
    def __init__(self, retries: int = 4):
        assert JAR.is_file(), f"missing shaded jar: {JAR}"
        last_err = None
        self.proc = None
        for attempt in range(1, retries + 1):
            try:
                env = os.environ.copy()
                for k in ("SIGNER_CFG", "SIGNER_CFG_DATA", "SIGNER_XSPA", "SIGNER_BODY_B64", "SIGNER_BODY_TEXT", "SIGNER_SOFTTOKEN"):
                    env.pop(k, None)
                env["SIGNER_SKIP_STALE_CFG"] = "1"
                # vuwuuwvv often UC_ERR hang under Unicorn in session mode
                env["SIGNER_SKIP_APPID"] = "1"
                self.proc = subprocess.Popen(
                    [str(JDK), "-jar", str(JAR), "session", str(SO), str(APK)],
                    cwd=str(WORKDIR),
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
                threading.Thread(target=self._reader, daemon=True).start()
                self._wait("SESSION_READY", 100)
                print(f"[probe] session ready attempt={attempt}", flush=True)
                return
            except Exception as e:
                last_err = e
                print(f"[probe] boot fail {attempt}: {str(e).encode('ascii','replace').decode('ascii')}", flush=True)
                try:
                    if self.proc and self.proc.poll() is None:
                        self.proc.kill()
                except Exception:
                    pass
                time.sleep(1.5)
        raise RuntimeError(f"session boot failed: {last_err}")

    def _reader(self):
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.q.put(line)
            if any(x in line for x in ("SESSION_", "CONFIG_BODY=", "APPLY_CFG", "RESULT=", "65533", "65532", "softToken", "loaded", "BOOT_FAIL", "CMD_ERR", "skipped")):
                try:
                    print(line[:240].encode("ascii", "replace").decode("ascii"), flush=True)
                except Exception:
                    pass

    def _wait(self, marker: str, timeout: float) -> list[str]:
        lines = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self.q.get(timeout=0.4)
            except Empty:
                if self.proc and self.proc.poll() is not None:
                    raise RuntimeError(f"signer died rc={self.proc.returncode}; last={lines[-8:]}")
                continue
            lines.append(line)
            if marker in line:
                return lines
        raise TimeoutError(f"timeout {marker}; last={lines[-8:]}")

    def cmd(self, command: str, wait_for: str, timeout: float = 90) -> list[str]:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()
        return self._wait(wait_for, timeout)

    def config_body(self) -> str:
        for line in reversed(self.cmd("CONFIG_BODY", "CONFIG_BODY=", 60)):
            if "CONFIG_BODY=" in line:
                return line.split("CONFIG_BODY=", 1)[1].strip()
        raise RuntimeError("no config body")

    def apply_cfg(self, data: str) -> int:
        path = WORKDIR / "_live_cfg_data.txt"
        path.write_text(data, encoding="utf-8")
        for line in reversed(self.cmd(f"APPLY_CFG @{path}", "APPLY_CFG status=", 60)):
            if "APPLY_CFG status=" in line:
                m = re.search(r"status=(-?\d+)", line)
                return int(m.group(1)) if m else -9
        return -9

    def xspa(self, token: str) -> None:
        path = WORKDIR / "_live_xspa.txt"
        path.write_text(token, encoding="utf-8")
        self.cmd(f"XSPA @{path}", "XSPA_OK", 30)

    def sign(self, url: str, body_text: str | None = None) -> dict:
        if body_text is not None:
            path = WORKDIR / "_live_sign_body.txt"
            path.write_text(body_text, encoding="utf-8")
            lines = self.cmd(f"SIGN {url} @{path}", "RESULT=", 90)
        else:
            lines = self.cmd(f"SIGN {url}", "RESULT=", 90)
        for line in reversed(lines):
            if "RESULT=" in line:
                return json.loads(line.split("RESULT=", 1)[1].strip())
        raise RuntimeError("no RESULT")

    def close(self):
        try:
            if self.proc and self.proc.poll() is None and self.proc.stdin:
                self.proc.stdin.write("QUIT\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=10)
        except Exception:
            try:
                if self.proc:
                    self.proc.kill()
            except Exception:
                pass


def hdr_modes(sig: dict) -> list[tuple[str, dict]]:
    longest = max(sig, key=lambda k: len(str(sig[k])))
    shorts = [(k, v) for k, v in sig.items() if k not in ("x-sap-ri", longest)]
    shorts.sort(key=lambda kv: len(str(kv[1])))
    raw = dict(sig)
    sap = {"x-sap-ri": sig.get("x-sap-ri", ""), "x-sap-sec": sig[longest]}
    dual = dict(sig)
    dual["x-sap-sec"] = sig[longest]
    if shorts:
        dual["af-ac-enc-dat"] = str(shorts[0][1])
    return [("raw", raw), ("sap", sap), ("dual", dual)]


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


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    before = len(list(OUT.glob("output_*.json")))
    cookie, proxies = load_proxy_cookie()
    proxy = proxies[0] if proxies else None
    px = {"http": proxy, "https": proxy} if proxy else None
    print("[probe] shaded=", JAR.name, "proxy=", (proxy or "")[:90], "before=", before, flush=True)

    sess = SignerSession()
    saved, errors = [], []
    apply_st = None
    cfg_code = None
    try:
        body_b64 = sess.config_body()
        sig_cfg = sess.sign(DF_CONFIG, body_text=body_b64)
        print("[probe] cfg sig", {k: len(str(v)) for k, v in sig_cfg.items()}, flush=True)
        hdrs = {
            **companion(),
            "Content-Type": "text/plain",
            "postType": "0",
            "szdet": str(int(time.time() * 1000)),
            **sig_cfg,
        }
        r = requests.post(DF_CONFIG, data=body_b64.encode("utf-8"), headers=hdrs, proxies=px, timeout=45)
        print("[probe] config HTTP", r.status_code, r.text[:160], flush=True)
        xspa = r.headers.get("X-Sp-A") or r.headers.get("x-sp-a")
        try:
            cj = r.json()
            cfg_code = cj.get("code")
            data = cj.get("data")
            if cfg_code == 0 and data:
                cfg_data = data if isinstance(data, str) else json.dumps(data)
                apply_st = sess.apply_cfg(cfg_data)
                print("[probe] APPLY_CFG", apply_st, "data_len", len(cfg_data), flush=True)
        except Exception as e:
            print("[probe] config parse", e, flush=True)
        if xspa:
            sess.xspa(xspa)

        for product in pick_urls(4):
            shop, item = parse_ids(product)
            paths = [
                f"/api/v4/pdp/get_pc?shop_id={shop}&item_id={item}",
                f"/api/v4/pdp/get_pc?item_id={item}&shop_id={shop}&tz_offset_in_minutes=-180&detail_level=0",
            ]
            for host, region, use_ck in (
                ("https://shopee.co.id", "id", False),
                ("https://shopee.com.br", "br", True),
            ):
                for path in paths:
                    full = host + path
                    try:
                        sig = sess.sign(full)
                    except Exception as e:
                        errors.append({"err": f"sign:{e}", "ok": False})
                        continue
                    for mode, extra in hdr_modes(sig):
                        headers = {
                            **companion(),
                            "Referer": host + "/",
                            "X-Shopee-Language": "pt-BR" if region == "br" else "id",
                            **extra,
                        }
                        if use_ck and cookie:
                            headers["Cookie"] = cookie
                            if "csrftoken=" in cookie:
                                headers["X-CSRFToken"] = cookie.split("csrftoken=")[1].split(";")[0]
                        try:
                            resp = requests.get(full, headers=headers, proxies=px, timeout=40)
                            body = resp.json() if resp.content else {}
                            err = body.get("error") if isinstance(body, dict) else None
                            data = body.get("data") if isinstance(body, dict) else None
                            item_obj = data.get("item") if isinstance(data, dict) else None
                            nkeys = len(item_obj) if isinstance(item_obj, dict) else 0
                            ok = err in (0, None) and nkeys >= 40
                            rec = {"region": region, "mode": mode, "st": resp.status_code, "err": err, "nkeys": nkeys, "ok": ok, "shop": shop, "item": item}
                            errors.append(rec)
                            print(f"[probe] {region}/{mode} st={resp.status_code} err={err} keys={nkeys}", flush=True)
                            xa = resp.headers.get("X-Sp-A") or resp.headers.get("x-sp-a")
                            if xa:
                                try:
                                    sess.xspa(xa)
                                except Exception:
                                    pass
                            if ok:
                                outp = OUT / f"output_{shop}_{item}.json"
                                outp.write_text(
                                    json.dumps({"task_link": product, "data": data, "error": None}, ensure_ascii=False),
                                    encoding="utf-8",
                                )
                                saved.append(str(outp))
                                print("[probe] SAVED", outp, flush=True)
                        except Exception as e:
                            errors.append({"err": f"EXC:{e}", "ok": False})
            if saved:
                break
    finally:
        sess.close()

    after = len(list(OUT.glob("output_*.json")))
    uniq = sorted({str(e.get("err")) for e in errors})
    summary = {
        "cfg_code": cfg_code,
        "apply_status": apply_st,
        "saved_count": len(saved),
        "new_files": after - before,
        "total": after,
        "unique_errs": uniq,
        "sample_errors": errors[:20],
        "jar": str(JAR),
    }
    (OUT / "_shaded_live_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("[probe] DONE", json.dumps({k: summary[k] for k in ("cfg_code", "apply_status", "saved_count", "new_files", "total", "unique_errs")}), flush=True)
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
