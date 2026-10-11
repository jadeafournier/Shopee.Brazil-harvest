# -*- coding: utf-8 -*-
"""The live server list is one file: Download/tasks.json.

Each new server payload overwrites that file. Harvest reads it directly.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from collect_layout import DOWNLOAD_DIR

NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(?:-\d+)?_tasks\.json$")
INDEX_PATH = DOWNLOAD_DIR / "list_index.json"
SELECT_PATH = DOWNLOAD_DIR / "selected_list.json"
SEEN_PATH = DOWNLOAD_DIR / "seen_lists.json"
LEGACY_JSON = DOWNLOAD_DIR / "tasks.json"
LEGACY_TXT = DOWNLOAD_DIR / "tasks.txt"
LIST_NAME = "tasks.json"
LIST_PATH = DOWNLOAD_DIR / LIST_NAME


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_stamp(received_at: str) -> str:
    text = (received_at or _now()).replace("+00:00", "Z")
    if "." in text:
        text = text.split(".", 1)[0] + ("Z" if text.endswith("Z") or "+00:00" in received_at else "")
        if not text.endswith("Z"):
            text += "Z"
    return text.replace(":", "-")


def _task_pairs(tasks: list[dict]) -> set[str]:
    pairs: set[str] = set()
    for row in tasks:
        if not isinstance(row, dict):
            continue
        shop = row.get("shop_id")
        item = row.get("item_id")
        if shop is None or item is None or shop == "" or item == "":
            continue
        pairs.add(f"{shop}/{item}")
    return pairs


_PAIR_CACHE: dict[str, tuple[int, set[str]]] = {}


def stored_pairs(path: Path) -> set[str]:
    """Shop/item pairs already stored in one list file."""
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return set()
    cached = _PAIR_CACHE.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    tasks = data.get("tasks") if isinstance(data, dict) else None
    pairs = _task_pairs(tasks if isinstance(tasks, list) else [])
    _PAIR_CACHE[str(path)] = (stamp, pairs)
    return pairs


def _read_index() -> dict:
    if not INDEX_PATH.is_file():
        return {"files": []}
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"files": []}
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        return {"files": []}
    return data


def _write_index(data: dict) -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _header(path: Path) -> dict:
    raw = path.read_bytes()[:320].decode("utf-8", errors="replace")
    at = re.search(r'"updated_at"\s*:\s*"([^"]+)"', raw)
    count = re.search(r'"count"\s*:\s*(\d+)', raw)
    return {
        "name": path.name,
        "count": int(count.group(1)) if count else 0,
        "arrived_at": at.group(1) if at else "",
    }


def _selected_name() -> str:
    if not SELECT_PATH.is_file():
        return ""
    try:
        data = json.loads(SELECT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    name = str((data or {}).get("file") or "")
    return name if NAME_RE.fullmatch(name) else ""


def _remember(name: str) -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SELECT_PATH.write_text(json.dumps({"file": name}, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_seen() -> set[str] | None:
    if not SEEN_PATH.is_file():
        return None
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        return set()
    return {str(name) for name in files if NAME_RE.fullmatch(str(name))}


def _write_seen(names: set[str]) -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(
        json.dumps({"files": sorted(names)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _seen_names(current: list[str]) -> set[str]:
    seen = _read_seen()
    if seen is None:
        seen = set(current)
        _write_seen(seen)
    return seen


def _ack(name: str) -> None:
    seen = _read_seen() or set()
    seen.add(name)
    _write_seen(seen)


def ensure_legacy() -> None:
    """The live server list stays in tasks.json. Do not rename it."""
    if LEGACY_TXT.is_file():
        LEGACY_TXT.unlink()


def sync_index() -> dict:
    ensure_legacy()
    index = _read_index()
    known = {str(item.get("name") or "") for item in index["files"]}
    changed = False
    for path in sorted(DOWNLOAD_DIR.glob("*_tasks.json")):
        if not NAME_RE.fullmatch(path.name) or path.name in known:
            continue
        info = _header(path)
        index["files"].append(info)
        known.add(path.name)
        changed = True
    files = [item for item in index["files"] if NAME_RE.fullmatch(str(item.get("name") or "")) and (DOWNLOAD_DIR / str(item["name"])).is_file()]
    files.sort(key=lambda item: str(item.get("arrived_at") or item.get("name") or ""))
    if len(files) != len(index["files"]) or changed:
        index["files"] = files
        _write_index(index)
    else:
        index["files"] = files
    chosen = _selected_name()
    names = [str(item.get("name") or "") for item in files]
    if files and chosen not in names:
        _remember(names[-1])
    return index


def catalog() -> list[dict]:
    index = sync_index()
    chosen = _selected_name()
    names = [str(item.get("name") or "") for item in index["files"]]
    seen = _seen_names(names)
    out = []
    for item in index["files"]:
        name = str(item.get("name") or "")
        out.append({
            "name": name,
            "count": int(item.get("count") or 0),
            "arrived_at": item.get("arrived_at") or "",
            "selected": name == chosen,
            "fresh": name not in seen,
        })
    return out


def select(name: str) -> dict:
    sync_index()
    if not NAME_RE.fullmatch(name) or not (DOWNLOAD_DIR / name).is_file():
        return {"ok": False, "error": "Unknown list."}
    _remember(name)
    _ack(name)
    return {"ok": True, "file": name}


def selected_path() -> Path | None:
    return LIST_PATH if LIST_PATH.is_file() else None


def live_count() -> int:
    """How many items are in the one server list file."""
    if not LIST_PATH.is_file():
        return 0
    return int(_header(LIST_PATH)["count"] or 0)


def newest() -> dict:
    rows = catalog()
    if not rows:
        return {"count": 0, "arrived_at": ""}
    return {"count": rows[-1]["count"], "arrived_at": rows[-1]["arrived_at"]}


def save_incoming(tasks: list[dict], received_at: str) -> Path | None:
    """Keep the server list in one file and overwrite it when the items change."""
    if not tasks:
        return None
    incoming = _task_pairs(tasks)
    if not incoming:
        return None
    old_keys: set[str] = set()
    if LIST_PATH.is_file():
        old_keys = stored_pairs(LIST_PATH)
        if old_keys == incoming:
            return None
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": received_at,
        "count": len(tasks),
        "tasks": tasks,
    }
    LIST_PATH.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    _PAIR_CACHE.pop(str(LIST_PATH), None)
    _remember(LIST_NAME)
    cycle_info: dict = {}
    try:
        import list_cycle_manager
        import list_progress

        ids = list_progress._item_ids(LIST_PATH)
        cycle_info = list_cycle_manager.on_server_list_changed(
            old_keys, incoming, received_at, ids
        )
    except Exception:
        ids = []
        cycle_info = {}
    try:
        import collect_layout

        if collect_layout.list_mode() == "mode2":
            import list_pool_manager
            import list_progress

            if not ids:
                ids = list_progress._item_ids(LIST_PATH)
            if not cycle_info.get("list_replaced"):
                list_pool_manager.reconcile_with_server(ids)
            return LIST_PATH
    except Exception:
        pass
    try:
        import list_progress
        list_progress.sync_claims()
        import phone_fleet

        nphones = phone_fleet.fleet_slot_count()
        if list_progress.pool_enabled() or list_progress.phone_only_shard(nphones) is None:
            list_progress.on_server_tasks_updated(nphones)
        else:
            list_progress.assign_all_pending_to(list_progress.phone_only_shard(nphones), nphones)
    except Exception:
        pass
    return LIST_PATH
