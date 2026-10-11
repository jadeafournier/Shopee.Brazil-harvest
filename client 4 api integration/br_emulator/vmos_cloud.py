# -*- coding: utf-8 -*-
"""VMOS Cloud Phone ADB 연결.

데스크톱에 로그인된 VMOS 세션(쿠키 Token)으로 클라우드폰 목록을 읽고
ADB를 켠 뒤 SSH 터널 + adb connect 한다.

실행:
  python -m br_emulator.vmos_cloud connect
  python -m br_emulator.vmos_cloud status
  python -m br_emulator.vmos_cloud capture
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_DIR = Path(__file__).resolve().parent
ROOT = _DIR.parent.parent
RUNTIME = ROOT / "runtime" / "vmos_cloud"
SESSION = RUNTIME / "session.json"
COOKIES = Path(os.environ.get(
    "VMOS_COOKIES",
    os.path.expandvars(r"%APPDATA%\vmoscloud\QtWebEngine\vmoscloud-qt\Cookies"),
))
API = "https://api.vmoscloud.com/vcpcloud"
ADB_CANDIDATES = (
    ROOT / "emu_setup" / "platform-tools" / "adb.exe",
    Path(r"D:\R\Work\Great Scraper\emu_setup\platform-tools\adb.exe"),
    Path(r"D:\Great Scraper\Great Scraper\emu_setup\platform-tools\adb.exe"),
)


def _log(msg: str) -> None:
    print(msg, flush=True)


def adb_bin() -> str:
    env = os.environ.get("ADB") or os.environ.get("ANDROID_HOME")
    if env:
        p = Path(env)
        if p.is_file():
            return str(p)
        cand = p / "platform-tools" / "adb.exe"
        if cand.is_file():
            return str(cand)
    for p in ADB_CANDIDATES:
        if p.is_file():
            return str(p)
    return "adb"


def desktop_session() -> dict:
    if not COOKIES.is_file():
        raise RuntimeError("VMOS 데스크톱 쿠키가 없습니다. VMOS Cloud에 로그인하세요.")
    con = sqlite3.connect(str(COOKIES))
    try:
        rows = {
            n: v
            for _h, n, v in con.execute(
                "select host_key, name, value from cookies where host_key like '%vmos%'"
            )
        }
    finally:
        con.close()
    token = rows.get("token") or ""
    user_id = rows.get("userId") or ""
    if not token or not user_id:
        raise RuntimeError("VMOS 로그인 토큰이 없습니다. 데스크톱에서 다시 로그인하세요.")
    return {"token": token, "userId": user_id, "deviceKey": rows.get("deviceKey") or ""}


def api(path: str, *, method: str = "GET", body: dict | None = None, params: dict | None = None) -> dict:
    sess = desktop_session()
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "en",
        "Token": sess["token"],
        "Authorization": sess["token"],
        "userId": sess["userId"],
        "clientType": "web",
        "appVersion": "3.9.232332",
        "requestsource": "wechat-miniapp",
        "SupplierType": "0",
        "Origin": "https://cloud.vmoscloud.com",
        "Referer": "https://cloud.vmoscloud.com/",
        "User-Agent": "Mozilla/5.0",
    }
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"VMOS API {path} HTTP {exc.code}: {detail}") from exc
    if raw.get("code") != 200:
        raise RuntimeError(f"VMOS API {path}: {raw.get('msg') or raw}")
    return raw


def list_pads() -> list[dict]:
    raw = api("/api/userEquipment/list", method="GET")
    pads = []
    for group in raw.get("data") or []:
        for pad in group.get("userPads") or []:
            pads.append(pad)
    return pads


def pick_pad(pads: list[dict], pad_code: str | None = None) -> dict:
    if not pads:
        raise RuntimeError("클라우드폰이 없습니다.")
    if pad_code:
        for pad in pads:
            if pad.get("padCode") == pad_code:
                return pad
        raise RuntimeError(f"padCode {pad_code} 를 찾지 못했습니다.")
    online = [p for p in pads if int(p.get("cvmStatus") or 0) == 100]
    return (online or pads)[0]


def enable_adb(pad_code: str) -> dict:
    last = None
    for attempt in range(10):
        try:
            api(
                "/api/userEquipment/openOnlineAdb",
                method="POST",
                body={"padCode": pad_code, "enabled": True},
            )
        except RuntimeError as exc:
            last = {"msg": str(exc)}
            msg = str(exc).lower()
            if "permission verification" in msg or "reassigned" in msg:
                time.sleep(min(8.0, 2.0 + attempt * 0.8))
                continue
            raise
        for _ in range(6):
            try:
                last = api(
                    "/api/userEquipment/padAdb",
                    method="POST",
                    body={"padCode": pad_code, "enabled": True},
                )
                data = last.get("data") or {}
                if data.get("command") and data.get("adb"):
                    return data
            except RuntimeError as exc:
                last = {"msg": str(exc)}
                msg = str(exc).lower()
                if "permission verification" in msg or "reassigned" in msg:
                    break
            time.sleep(1.2)
        time.sleep(2.0)
    raise RuntimeError(f"ADB 연결 정보를 받지 못했습니다: {last}")


def _parse_adb_serial(adb_line: str) -> str:
    parts = (adb_line or "").split()
    if len(parts) >= 3 and parts[0] == "adb" and parts[1] == "connect":
        return parts[2]
    if "localhost:" in (adb_line or ""):
        return adb_line.split()[-1]
    return "localhost:5555"


def start_ssh_tunnel(command: str, key: str) -> None:
    work = Path(tempfile.mkdtemp(prefix="vmos_ssh_"))
    key_file = work / "key.txt"
    ask_py = work / "askpass.py"
    ask_cmd = work / "askpass.cmd"
    key_file.write_text(key, encoding="utf-8")
    ask_py.write_text(
        "from pathlib import Path\n"
        f"print(Path(r'{key_file}').read_text(encoding='utf-8'), end='')\n",
        encoding="utf-8",
    )
    py = sys.executable
    ask_cmd.write_text(f'@echo off\r\n"{py}" "{ask_py}"\r\n', encoding="utf-8")
    env = os.environ.copy()
    env["DISPLAY"] = "dummy"
    env["SSH_ASKPASS"] = str(ask_cmd)
    env["SSH_ASKPASS_REQUIRE"] = "force"
    extra = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "UserKnownHostsFile=NUL",
        "-o", "IdentitiesOnly=yes",
        "-o", "PreferredAuthentications=password",
        "-o", "NumberOfPasswordPrompts=1",
    ]
    argv = command.split()
    if argv and argv[0].lower() == "ssh":
        argv = [argv[0], *extra, *argv[1:]]
    creation = 0
    if os.name == "nt":
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        argv, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=creation,
    )
    time.sleep(2.5)


def adb_connect(serial: str) -> str:
    adb = adb_bin()
    out = subprocess.run([adb, "connect", serial], capture_output=True, text=True, timeout=20)
    text = ((out.stdout or "") + (out.stderr or "")).strip()
    if out.returncode != 0 and "connected" not in text.lower():
        raise RuntimeError(f"adb connect 실패: {text}")
    return text


def adb_devices() -> list[str]:
    out = subprocess.run([adb_bin(), "devices"], capture_output=True, text=True, timeout=15)
    lines = []
    for line in (out.stdout or "").splitlines():
        if "\tdevice" in line:
            lines.append(line.split("\t", 1)[0].strip())
    return lines


def save_session(payload: dict) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    safe = dict(payload)
    safe.pop("key", None)
    SESSION.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session() -> dict:
    if not SESSION.is_file():
        return {}
    try:
        return json.loads(SESSION.read_text(encoding="utf-8"))
    except Exception:
        return {}


def ensure_connected(pad_code: str | None = None) -> dict:
    pads = list_pads()
    pad = pick_pad(pads, pad_code or os.environ.get("VMOS_PAD_CODE"))
    code = pad["padCode"]
    _log(f"VMOS 클라우드폰 {code}  {pad.get('padName') or ''}  Android {pad.get('androidVersion') or '?'}")
    info = enable_adb(code)
    serial = _parse_adb_serial(info.get("adb") or "")
    start_ssh_tunnel(info.get("command") or "", info.get("key") or "")
    msg = adb_connect(serial)
    _log(f"ADB {msg}")
    devices = adb_devices()
    if serial not in devices and not any(serial.split(":")[-1] in d for d in devices):
        adb = adb_bin()
        subprocess.run([adb, "kill-server"], capture_output=True, timeout=8)
        time.sleep(1.0)
        subprocess.run([adb, "start-server"], capture_output=True, timeout=12)
        start_ssh_tunnel(info.get("command") or "", info.get("key") or "")
        msg = adb_connect(serial)
        _log(f"ADB 재시도 {msg}")
        devices = adb_devices()
    if serial not in devices and not any(serial.split(":")[-1] in d for d in devices):
        raise RuntimeError(f"adb devices 에 {serial} 이 없습니다: {devices}")
    os.environ["ANDROID_SERIAL"] = serial
    rec = {
        "padCode": code,
        "padName": pad.get("padName"),
        "androidVersion": pad.get("androidVersion"),
        "country": pad.get("armCountryMsg") or pad.get("countryCode"),
        "serial": serial,
        "adb": info.get("adb"),
        "expireTime": info.get("expireTime"),
        "devices": devices,
    }
    save_session(rec)
    return rec


def device_probe(serial: str | None = None) -> dict:
    serial = serial or load_session().get("serial") or os.environ.get("ANDROID_SERIAL")
    if not serial:
        raise RuntimeError("연결된 시리얼이 없습니다.")
    out = subprocess.run(
        [adb_bin(), "-s", serial, "shell", "pm", "list", "packages"],
        capture_output=True, text=True, timeout=20,
    )
    pkgs = [
        ln.replace("package:", "").strip()
        for ln in (out.stdout or "").splitlines()
        if ln.startswith("package:")
    ]
    shopee = [p for p in pkgs if "shopee" in p.lower()]
    return {"serial": serial, "packages": len(pkgs), "shopee": shopee}


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "connect").lower()
    if cmd == "capture":
        from br_emulator.vmos_app_capture import main as capture_main
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        return capture_main()
    if cmd == "status":
        sess = load_session()
        _log(json.dumps(sess, ensure_ascii=False, indent=2) if sess else "세션 없음")
        if sess.get("serial"):
            _log(json.dumps(device_probe(sess["serial"]), ensure_ascii=False))
        return 0
    if cmd == "list":
        for pad in list_pads():
            _log(f"{pad.get('padCode')}  {pad.get('padName')}  {pad.get('armCountryMsg')}  status={pad.get('cvmStatus')}")
        return 0
    rec = ensure_connected()
    try:
        rec["probe"] = device_probe(rec["serial"])
    except Exception as exc:
        rec["probe_error"] = str(exc)
    _log(json.dumps({k: rec[k] for k in rec if k != "key"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
