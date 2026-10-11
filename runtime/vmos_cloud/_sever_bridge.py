# -*- coding: utf-8 -*-
"""Poll the m02 task list and upload new product JSON as it is saved."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HUB = ROOT / "runtime" / "br_member_hub"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from collect_layout import DOWNLOAD_DIR, UPLOAD_DIR, clear_upload_once, load as load_layout

OUT = UPLOAD_DIR
LIVE = HERE / "sever_live.json"
SENT_PATH = HUB / "m02_push_upload.json"
EVENTS_PATH = HERE / "upload_events.json"
ENV_PATH = HUB / "m02.env"
PRODUCT = re.compile(r"^\d+_\d+\.json$")
# Brief in-process lock only while rewriting the sent registry (no .lock file).
_SENT_REGISTRY = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _env() -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _get(url: str, key: str) -> object:
    req = urllib.request.Request(
        url,
        headers={"X-Member-Key": key, "Accept": "application/json", "User-Agent": "GreatScraper/1.0"},
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _post(url: str, key: str, data: bytes) -> object:
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "X-Member-Key": key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "GreatScraper/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"ok": True, "body": body[:180]}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code} {raw[:180]}") from exc


def _load_sent() -> dict:
    if not SENT_PATH.is_file():
        return {}
    try:
        data = json.loads(SENT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sent = data.get("sent") if isinstance(data, dict) else None
    return sent if isinstance(sent, dict) else {}


def _save_sent(sent: dict) -> None:
    SENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": _now(), "sent_count": len(sent), "sent": sent}
    tmp = SENT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(SENT_PATH)


def _write_live(payload: dict) -> None:
    tmp = LIVE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(LIVE)


def _products() -> list[Path]:
    if not OUT.is_dir():
        return []
    found: list[Path] = []
    roots = [OUT]
    roots.extend(path for path in OUT.iterdir() if path.is_dir())
    for folder in roots:
        found.extend(
            path for path in folder.glob("*.json")
            if path.is_file() and PRODUCT.match(path.name)
        )
    return sorted(found)


def _load_events() -> list[dict]:
    if not EVENTS_PATH.is_file():
        return []
    try:
        data = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _note_event(name: str, result: str) -> None:
    events = _load_events()
    events.append({"at": _now(), "file": name, "result": result})
    EVENTS_PATH.write_text(json.dumps(events[-12:], ensure_ascii=False) + "\n", encoding="utf-8")


def deliver(path: Path) -> str:
    """Send one finished product file. The same file is not sent twice."""
    if not PRODUCT.match(path.name) or not path.is_file():
        return "skip"
    try:
        if path.stat().st_size < 20:
            return "skip"
    except OSError:
        return "skip"
    env = _env()
    host = env.get("BR_MEMBER_HOST") or ""
    port = env.get("BR_MEMBER_PORT") or ""
    key = env.get("BR_MEMBER_KEY") or ""
    if not host or not port or not key:
        return "no key"
    if path.name in _load_sent():
        return "sent"
    body = path.read_bytes()
    result = _post(f"http://{host}:{port}/v1/packet", key, body)
    with _SENT_REGISTRY:
        sent = _load_sent()
        if path.name in sent:
            return "sent"
        sent[path.name] = {"at": _now()}
        _save_sent(sent)
    label = "saved" if isinstance(result, dict) and result.get("saved") else "ok"
    _note_event(path.name, label)
    print(f"[{_now()}] OK {path.name}", flush=True)
    return label


def push_saved(path: Path) -> str:
    """Auto mode: hand off to the upload manager (immediate try + queued retries)."""
    import upload_manager

    return upload_manager.enqueue(path)


def _ready(path: Path) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size < 20:
        return False
    if time.time() - stat.st_mtime < 1.5:
        return False
    return True


def _session_busy() -> bool:
    try:
        out = subprocess.check_output(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -like '*_store_sessions.py*' } | "
                "Select-Object -ExpandProperty ProcessId",
            ],
            text=True, timeout=12, encoding="utf-8", errors="replace",
        )
    except Exception:
        return False
    return any(line.strip().isdigit() for line in out.splitlines())


def _harvest_running() -> bool:
    try:
        out = subprocess.check_output(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -like '*_run_list.py*' } | "
                "Select-Object -ExpandProperty ProcessId",
            ],
            text=True, timeout=12, encoding="utf-8", errors="replace",
        )
    except Exception:
        return False
    return any(line.strip().isdigit() for line in out.splitlines())


def main() -> int:
    import upload_manager

    upload_manager.ensure_started()
    env = _env()
    host = env["BR_MEMBER_HOST"]
    port = env["BR_MEMBER_PORT"]
    key = env["BR_MEMBER_KEY"]
    baseline = {p.name for p in _products()}
    fail = 0
    harvest = "waiting"
    note = "Waiting for the server list."
    launched = False
    print(f"[{_now()}] bridge folder={OUT} existing={len(baseline)}", flush=True)
    while True:
        tasks: list[dict] = []
        task_error = ""
        try:
            data = _get(f"http://{host}:{port}/v1/tasks?limit=5000", key)
            raw = data.get("tasks") if isinstance(data, dict) else None
            if isinstance(raw, list):
                tasks = [row for row in raw if isinstance(row, dict)]
        except Exception as exc:  # noqa: BLE001
            task_error = str(exc)[:180]
        samples = []
        for row in tasks:
            shop = row.get("shop_id")
            item = row.get("item_id")
            url = str(row.get("url") or "")
            if not url and shop and item:
                row["url"] = f"https://shopee.com.br/product/{shop}/{item}"
            if len(samples) < 8 and shop and item:
                samples.append({
                    "shop_id": shop,
                    "item_id": item,
                    "expire": row.get("expireDate") or "",
                })
        import task_lists
        task_lists.save_incoming(tasks, _now())
        layout = load_layout()
        mode = layout.get("mode") or "auto"
        once = bool(layout.get("upload_once"))
        sent = _load_sent()
        products = _products()
        if mode == "auto" or once:
            upload_manager.scan_unsent(limit=12)
        sent = _load_sent()
        recent = _load_events()
        if once:
            clear_upload_once()
        if tasks and not launched:
            if _harvest_running():
                harvest = "running"
                note = f"List has {len(tasks)} items. Harvest is already running."
            elif _session_busy():
                harvest = "blocked"
                note = f"List has {len(tasks)} items. Session collection is using the phones."
            else:
                harvest = "ready"
                note = f"List has {len(tasks)} items. Collection waits for a phone's Start collection button."
            launched = True
        elif not tasks:
            harvest = "running" if _harvest_running() else "waiting"
            if task_error:
                note = task_error
            elif mode == "manual":
                note = "Waiting for the server list. Upload is manual."
            else:
                note = "Waiting for the server list. New product files upload as they are saved."
        elif launched:
            harvest = "running" if _harvest_running() else harvest
        waiting = sum(1 for p in products if p.name not in sent)
        um = upload_manager.stats()
        fail = int(um.get("fail") or fail)
        _write_live({
            "running": True,
            "updated_at": _now(),
            "folder": str(OUT),
            "download": str(DOWNLOAD_DIR),
            "mode": mode,
            "tasks": len(tasks),
            "tasks_at": _now(),
            "samples": samples,
            "local_products": len(products),
            "sent": len(sent),
            "pending": waiting,
            "upload_fail": fail,
            "recent": list(reversed(recent)),
            "harvest": harvest,
            "note": note,
        })
        time.sleep(4)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
