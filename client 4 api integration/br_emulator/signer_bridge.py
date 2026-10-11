# -*- coding: utf-8 -*-
"""Frida ↔ Python 서명 브리지 — ios_client 의 signer 슬롯에 앱 서명을 주입.

두 가지 사용:
  1) 탐색 모드: 앱을 spawn/attach 하고 frida_signer.js 를 주입해 서명 헤더 이름과
     서명 후보 클래스를 로그로 확인(정확한 서명 함수 확정용).
       python -m br_emulator.signer_bridge --spawn com.shopee.br
  2) 서명 제공 모드: frida_signer.js 의 SIGN_SPEC 가 확정되면, make_signer() 가
     반환하는 콜러블을 emulator_fleet 이 IosMobileClient(signer=...) 로 사용.

서명을 위조하지 않는다 — 에뮬 안의 진짜 앱 코드가 계산한 서명만 사용한다.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

_DIR = Path(__file__).resolve().parent
for _p in (str(_DIR), str(_DIR.parent), str(_DIR.parent.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

JS_PATH = _DIR / "frida_signer.js"
# Frida 17+: Java bridge must be compiled in (see D:\emu\frida_agent).
COMPILED_JS = Path(r"D:\emu\frida_agent\sign_agent_compiled.js")


def _log(msg: str) -> None:
    print(f"[emu-frida] {msg}", flush=True)


class FridaSigner:
    """frida-server(에뮬 내부)에 연결해 서명 함수를 호출하는 브리지."""

    def __init__(self, target: str, *, spawn: bool = False, device_id: Optional[str] = None):
        self.target = target
        self.spawn = spawn
        self.device_id = device_id
        self._script = None
        self._session = None
        self._pid = None
        self._lock = threading.Lock()
        self._ready = threading.Event()

    def _on_message(self, message, data):
        if message.get("type") == "send":
            p = message.get("payload") or {}
            t, m = p.get("t"), p.get("m")
            if t in ("info", "scan"):
                _log(f"{t}: {m}")
            elif t == "candidate":
                _log(f"  후보 클래스: {m}")
            elif t == "sig-header":
                _log(f"  🔏 서명 헤더 관찰: {m}")
            elif t == "warn":
                _log(f"⚠ {m}")
        elif message.get("type") == "error":
            _log(f"JS 오류: {message.get('stack') or message.get('description')}")

    def start(self) -> None:
        import frida

        if self.device_id:
            try:
                dev = frida.get_device_manager().get_device(self.device_id)
            except Exception:
                dev = frida.get_device(self.device_id)
        else:
            dev = frida.get_usb_device(timeout=8) if _has_usb() else frida.get_remote_device()
        if self.spawn:
            _log(f"앱 spawn: {self.target}")
            self._pid = dev.spawn([self.target])
            self._session = dev.attach(self._pid)
        else:
            _log(f"앱 attach: {self.target}")
            try:
                self._session = dev.attach(self.target)
            except Exception as first:
                # AVD often exposes display name ("Shopee") not package id.
                pid = None
                try:
                    t = str(self.target).lower().strip()
                    procs = list(dev.enumerate_processes())
                    # Exact name first, then package-style contains (min length 4).
                    for p in procs:
                        n = (p.name or "").lower().strip()
                        if n == t:
                            pid = p.pid
                            _log(f"exact-name attach: pid={pid} name={p.name}")
                            break
                    if pid is None and len(t) >= 4:
                        for p in procs:
                            n = (p.name or "").lower().strip()
                            if t in n or (len(n) >= 4 and n in t):
                                pid = p.pid
                                _log(f"이름 매칭 attach: pid={pid} name={p.name}")
                                break
                    if pid is None and "shopee" in t:
                        for p in procs:
                            if (p.name or "").lower().strip() == "shopee":
                                pid = p.pid
                                _log(f"Shopee display-name attach: pid={pid}")
                                break
                except Exception:
                    pass
                if pid is None:
                    raise first
                self._session = dev.attach(pid)
        if COMPILED_JS.is_file():
            code = COMPILED_JS.read_text(encoding="utf-8")
            _log(f"load compiled agent: {COMPILED_JS}")
        else:
            code = JS_PATH.read_text(encoding="utf-8")
            _log(f"load raw js (may lack Java bridge on Frida 17+): {JS_PATH}")
        self._script = self._session.create_script(code)
        self._script.on("message", self._on_message)
        self._script.load()
        if self.spawn and self._pid:
            dev.resume(self._pid)
        self._ready.set()
        _log("서명 브리지 로드 완료")

    def sign(self, input_str: str) -> Optional[str]:
        if not self._script:
            return None
        with self._lock:
            try:
                return self._script.exports_sync.sign(input_str)
            except Exception as e:
                _log(f"sign 호출 실패: {e}")
                return None

    def observed(self) -> dict:
        if not self._script:
            return {}
        try:
            return self._script.exports_sync.observed() or {}
        except Exception:
            return {}

    def make_signer(self) -> Callable[[str, dict, dict], dict]:
        """ios_client.IosMobileClient(signer=...) 용 콜러블 반환.

        signer(url, headers, cookies) -> headers  (서명 헤더 주입)
        서명 함수(SIGN_SPEC)가 아직 미확정이면 헤더를 그대로 돌려준다(플릿은
        need_app_sign 보고 → 브라우저/mitm 경로로 폴백).
        """
        def _signer(url: str, headers: dict, cookies: dict) -> dict:
            sig = self.sign(url)
            if sig:
                # 실제 서명 포맷에 맞춰 헤더명 매핑(관찰된 헤더명 사용).
                headers["x-sap-sec"] = sig
            return headers
        return _signer


def _has_usb() -> bool:
    try:
        import frida
        frida.get_usb_device(timeout=1)
        return True
    except Exception:
        return False


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Frida 서명 브리지(탐색/서명 제공)")
    ap.add_argument("--spawn", metavar="PKG", help="앱을 spawn 해서 후킹 (예: com.shopee.br)")
    ap.add_argument("--attach", metavar="PKG", help="실행 중 앱에 attach")
    ap.add_argument("--device", default=None, help="frida 기기 id (adb serial)")
    ap.add_argument("--watch", type=int, default=60, help="탐색 로그 관찰 시간(초)")
    args = ap.parse_args()

    target = args.spawn or args.attach
    if not target:
        ap.print_help()
        return 1
    signer = FridaSigner(target, spawn=bool(args.spawn), device_id=args.device)
    try:
        signer.start()
    except Exception as e:
        _log(f"시작 실패: {e}")
        _log("점검: 에뮬 내 frida-server 실행 여부, adb 연결, 패키지명, frida 버전 일치")
        return 2
    _log(f"{args.watch}s 동안 서명 헤더/후보 클래스를 관찰합니다… (Ctrl+C 중단)")
    try:
        time.sleep(args.watch)
    except KeyboardInterrupt:
        pass
    obs = signer.observed()
    if obs:
        _log(f"관찰된 서명 헤더 세트: {list(obs.keys())[:10]}")
    _log("탐색 종료. frida_signer.js 의 SIGN_SPEC 를 확정하면 서명 제공 모드로 전환됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
