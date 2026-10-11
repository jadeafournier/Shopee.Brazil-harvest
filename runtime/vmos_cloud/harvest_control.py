# -*- coding: utf-8 -*-
"""Start or stop one phone's harvest. A server list never starts collection by itself."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = Path(r"C:\Python314\python.exe")
RUN_LIST = HERE / "_run_list.py"
ADB = Path(r"D:\Luxury\Great Scraper2\Great Scraper\emu_setup\platform-tools\adb.exe")
PHONES_PATH = HERE / "phones.json"

_LOCK = threading.Lock()


def _phones() -> list[dict]:
    return json.loads(PHONES_PATH.read_text(encoding="utf-8"))


def _phone(name: str) -> dict | None:
    for phone in _phones():
        if str(phone.get("name") or "") == name:
            return phone
    return None


def _commands() -> list[tuple[int, str]]:
    try:
        out = subprocess.check_output(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -like '*_run_list.py*' -or $_.CommandLine -like '*vmos_app_capture.py*' } | "
                "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }",
            ],
            text=True, timeout=12, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[tuple[int, str]] = []
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition("|")
        if pid.isdigit() and cmd:
            rows.append((int(pid), cmd))
    return rows


def _shard_running(shard: int) -> bool:
    needle = f"--shard {shard}"
    return any("_run_list.py" in cmd and needle in cmd for _, cmd in _commands())


def _blocked(name: str) -> str:
    try:
        import phone_activity
        for card in phone_activity.cards():
            if card.get("name") != name:
                continue
            if card.get("mode") == "session":
                return "Session collection is using this phone."
            if card.get("mode") == "region":
                return "Change region is using this phone."
    except Exception:
        return ""
    return ""


def _ensure_lists() -> str:
    import task_lists
    source = task_lists.selected_path()
    if source is None:
        return "The server list is not here yet."
    marker = HERE / "list_source.txt"
    marker.write_text(source.name, encoding="utf-8")
    import list_progress
    list_progress.apply_worklists(len(_phones()))
    return ""


def _serial(shard: int) -> str:
    path = HERE / f"list_{shard}.log"
    if not path.is_file():
        return ""
    raw = path.read_bytes()[-12000:]
    text = raw.decode("utf-8", errors="replace")
    found = ""
    for line in text.splitlines():
        if "localhost:" not in line:
            continue
        if "ADB 재사용" not in line and "기기 " not in line:
            continue
        for part in line.split():
            if part.startswith("localhost:"):
                found = part.strip(".,")
    return found


def _clear_proxy(phone: dict) -> None:
    serial = _serial(int(phone["shard"]))
    if not serial:
        return
    port = str(phone["adb_port"])
    mitm = str(phone["port"])
    env = os.environ.copy()
    env["ANDROID_ADB_SERVER_PORT"] = port
    steps = [
        ["-s", serial, "shell", "settings", "put", "global", "http_proxy", ":0"],
        ["-s", serial, "shell", "settings", "delete", "global", "global_http_proxy_host"],
        ["-s", serial, "shell", "settings", "delete", "global", "global_http_proxy_port"],
        ["-s", serial, "reverse", "--remove", f"tcp:{mitm}"],
    ]
    for args in steps:
        try:
            subprocess.run(
                [str(ADB), "-P", port, *args],
                capture_output=True, text=True, timeout=6, env=env,
            )
        except (OSError, subprocess.SubprocessError):
            break


def _kill_mitm(port: str) -> None:
    needle = f"--listen-port {port}"
    try:
        out = subprocess.check_output(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | "
                f"Where-Object {{ $_.Name -eq 'mitmdump.exe' -and $_.CommandLine -like '*{needle}*' }} | "
                "Select-Object -ExpandProperty ProcessId",
            ],
            text=True, timeout=12, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return
    for token in out.split():
        if token.isdigit():
            subprocess.run(["taskkill", "/F", "/PID", token], capture_output=True, text=True)


def _kill_phone(phone: dict) -> None:
    shard = int(phone["shard"])
    port = str(phone["port"])
    port_mark = f"--port {port}"
    shard_mark = f"--shard {shard}"
    for pid, cmd in _commands():
        harvest = "_run_list.py" in cmd and shard_mark in cmd
        capture = "vmos_app_capture.py" in cmd and port_mark in cmd
        if harvest or capture:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, text=True)
    _kill_mitm(port)
    _clear_proxy(phone)


def _archive_logged_in_session(phone: dict) -> None:
    """수집 시작 직전: 로그인된 계정이면 PC에 세션 보관(순환용, snap_after_login 과 동일)."""
    name = str(phone.get("name") or "")
    if not name or _blocked(name):
        return
    try:
        import phone_activity

        _region, session = phone_activity._parse(phone_activity._commands())
        if name in session:
            return
    except Exception:
        pass
    client = HERE.parents[1] / "client 4 api integration"
    if str(client) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(client))
    if str(HERE) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(HERE))
    from _run_list import connect_phone

    serial = (phone.get("serial") or "").strip() or _serial(int(phone["shard"]))
    try:
        serial = connect_phone(phone) or serial
    except Exception:
        if not serial:
            return
    env = os.environ.copy()
    env["ANDROID_ADB_SERVER_PORT"] = str(phone["adb_port"])
    env["SHOPEE_PAD_NAME"] = name
    env["VMOS_PAD_CODE"] = str(phone.get("pad") or "")
    env.setdefault("SHOPEE_WEB_HOST", "shopee.com.br")
    os.environ.update(env)
    from br_emulator.vmos_app_capture import (
        _account_userid_on_device,
        _session_parts_present,
        launch_shopee,
        snapshot_session,
    )

    if not _account_userid_on_device(serial):
        return
    parts = _session_parts_present(serial)
    if "shared_prefs" not in parts:
        return
    uid = snapshot_session(serial)
    if not uid:
        return
    username = ""
    meta = HERE.parents[1] / "Shopee.Brazil" / "Sessions" / f"{uid}.json"
    if meta.is_file():
        try:
            username = str(json.loads(meta.read_text(encoding="utf-8")).get("username") or "")
        except (OSError, json.JSONDecodeError):
            username = ""
    try:
        import session_ledger

        session_ledger.note_created(uid, name, serial, str(phone.get("pad") or ""), username=username)
    except Exception:
        pass
    launch_shopee(serial)


def start(name: str) -> dict:
    phone = _phone(name)
    if not phone:
        return {"ok": False, "error": "Unknown phone."}
    with _LOCK:
        if _shard_running(int(phone["shard"])):
            return {"ok": True, "running": True}
        reason = _blocked(name)
        if reason:
            return {"ok": False, "error": reason}
        try:
            _archive_logged_in_session(phone)
        except Exception:
            pass
        missing = _ensure_lists()
        if missing:
            return {"ok": False, "error": missing}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        subprocess.Popen(
            [str(PY), "-u", str(RUN_LIST), "--shard", str(int(phone["shard"]))],
            cwd=str(HERE),
            creationflags=flags,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            import list_cycle_manager

            list_cycle_manager.note_collection_started()
        except Exception:
            pass
    return {"ok": True, "running": True}


def stop(name: str) -> dict:
    phone = _phone(name)
    if not phone:
        return {"ok": False, "error": "Unknown phone."}
    with _LOCK:
        _kill_phone(phone)
        import list_progress
        import collect_stats

        shard = int(phone["shard"])
        list_progress.release_shard(shard)
        collect_stats.fresh([shard], {shard: HERE / f"list_{shard}.log"})
    return {"ok": True, "running": False}


def arrival() -> dict:
    import task_lists
    return task_lists.newest()
