# -*- coding: utf-8 -*-
"""m02 hub status for the Sever tab. The member key never leaves this process."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from collect_layout import DOWNLOAD_DIR, UPLOAD_DIR, load as load_layout

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "runtime" / "br_member_hub"
LIVE = Path(__file__).resolve().parent / "sever_live.json"
SENT_PATH = HUB / "m02_push_upload.json"
EVENTS_PATH = Path(__file__).resolve().parent / "upload_events.json"
ENV_PATH = HUB / "m02.env"
_ME: dict = {"at": 0.0, "body": None, "error": ""}


def _env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_PATH.is_file():
        return out
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _get(url: str, key: str, timeout: float = 8.0) -> object:
    req = urllib.request.Request(
        url,
        headers={"X-Member-Key": key, "Accept": "application/json", "User-Agent": "GreatScraper/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _me(host: str, port: str, key: str) -> tuple[dict, str]:
    now = time.time()
    if now - float(_ME["at"]) < 4 and (_ME["body"] is not None or _ME["error"]):
        return _ME["body"] or {}, str(_ME["error"] or "")
    body: dict = {}
    error = ""
    try:
        raw = _get(f"http://{host}:{port}/v1/me", key)
        body = raw if isinstance(raw, dict) else {}
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:180]
    _ME["at"] = now
    _ME["body"] = body
    _ME["error"] = error
    return body, error


def _live() -> dict:
    if not LIVE.is_file():
        return {}
    try:
        data = json.loads(LIVE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def registry_sent_count() -> int:
    return _upload_registry_count()


def _upload_registry_count() -> int:
    if not SENT_PATH.is_file():
        return 0
    try:
        data = json.loads(SENT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    sent = data.get("sent") if isinstance(data, dict) else None
    if isinstance(sent, dict):
        return len(sent)
    if isinstance(data, dict):
        return int(data.get("sent_count") or 0)
    return 0


def _upload_recent_sig() -> str:
    if EVENTS_PATH.is_file():
        try:
            events = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            events = []
        if isinstance(events, list) and events and isinstance(events[0], dict):
            r0 = events[0]
            if r0.get("file"):
                return f"{r0.get('at') or ''}|{r0.get('file')}"
    live = _live()
    recent = live.get("recent") if isinstance(live.get("recent"), list) else []
    if recent and isinstance(recent[0], dict):
        r0 = recent[0]
        return f"{r0.get('at') or ''}|{r0.get('file') or ''}"
    return ""


def pulse() -> dict:
    """Lightweight upload pulse for the Collection workload UI (no hub HTTP)."""
    live = _live()
    total = _upload_registry_count()
    baseline = 0
    try:
        import collect_stats

        baseline = collect_stats.upload_baseline()
    except Exception:
        baseline = 0
    session_sent = max(0, total - baseline)
    upload_q: dict = {}
    try:
        import upload_manager

        upload_manager.ensure_started()
        upload_q = upload_manager.stats()
    except Exception:
        upload_q = {}
    return {
        "sent": total,
        "session_sent": session_sent,
        "bridge": bool(live.get("running")),
        "recent_sig": _upload_recent_sig(),
        "upload_pending": int(live.get("pending") or 0),
        "upload_queue": int(upload_q.get("queue_depth") or 0),
    }


def view() -> dict:
    env = _env()
    host = env.get("BR_MEMBER_HOST") or "95.217.87.90"
    port = env.get("BR_MEMBER_PORT") or "8745"
    member = env.get("BR_MEMBER_ID") or "m02"
    key = env.get("BR_MEMBER_KEY") or ""
    remote: dict = {}
    error = ""
    if key:
        remote, error = _me(host, port, key)
    else:
        error = "m02.env 에 키가 없습니다"
    dispatch = remote.get("dispatch") if isinstance(remote.get("dispatch"), dict) else {}
    members = dispatch.get("members") if isinstance(dispatch.get("members"), dict) else {}
    live = _live()
    layout = load_layout()
    list_workload: dict = {}
    try:
        import list_progress

        list_workload = list_progress.workload_view()
    except Exception:
        list_workload = {}
    return {
        "member": remote.get("id") or member,
        "name": remote.get("name") or "Member 2",
        "host": host,
        "port": int(port) if str(port).isdigit() else port,
        "key_stored": bool(key),
        "ok": bool(remote.get("ok")) and not error,
        "error": error,
        "packets": remote.get("packets"),
        "inbox": remote.get("inbox"),
        "dispatch_open": dispatch.get("open"),
        "dispatch_uploaded": dispatch.get("uploaded"),
        "assigned": members.get(member),
        "folder": str(UPLOAD_DIR),
        "download": str(DOWNLOAD_DIR),
        "mode": layout.get("mode") or "auto",
        "list_mode": layout.get("list_mode") or "mode2",
        "tasks": int(live.get("tasks") or 0),
        "tasks_at": live.get("tasks_at") or "",
        "samples": live.get("samples") or [],
        "local_products": int(live.get("local_products") or 0),
        "sent": _upload_registry_count(),
        "pending": int(live.get("pending") or 0),
        "upload_fail": int(live.get("upload_fail") or 0),
        "recent": live.get("recent") or [],
        "bridge": bool(live.get("running")),
        "bridge_at": live.get("updated_at") or "",
        "harvest": live.get("harvest") or "waiting",
        "note": live.get("note") or "",
    }
