# -*- coding: utf-8 -*-
"""Signer microservice — wraps Frida(emulators) / Unidbg / stub for HTTP workers.

POST /sign  {"url": "...", "method": "GET", "body": null, "headers": {}}
→ {"ok": true, "headers": {"x-sap-ri": "...", "af-ac-enc-dat": "...", ...}}

Until a real backend returns valid headers, workers will still see 90309999.
This service is the scale choke-point once SHPSSDK signing works.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "shopee_getpc_crawler"), str(HERE), str(HERE / "br_emulator")]

LOG = Path(__file__).resolve().parent / "signer_service.log"


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


class SignerBackend:
    def sign(self, url: str, method: str = "GET", body: bytes | None = None,
             headers: dict | None = None) -> dict[str, Any]:
        raise NotImplementedError


class StubBackend(SignerBackend):
    """Placeholder — documents required interface; does NOT unlock get_pc."""

    def sign(self, url: str, method: str = "GET", body: bytes | None = None,
             headers: dict | None = None) -> dict[str, Any]:
        return {
            "ok": False,
            "reason": "stub_backend — wire Frida SHPSSDK or Unidbg libshpssdk.so",
            "headers": {},
            "hint": (
                "1) Install com.shopee.br on emulator  "
                "2) frida -U -f com.shopee.br -l br_emulator/frida_signer.js  "
                "3) Fill SIGN_SPEC after observing x-sap-* headers  "
                "4) Restart with --backend frida"
            ),
        }


class UnidbgBackend(SignerBackend):
    """Best-effort bridge to D:\\emu\\shopee_signer if present."""

    def __init__(self) -> None:
        self.jar = Path(r"D:\emu\shopee_signer\target\shopee-signer-1.0.0-shaded.jar")
        self.ok = self.jar.is_file()
        if not self.ok:
            log(f"unidbg jar missing: {self.jar}")

    def sign(self, url: str, method: str = "GET", body: bytes | None = None,
             headers: dict | None = None) -> dict[str, Any]:
        if not self.ok:
            return {"ok": False, "reason": "unidbg_jar_missing", "headers": {}}
        # Existing probes showed SESSION_READY but get_pc still 90309999 —
        # expose interface for when softToken/init is solved.
        return {
            "ok": False,
            "reason": "unidbg_not_acceptance_ready — see _br_shaded_live_probe (90309999)",
            "headers": {},
            "jar": str(self.jar),
        }


class FridaBackend(SignerBackend):
    """Attach to emulator Shopee via compiled Frida Java-bridge agent.

    Returns SHPSSDK map (includes x-sap-ri + opaque fields). Live get_pc still
    needs af-ac-enc-sz-token + correct header mapping — see D:\\emu\\frida_live_*.
    """

    def __init__(self, target: str = "com.shopee.id", device_id: str = "emulator-5554") -> None:
        self.target = target
        self.device_id = device_id
        self._signer = None
        try:
            from br_emulator.signer_bridge import FridaSigner
            self._signer = FridaSigner(target, spawn=False, device_id=device_id)
            self._signer.start()
            log(f"frida backend attached target={target} device={device_id}")
        except Exception as e:
            log(f"frida backend init failed: {e}")

    def sign(self, url: str, method: str = "GET", body: bytes | None = None,
             headers: dict | None = None) -> dict[str, Any]:
        if not self._signer:
            return {"ok": False, "reason": "frida_not_attached", "headers": {}}
        try:
            raw = self._signer.sign(url)
            if not raw:
                return {"ok": False, "reason": "empty_sign", "headers": {}}
            blob = raw
            if isinstance(raw, str) and raw.startswith("{"):
                try:
                    blob = json.loads(raw)
                except Exception:
                    blob = {"_raw": raw}
            out_headers: dict[str, str] = {}
            if isinstance(blob, dict):
                if blob.get("x-sap-ri"):
                    out_headers["x-sap-ri"] = str(blob["x-sap-ri"])
                # longest string value is typically the main sap payload
                strs = [(k, v) for k, v in blob.items() if isinstance(v, str) and k != "x-sap-ri"]
                strs.sort(key=lambda kv: len(kv[1]), reverse=True)
                if strs:
                    out_headers["x-sap-sec"] = strs[0][1]
                    out_headers["af-ac-enc-dat"] = strs[0][1]
                out_headers["_sign_blob"] = json.dumps(blob, separators=(",", ":"))[:4000]
            else:
                out_headers["x-sap-sec"] = str(blob)
            return {"ok": True, "headers": out_headers, "raw": blob}
        except Exception as e:
            return {"ok": False, "reason": f"frida_sign:{e}", "headers": {}}


BACKEND: SignerBackend = StubBackend()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter
        pass

    def _json(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json(200, {"ok": True, "backend": type(BACKEND).__name__})
            return
        self._json(404, {"ok": False, "reason": "not_found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/sign":
            self._json(404, {"ok": False, "reason": "not_found"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            self._json(400, {"ok": False, "reason": "bad_json"})
            return
        url = req.get("url") or ""
        if not url:
            self._json(400, {"ok": False, "reason": "url_required"})
            return
        body = req.get("body")
        if isinstance(body, str):
            body_b = body.encode("utf-8")
        elif isinstance(body, (bytes, bytearray)):
            body_b = bytes(body)
        else:
            body_b = None
        out = BACKEND.sign(url, method=req.get("method") or "GET", body=body_b,
                           headers=req.get("headers") or {})
        self._json(200 if out.get("ok") else 503, out)


def main() -> int:
    global BACKEND
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--backend", choices=("stub", "unidbg", "frida"), default="stub")
    ap.add_argument("--frida-target", default="com.shopee.id")
    ap.add_argument("--frida-device", default="emulator-5554")
    args = ap.parse_args()

    if args.backend == "stub":
        BACKEND = StubBackend()
    elif args.backend == "unidbg":
        BACKEND = UnidbgBackend()
    else:
        BACKEND = FridaBackend(args.frida_target, device_id=args.frida_device)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    log(f"signer_service {args.backend} on http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
