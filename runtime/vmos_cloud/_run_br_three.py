# -*- coding: utf-8 -*-
"""폰 3대 Change Region(BR) → Shopee 설치 → 세션 보관을 진행한다."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
RUNTIME = ROOT / "runtime" / "vmos_cloud"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
PHONES_PATH = RUNTIME / "phones.json"
CRED_PATH = RUNTIME / "vmos_credentials.json"
ADB = str(ROOT / "emu_setup" / "platform-tools" / "adb.exe")
APKS = [
    Path(r"D:\Luxury\Shopee.BR.Android\Shopee.BR.Android\runtime\vmos_cloud\apk_cache\xapk_unpack\com.shopee.br.apk"),
    Path(r"D:\Luxury\Shopee.BR.Android\Shopee.BR.Android\runtime\vmos_cloud\apk_cache\xapk_unpack\config.arm64_v8a.apk"),
    Path(r"D:\Luxury\Shopee.BR.Android\Shopee.BR.Android\runtime\vmos_cloud\apk_cache\xapk_unpack\config.xxxhdpi.apk"),
]
PKG = "com.shopee.br"
LOG = RUNTIME / "_run_br_three.log"


def _log(msg: str) -> None:
    line = msg.rstrip()
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _vmos():
    path = ROOT / "client 4 api integration" / "br_emulator" / "vmos_cloud.py"
    spec = importlib.util.spec_from_file_location("vmos_cloud", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _openapi(path: str, params: dict) -> dict:
    cred = json.loads(CRED_PATH.read_text(encoding="utf-8"))
    body = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
    ts = str(int(time.time()))
    sign = hashlib.sha256((cred["secretAccessKey"] + ts + path + body).encode("utf-8")).hexdigest()
    req = urllib.request.Request(
        "https://api.vmoscloud.com" + path,
        data=body.encode("utf-8"),
        headers={
            "X-Access-Key": cred["accessKeyId"],
            "X-Timestamp": ts,
            "X-Sign": sign,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _pads() -> list[dict]:
    raw = _openapi("/vcpcloud/api/padApi/userPadList", {})
    rows = raw.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("pageData") or rows.get("userPads") or []
    return rows if isinstance(rows, list) else []


def _row(rows: list[dict], code: str) -> dict:
    for row in rows:
        if str(row.get("padCode") or "") == code:
            return row
    return {}


def _online(row: dict) -> bool:
    status = row.get("cvmStatus", row.get("padStatus", row.get("status")))
    try:
        return int(status) in (100, 10, 1)
    except (TypeError, ValueError):
        return str(status).lower() in ("100", "online", "running")


def _country(row: dict) -> str:
    return str(row.get("armCountryMsg") or row.get("countryCode") or row.get("country") or "")


def _brazil_meta(row: dict) -> bool:
    if not row:
        return False
    for key in ("armCountryMsg", "countryCode", "country"):
        val = str(row.get(key) or "")
        if "brazil" in val.lower() or val.upper() in ("BR", "BRA"):
            return True
    return False


def change_region(phones: list[dict]) -> list[int]:
    codes = [p["pad"] for p in phones]
    body = {
        "padCodes": codes,
        "countryCode": "BR",
        "wipeData": True,
        "replacementRealAdiFlag": True,
    }
    last = None
    for attempt in range(8):
        try:
            raw = _openapi("/vcpcloud/api/padApi/replacePad", body)
            _log("replacePad " + json.dumps(raw, ensure_ascii=False)[:500])
            if raw.get("code") == 200:
                ids = [int(row["taskId"]) for row in (raw.get("data") or []) if row.get("taskId")]
                try:
                    import session_ledger
                    session_ledger.begin_round(phones, ids)
                except Exception as exc:
                    _log(f"세션 장부 기록 실패: {exc}")
                return ids
            last = raw
        except Exception as exc:
            last = str(exc)
            _log(f"replacePad 시도 {attempt + 1} 실패: {last}")
        time.sleep(8)
    raise RuntimeError(f"replacePad 실패: {last}")


def wait_tasks(task_ids: list[int], timeout: float = 720, on_tick=None) -> None:
    pending = set(task_ids)
    total = len(task_ids)
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        raw = _openapi("/vcpcloud/api/padApi/padTaskDetail", {"taskIds": list(pending)})
        for row in raw.get("data") or []:
            tid = row.get("taskId")
            if tid is None or int(tid) not in pending:
                continue
            status = row.get("taskStatus")
            _log(f"task {tid} {row.get('padCode')} status={status}")
            try:
                code = int(status)
            except (TypeError, ValueError):
                code = -1
            if code in (1, 2, 9):
                continue
            pending.discard(int(tid))
            if code != 3:
                raise RuntimeError(f"Change Region 실패 task={tid} status={status} {row}")
        if on_tick:
            on_tick(total - len(pending), total)
        if pending:
            time.sleep(12)
    if pending:
        raise RuntimeError(f"Change Region 시간 초과: {sorted(pending)}")


def wait_online(phones: list[dict], timeout: float = 720, on_tick=None) -> None:
    """replacePad(BR) 후 VMOS 목록 country 가 BR 로 안 바뀌는 HK 구매 폰도 있다 — 온라인이면 진행."""
    deadline = time.time() + timeout
    codes = [p["pad"] for p in phones]
    while time.time() < deadline:
        rows = _pads()
        bits = []
        ready_n = 0
        for code in codes:
            row = _row(rows, code)
            country = _country(row)
            on = bool(row) and _online(row)
            bits.append(f"{code} on={on} country={country or '?'}")
            if on:
                ready_n += 1
        _log("대기 " + " | ".join(bits))
        if on_tick:
            on_tick(ready_n, len(codes))
        if ready_n == len(codes):
            rows = _pads()
            for code in codes:
                row = _row(rows, code)
                if row and not _brazil_meta(row):
                    _log(
                        f"{code} VMOS 목록 country={_country(row)} — "
                        "replacePad BR 지문 적용·온라인 확인으로 설치 단계 진행"
                    )
            return
        time.sleep(15)
    raise RuntimeError("브라질 온라인 복귀 시간 초과")


def _adb_ok(port: str, serial: str) -> bool:
    out = subprocess.run(
        [ADB, "-P", port, "-s", serial, "shell", "echo", "ok"],
        capture_output=True, text=True, timeout=20,
    )
    return out.returncode == 0 and "ok" in (out.stdout or "")


def connect_phone(vm, phone: dict) -> str:
    pad = phone["pad"]
    port = str(phone["adb_port"])
    subprocess.run([ADB, "-P", port, "start-server"], capture_output=True, text=True, timeout=25)
    subprocess.run([ADB, "-P", port, "disconnect"], capture_output=True, text=True, timeout=15)
    info = vm.enable_adb(pad)
    serial = vm._parse_adb_serial(info.get("adb") or "")
    vm.start_ssh_tunnel(info.get("command") or "", info.get("key") or "")
    msg = ""
    for _ in range(6):
        out = subprocess.run(
            [ADB, "-P", port, "connect", serial],
            capture_output=True, text=True, timeout=25,
        )
        msg = ((out.stdout or "") + (out.stderr or "")).strip()
        if _adb_ok(port, serial):
            break
        time.sleep(3)
    if not _adb_ok(port, serial):
        raise RuntimeError(f"{phone['name']} ADB 응답 없음 {serial} {msg}")
    _log(f"{phone['name']} {serial} adb {port} {msg}")
    phone["serial"] = serial
    return serial


def _has_shopee(port: str, serial: str) -> bool:
    try:
        out = subprocess.run(
            [ADB, "-P", port, "-s", serial, "shell", "pm", "path", PKG],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False
    text = (out.stdout or "") + (out.stderr or "")
    return "package:" in text


def _device_state(port: str, serial: str) -> str:
    """adb get-state: 'device' when online, else offline/unknown/''"""
    try:
        out = subprocess.run(
            [ADB, "-P", port, "-s", serial, "get-state"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return ""
    return (out.stdout or "").strip()


def _ensure_online(phone: dict, vm, *, tries: int = 3) -> str:
    """Make sure the device is in 'device' state; reconnect via VMOS if offline."""
    port = str(phone["adb_port"])
    serial = str(phone.get("serial") or "")
    for attempt in range(max(1, tries)):
        if serial and _device_state(port, serial) == "device" and _adb_ok(port, serial):
            return serial
        if vm is None:
            break
        _log(f"{phone['name']} ADB 상태 비정상 — 재연결 시도 {attempt + 1}")
        try:
            serial = connect_phone(vm, phone)
        except Exception as exc:
            _log(f"{phone['name']} 재연결 실패: {exc}")
            time.sleep(4)
    return str(phone.get("serial") or serial)


REMOTE_TMP = "/data/local/tmp"
# Conservative throughput floor (bytes/sec) used to size push timeouts.
# The VMOS SSH tunnel can run as slow as ~60 KB/s, so a 100 MB app needs
# tens of minutes. Timeouts are a ceiling, not the expected duration.
_PUSH_FLOOR_BPS = 15 * 1024


def _adb(port: str, serial: str, args: list[str], *, timeout: float):
    return subprocess.run(
        [ADB, "-P", port, "-s", serial, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _remote_size(port: str, serial: str, path: str) -> int:
    try:
        out = _adb(port, serial, ["shell", "stat", "-c", "%s", path], timeout=25)
    except subprocess.TimeoutExpired:
        return -1
    try:
        return int((out.stdout or "").strip())
    except ValueError:
        return -1


def _push_apk(phone: dict, port: str, serial: str, src: Path, dst: str) -> None:
    """Push one APK, sizing the timeout to the slow tunnel and verifying bytes."""
    size = src.stat().st_size
    budget = max(600, int(size / _PUSH_FLOOR_BPS) + 180)
    for attempt in range(1, 4):
        _adb(port, serial, ["shell", "rm", "-f", dst], timeout=25)
        _log(f"{phone['name']} {src.name} 전송 {attempt}/3 "
             f"({size/1048576:.1f}MB, 최대 {budget}s)")
        try:
            _adb(port, serial, ["push", str(src), dst], timeout=budget)
        except subprocess.TimeoutExpired:
            _log(f"{phone['name']} {src.name} 전송 타임아웃 — 재시도")
            continue
        got = _remote_size(port, serial, dst)
        if got == size:
            return
        _log(f"{phone['name']} {src.name} 크기 불일치 {got}/{size} — 재전송")
    raise RuntimeError(f"{src.name} 전송 실패")


def _session_install(phone: dict, port: str, serial: str, label: str) -> str:
    """Push APKs once, then install from on-device files via pm session (instant)."""
    pushed: list[tuple[str, str, int]] = []
    for idx, src in enumerate(APKS):
        dst = f"{REMOTE_TMP}/shopee_split_{idx}.apk"
        _push_apk(phone, port, serial, dst=dst, src=src)
        pushed.append((f"split{idx}", dst, src.stat().st_size))
    total = sum(sz for _, _, sz in pushed)
    out = _adb(port, serial,
               ["shell", "pm", "install-create", "-r", "-g", "-S", str(total)],
               timeout=60)
    text = ((out.stdout or "") + (out.stderr or "")).strip()
    m = re.search(r"\[(\d+)\]", text) or re.search(r"session\s+(\d+)", text, re.I)
    if not m:
        raise RuntimeError(f"install-create 실패: {text[:200]}")
    sess = m.group(1)
    try:
        for name, dst, size in pushed:
            w = _adb(port, serial,
                     ["shell", "pm", "install-write", "-S", str(size), sess, name, dst],
                     timeout=180)
            wtext = ((w.stdout or "") + (w.stderr or "")).lower()
            if "success" not in wtext and "written" not in wtext:
                raise RuntimeError(f"install-write 실패({name}): {wtext[:160]}")
        c = _adb(port, serial, ["shell", "pm", "install-commit", sess], timeout=180)
        ctext = ((c.stdout or "") + (c.stderr or "")).strip()
    except Exception:
        _adb(port, serial, ["shell", "pm", "install-abandon", sess], timeout=30)
        raise
    finally:
        for _, dst, _sz in pushed:
            _adb(port, serial, ["shell", "rm", "-f", dst], timeout=25)
    _log(f"{phone['name']} {label} commit: {ctext[:160]}")
    if "Success" in ctext or _has_shopee(port, serial):
        return "installed"
    raise RuntimeError(f"install-commit 실패: {ctext[:200]}")


def _adb_install(phone: dict, *, vm=None, label: str = "install", attempts: int = 3) -> str:
    """Install Shopee by decoupling the slow push from the (instant) pm install.

    The VMOS ADB-over-SSH tunnel is often very slow (~60 KB/s), so streaming
    install-multiple reliably times out. Instead we push each split once with a
    generous, size-based timeout, verify the bytes landed, then install from the
    on-device files via a pm session. Reconnects and retries on tunnel drops.
    """
    port = str(phone["adb_port"])
    missing = [str(p) for p in APKS if not p.is_file()]
    if missing:
        raise RuntimeError("Shopee APK 없음: " + ", ".join(missing))
    last = ""
    for attempt in range(1, attempts + 1):
        serial = _ensure_online(phone, vm)
        _log(f"{phone['name']} Shopee {label} 시도 {attempt}/{attempts} ({serial})")
        try:
            return _session_install(phone, port, serial, label)
        except subprocess.TimeoutExpired:
            last = "adb timeout"
            _log(f"{phone['name']} {label} 명령 타임아웃 — 재연결 후 재시도")
        except RuntimeError as exc:
            last = str(exc)
            low = last.lower()
            transient = ("offline" in low or "abb_exec" in low or "closed" in low
                         or "protocol fault" in low or "device" in low
                         or "전송" in last or "timeout" in low)
            _log(f"{phone['name']} {label} 실패: {last[:200]}")
            if not transient:
                break  # e.g. INSTALL_FAILED_* — retry won't help
        subprocess.run([ADB, "-P", port, "disconnect", serial],
                       capture_output=True, text=True, timeout=15)
        time.sleep(2)
    raise RuntimeError(f"{phone['name']} {label} 실패: {last[:300]}")


def install_shopee(phone: dict, *, vm=None) -> str:
    port = str(phone["adb_port"])
    serial = str(phone.get("serial") or "")
    if serial and _has_shopee(port, serial):
        _log(f"{phone['name']} Shopee 이미 설치됨")
        return "installed"
    return _adb_install(phone, vm=vm, label="설치")


def reinstall_shopee(phone: dict, *, vm=None) -> str:
    """Force reinstall Shopee (region change recovery or broken app)."""
    return _adb_install(phone, vm=vm, label="재설치")


def start_session(phone: dict) -> None:
    env = os.environ.copy()
    env["ANDROID_ADB_SERVER_PORT"] = str(phone["adb_port"])
    env["PYTHONIOENCODING"] = "utf-8"
    env["SHOPEE_PAD_NAME"] = str(phone.get("name") or "")
    env["VMOS_PAD_CODE"] = str(phone.get("pad") or "")
    env.setdefault("SHOPEE_WEB_HOST", "shopee.com.br")
    serial = str(phone.get("serial") or "")
    log_path = RUNTIME / f"session_store_{serial.replace(':', '_')}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("a", encoding="utf-8")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [sys.executable, "-u", str(RUNTIME / "_store_sessions.py"), serial, str(phone.get("name") or "")],
        cwd=str(RUNTIME),
        env=env,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        close_fds=False,
    )
    _log(f"{phone['name']} 세션 보관 시작 {phone['serial']}")


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    phones = json.loads(PHONES_PATH.read_text(encoding="utf-8"))
    phones.sort(key=lambda p: int(p["shard"]))
    args = [a for a in sys.argv[1:] if a not in ("--resume", "--install-only", "--no-sessions", "--sessions-only")]
    if "--sessions-only" in sys.argv:
        _log("==== 세션 보관만 ====")
    elif "--install-only" not in sys.argv:
        _log(f"==== {len(phones)}대 Change Region BR ====")
        if "--resume" in sys.argv:
            task_ids = [int(a) for a in args]
        else:
            task_ids = change_region(phones)
        if task_ids:
            wait_tasks(task_ids)
        wait_online(phones)
    else:
        _log("==== 설치와 세션 보관만 ====")
    vm = _vmos()
    workers = max(1, len(phones))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(connect_phone, vm, phone) for phone in phones]
        for fut in as_completed(futures):
            fut.result()
    if "--sessions-only" not in sys.argv:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(install_shopee, phone, vm=vm) for phone in phones]
            for fut in as_completed(futures):
                fut.result()
    if "--no-sessions" not in sys.argv:
        for phone in phones:
            start_session(phone)
    vm.save_session({
        "padCode": phones[0]["pad"],
        "padName": phones[0]["name"],
        "androidVersion": "13",
        "country": "Brazil",
        "serial": phones[0]["serial"],
        "adb": f"adb connect {phones[0]['serial']}",
        "devices": [p["serial"] for p in phones],
        "phones": [
            {
                "padCode": p["pad"],
                "padName": p["name"],
                "androidVersion": "13",
                "country": "Brazil",
                "serial": p["serial"],
                "adb": f"adb connect {p['serial']}",
            }
            for p in phones
        ],
    })
    if "--sessions-only" in sys.argv:
        _log("==== 세션 보관 시작, 로그인 대기 ====")
    elif "--no-sessions" in sys.argv:
        _log("==== 지역 변경·설치 완료 ====")
    else:
        _log("==== 지역 변경·설치 완료, 세션 보관은 로그인 대기 ====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
