# -*- coding: utf-8 -*-
"""Cumulative per-phone save counts. Fresh resets the displayed totals."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATH = HERE / "collect_stats.json"
_LOCK = threading.Lock()
_OK = re.compile(r"✅\s*i\.(\d+)\.(\d+)\s+([\d.]+)s")
_MISS = re.compile(r"(?:…|\.\.\.)?\s*i\.(\d+)\.(\d+)\s+([\d.]+)s\s+미저장")


def _empty(pos: int) -> dict:
    return {"saved": 0, "miss": 0, "pos": pos, "last": "", "last_sec": None, "fresh_at": time.time()}


def _read() -> dict:
    if not PATH.is_file():
        return {"fresh_at": time.time(), "phones": {}}
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"fresh_at": time.time(), "phones": {}}
    if not isinstance(data, dict):
        return {"fresh_at": time.time(), "phones": {}}
    phones = data.get("phones")
    if not isinstance(phones, dict):
        data["phones"] = {}
    return data


def _write(data: dict) -> None:
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def observe(shard: int, log_path: Path) -> dict:
    """Add saves and misses appended since the last read."""
    with _LOCK:
        data = _read()
        phones = data["phones"]
        key = str(int(shard))
        row = phones.get(key)
        size = _size(log_path)
        if not isinstance(row, dict):
            row = _empty(size)
            phones[key] = row
        if not row.get("fresh_at"):
            row["fresh_at"] = float(data.get("fresh_at") or time.time())
        pos = int(row.get("pos") or 0)
        if pos > size:
            pos = size
        if log_path.is_file() and pos < size:
            with log_path.open("rb") as handle:
                handle.seek(pos)
                chunk = handle.read(size - pos)
            text = chunk.decode("utf-8", errors="replace")
            if "1차 수집 시작" in text:
                row["saved"] = 0
                row["miss"] = 0
                row["last"] = ""
                row["last_sec"] = None
                idx = text.rfind("1차 수집 시작")
                text = text[idx:]
            ok = list(_OK.finditer(text))
            missed = list(_MISS.finditer(text))
            row["saved"] = int(row.get("saved") or 0) + len(ok)
            row["miss"] = int(row.get("miss") or 0) + len(missed)
            row["pos"] = size
            last = None
            last_at = -1
            if ok:
                last = ok[-1]
                last_at = last.start()
                kind = "Saved"
            if missed and (last is None or missed[-1].start() > last_at):
                last = missed[-1]
                kind = "Missed"
            if last is not None:
                row["last"] = f"{kind} i.{last.group(1)}.{last.group(2)}"
                row["last_sec"] = float(last.group(3))
        _write(data)
        return {
            "saved": int(row.get("saved") or 0),
            "miss": int(row.get("miss") or 0),
            "last": str(row.get("last") or ""),
            "last_sec": row.get("last_sec"),
            "fresh_at": float(data.get("fresh_at") or 0),
        }


def _registry_sent_count() -> int:
    try:
        import sever_hub

        return sever_hub.registry_sent_count()
    except Exception:
        return 0


def upload_baseline() -> int:
    """Upload registry count at the start of the current collection round (Fresh reset)."""
    with _LOCK:
        data = _read()
        if "upload_sent_baseline" not in data:
            data["upload_sent_baseline"] = _registry_sent_count()
            _write(data)
        return int(data.get("upload_sent_baseline") or 0)


def fresh(shards: list[int], logs: dict[int, Path]) -> dict:
    """Zero every displayed counter and ignore the log lines already on disk."""
    with _LOCK:
        now = time.time()
        phones = {}
        for shard in shards:
            phones[str(int(shard))] = _empty(_size(logs.get(int(shard), Path())))
        data = {
            "fresh_at": now,
            "phones": phones,
            "upload_sent_baseline": _registry_sent_count(),
        }
        _write(data)
        return data
