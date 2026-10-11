# -*- coding: utf-8 -*-
"""Sessions 탭의 폰 카드 상태. 패드 코드는 밖으로 내보내지 않는다."""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent
ROOT = RUNTIME.parents[1]
PHONES = RUNTIME / "phones.json"
JOBS = RUNTIME / "phone_jobs.json"
PROGRESS = RUNTIME / "phone_progress.json"
SESSION_DIR = ROOT / "Shopee.Brazil" / "Sessions"
SESSION_LIMIT = 3
_PROGRESS_LOCK = threading.Lock()
STEP_LABEL = {
    "preparing": "Preparing",
    "resetting": "Resetting",
    "online": "Coming online",
    "connecting": "Connecting",
    "installing": "Installing",
    "done": "Installed",
}
PY = Path(r"C:\Python314\python.exe")
_CACHE = {"at": 0.0, "cmds": []}


def _phones() -> list[dict]:
    raw = json.loads(PHONES.read_text(encoding="utf-8"))
    rows = [row for row in raw if isinstance(row, dict) and row.get("name")]
    rows.sort(key=lambda row: int(row.get("shard") or 0))
    return rows


def _jobs() -> dict:
    if not JOBS.is_file():
        return {}
    try:
        data = json.loads(JOBS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_jobs(data: dict) -> None:
    JOBS.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _read_progress() -> dict:
    if not PROGRESS.is_file():
        return {}
    try:
        data = json.loads(PROGRESS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_progress(data: dict) -> None:
    PROGRESS.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def set_progress(names: list[str], step: str, progress: int) -> None:
    label = STEP_LABEL.get(step, step)
    value = max(0, min(100, int(progress)))
    with _PROGRESS_LOCK:
        data = _read_progress()
        for name in names:
            data[str(name)] = {"step": step, "label": label, "progress": value}
        _write_progress(data)


def clear_progress(names: list[str]) -> None:
    with _PROGRESS_LOCK:
        data = _read_progress()
        for name in names:
            data.pop(str(name), None)
        _write_progress(data)


def _commands() -> list[str]:
    now = time.time()
    if now - float(_CACHE["at"]) < 1.5:
        return list(_CACHE["cmds"])
    try:
        out = subprocess.check_output(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -like '*_store_sessions.py*' "
                "-or $_.CommandLine -like '*_phone_actions.py*' } | "
                "Select-Object -ExpandProperty CommandLine",
            ],
            text=True, timeout=12, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        out = ""
    cmds = [line.strip() for line in out.splitlines() if line.strip()]
    _CACHE["at"] = now
    _CACHE["cmds"] = cmds
    return cmds


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            text=True, timeout=8, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return str(pid) in out and "No tasks" not in out


def _tool_names(cmds: list[str], verb: str) -> set[str]:
    out: set[str] = set()
    needle = f" {verb} "
    for cmd in cmds:
        if "_phone_actions.py" not in cmd or needle not in cmd:
            continue
        tail = cmd.split(needle, 1)[1].replace('"', "")
        out.update(part for part in tail.split() if part)
    return out


def _parse(cmds: list[str]) -> tuple[set[str], dict[str, str]]:
    region: set[str] = set()
    session: dict[str, str] = {}
    for cmd in cmds:
        if "_phone_actions.py" in cmd and " region " in cmd:
            tail = cmd.split(" region ", 1)[1].replace('"', "")
            region.update(part for part in tail.split() if part)
        match = re.search(r"_store_sessions\.py\"?\s+(\S+)\s+(\S+)", cmd)
        if match:
            session[match.group(2).strip('"')] = match.group(1).strip('"')
    return region, session


def _session_count(serial: str) -> int:
    path = RUNTIME / f"session_store_{serial.replace(':', '_')}.log"
    if not serial or not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    mark = text.rfind("---- 세션 보관 시작 ----")
    chunk = text[mark:] if mark >= 0 else text
    return chunk.count("보관 완료")


def _collection_serials() -> dict[str, str]:
    """이번 수집의 시리얼. 그 뒤에 지역 변경이 끝나면 비운다."""
    names = [str(phone["name"]) for phone in _phones()]
    path = RUNTIME / "_run_br_three.log"
    try:
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {name: "" for name in names}
    if _CACHE.get("marks_key") == key and isinstance(_CACHE.get("marks"), dict):
        return dict(_CACHE["marks"])
    text = path.read_text(encoding="utf-8", errors="replace")
    latest = {name: "" for name in names}
    known = set(names)
    pending_all = False
    pending_some: list[str] = []
    for line in text.splitlines():
        if re.match(r"^==== \d+대 Change Region", line):
            pending_all = True
            continue
        if line.startswith("==== 선택 Change Region "):
            body = line[len("==== 선택 Change Region "):]
            if body.endswith(" ===="):
                body = body[:-5]
            pending_some = [part.strip() for part in body.split(",") if part.strip() in known]
            continue
        if line.startswith("==== 선택 지역 변경·설치 완료"):
            for name in pending_some:
                latest[name] = ""
            pending_some = []
            continue
        if line.startswith("==== 지역 변경·설치 완료"):
            if pending_all:
                for name in names:
                    latest[name] = ""
                pending_all = False
            continue
        if " 세션 보관 시작 " not in line:
            continue
        name, _, serial = line.partition(" 세션 보관 시작 ")
        name = name.strip()
        serial = serial.strip()
        if name in known and serial:
            latest[name] = serial
    _CACHE["marks_key"] = key
    _CACHE["marks"] = latest
    return dict(latest)


def _run_count(name: str, live_serial: str) -> int:
    serial = live_serial or _collection_serials().get(name, "")
    return _session_count(serial) if serial else 0


def _phone_map() -> dict[str, dict]:
    return {str(phone["name"]): phone for phone in _phones()}


def _session_stop_file(serial: str) -> Path:
    return SESSION_DIR / f"STOP_{serial.replace(':', '_')}"


def _request_session_stop(name: str, serial: str = "") -> None:
    if not serial:
        serial = str(_phone_map().get(name, {}).get("serial") or "")
    if not serial:
        return
    stop = _session_stop_file(serial)
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("stop\n", encoding="utf-8")


def _store_session_pids(name: str) -> list[int]:
    safe = name.replace("'", "''")
    try:
        out = subprocess.check_output(
            [
                "powershell", "-NoProfile", "-Command",
                f"$n='{safe}'; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -like '*_store_sessions.py*' "
                "-and $_.CommandLine -like ('*' + $n + '*') } | "
                "Select-Object -ExpandProperty ProcessId",
            ],
            text=True, timeout=12, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        part = line.strip()
        if part.isdigit():
            pids.append(int(part))
    return pids


def _kill_store_sessions(name: str) -> None:
    for pid in _store_session_pids(name):
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, text=True, timeout=15,
        )


def _stop_session_collect(names: list[str]) -> list[str]:
    """세션 보관 루프에 STOP 을 걸고 프로세스를 종료한다."""
    _region, session = _parse(_commands())
    phones = _phone_map()
    stopped: list[str] = []
    for label in names:
        name = str(label)
        phone = phones.get(name)
        if not phone:
            continue
        serial = session.get(name) or str(phone.get("serial") or "")
        _request_session_stop(name, serial)
        _kill_store_sessions(name)
        stopped.append(name)
    if stopped:
        time.sleep(0.8)
    return stopped


def _stop_harvest_for_region(names: list[str]) -> None:
    """지역변경 전: 자료 수집·세션 보관을 중지한다."""
    import harvest_control

    for label in names:
        try:
            harvest_control.stop(str(label))
        except Exception:
            pass
    _stop_session_collect(names)
    jobs = _jobs()
    changed = False
    for label in names:
        job = jobs.get(str(label))
        if isinstance(job, dict) and job.get("mode") == "session":
            jobs.pop(str(label), None)
            changed = True
    if changed:
        _save_jobs(jobs)


def stop_session(names: list[str]) -> dict:
    """UI 세션중지 — 보관 루프만 끝낸다."""
    known = set(_phone_map())
    _region, session = _parse(_commands())
    jobs = _jobs()
    stopped: list[str] = []
    skipped: list[str] = []
    for raw in names:
        label = str(raw)
        if label not in known:
            skipped.append(label)
            continue
        job = jobs.get(label) if isinstance(jobs.get(label), dict) else {}
        active = (
            label in session
            or job.get("mode") == "session"
            or bool(_store_session_pids(label))
        )
        if not active:
            skipped.append(label)
            continue
        _stop_session_collect([label])
        if isinstance(jobs.get(label), dict) and jobs[label].get("mode") == "session":
            jobs.pop(label, None)
        stopped.append(label)
    _save_jobs(jobs)
    _CACHE["at"] = 0.0
    return {"stopped": stopped, "skipped": skipped}


def stop_at_limit() -> list[str]:
    """이번 수집이 3개에 닿은 폰은 STOP 파일로 보관 루프를 끝낸다."""
    _region, session = _parse(_commands())
    stopped: list[str] = []
    for name, serial in session.items():
        if _session_count(serial) < SESSION_LIMIT:
            continue
        stop = SESSION_DIR / f"STOP_{serial.replace(':', '_')}"
        if not stop.is_file():
            stop.parent.mkdir(parents=True, exist_ok=True)
            stop.write_text("stop\n", encoding="utf-8")
        stopped.append(name)
    return stopped


def cards() -> list[dict]:
    cmds = _commands()
    region, session = _parse(cmds)
    adb_busy = _tool_names(cmds, "adb")
    reinstall_busy = _tool_names(cmds, "reinstall")
    jobs = _jobs()
    progress_rows = _read_progress()
    changed = False
    rows = []
    for phone in _phones():
        name = str(phone["name"])
        job = jobs.get(name) if isinstance(jobs.get(name), dict) else {}
        mode = str(job.get("mode") or "")
        pid = int(job.get("pid") or 0)
        if (
            mode
            and pid
            and not _alive(pid)
            and name not in session
            and name not in region
            and name not in adb_busy
            and name not in reinstall_busy
        ):
            jobs.pop(name, None)
            changed = True
            mode = ""
        if name in region or mode == "region":
            state = "region"
        elif name in reinstall_busy or mode == "reinstall":
            state = "reinstall"
        elif name in adb_busy or mode == "adb":
            state = "adb"
        elif name in session or mode == "session":
            state = "session"
        else:
            state = "idle"
        count = 0 if state in ("region", "adb", "reinstall") else _run_count(name, session.get(name, ""))
        prog = progress_rows.get(name) if isinstance(progress_rows.get(name), dict) else {}
        label = str(prog.get("label") or "")
        step = str(prog.get("step") or "")
        pct = int(prog.get("progress") or 0)
        if state == "reinstall":
            label = "Reinstalling Shopee"
            step = "reinstall"
            pct = max(pct, 45)
        elif state == "adb":
            label = "ADB reconnect"
            step = "adb"
            pct = max(pct, 35)
        elif state != "region":
            label = ""
            step = ""
            pct = 0
        elif pct <= 0 or step == "preparing":
            label = "Preparing"
            step = "preparing"
            pct = pct or 8
        rows.append({
            "name": name,
            "mode": state,
            "count": count if state == "session" or count >= SESSION_LIMIT else 0,
            "full": count >= SESSION_LIMIT,
            "locked": state == "region",
            "label": label,
            "step": step,
            "progress": pct,
        })
    if changed:
        _save_jobs(jobs)
    return rows


def start_tool(action: str, names: list[str]) -> dict:
    """Background ADB reconnect or Shopee reinstall for selected phones."""
    if action not in ("adb", "reinstall"):
        raise RuntimeError("unknown tool action")
    known = {str(phone["name"]) for phone in _phones()}
    region, _session = _parse(_commands())
    jobs = _jobs()
    started: list[str] = []
    skipped: list[str] = []
    for name in names:
        label = str(name)
        if label not in known:
            skipped.append(label)
            continue
        if label in region:
            skipped.append(label)
            continue
        job = jobs.get(label) if isinstance(jobs.get(label), dict) else {}
        if job.get("mode") in ("region", "session", "adb", "reinstall"):
            skipped.append(label)
            continue
        started.append(label)
    if not started:
        return {"started": [], "skipped": skipped}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [str(PY), "-u", str(RUNTIME / "_phone_actions.py"), action, *started],
        cwd=str(RUNTIME),
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    now = time.time()
    for label in started:
        jobs[label] = {"mode": action, "pid": proc.pid, "at": now}
    _save_jobs(jobs)
    _CACHE["at"] = 0.0
    return {"started": started, "skipped": skipped}


def start(action: str, names: list[str]) -> dict:
    if action not in ("region", "session"):
        raise RuntimeError("unknown action")
    known = {str(phone["name"]) for phone in _phones()}
    region, session = _parse(_commands())
    jobs = _jobs()
    started: list[str] = []
    skipped: list[str] = []
    for name in names:
        label = str(name)
        job = jobs.get(label) if isinstance(jobs.get(label), dict) else {}
        if label not in known:
            skipped.append(label)
            continue
        if action == "region":
            if label in region or job.get("mode") == "region":
                skipped.append(label)
                continue
            started.append(label)
            continue
        if (
            label in session
            or job.get("mode") == "session"
            or _run_count(label, session.get(label, "")) >= SESSION_LIMIT
        ):
            skipped.append(label)
            continue
        started.append(label)
    if not started:
        return {"started": [], "skipped": skipped}
    if action == "region":
        _stop_harvest_for_region(started)
    elif action == "session":
        _stop_harvest_for_region(started)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [str(PY), "-u", str(RUNTIME / "_phone_actions.py"), action, *started],
        cwd=str(RUNTIME),
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    now = time.time()
    for label in started:
        jobs[label] = {"mode": action, "pid": proc.pid, "at": now}
    _save_jobs(jobs)
    _CACHE["at"] = 0.0
    return {"started": started, "skipped": skipped}
