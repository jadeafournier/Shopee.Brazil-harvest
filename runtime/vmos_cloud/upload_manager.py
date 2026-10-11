# -*- coding: utf-8 -*-
"""Dedicated upload queue: enqueue on every saved product, worker sends to member hub."""
from __future__ import annotations

import queue
import re
import threading
import time
from pathlib import Path

from collect_layout import UPLOAD_DIR, load as load_layout

HERE = Path(__file__).resolve().parent
PRODUCT = re.compile(r"^\d+_\d+\.json$")
# Parallel HTTP uploads (registry writes stay short; no m02_push_upload.lock file).
WORKERS = 4

_QUEUE: queue.Queue[Path] = queue.Queue()
_OFFERED: set[str] = set()
_OFFER_LOCK = threading.Lock()
_START_LOCK = threading.Lock()
_STARTED = False
_STATS = {"queued": 0, "sent": 0, "fail": 0, "last_at": "", "last_file": ""}
_STATS_LOCK = threading.Lock()


def _products() -> list[Path]:
    if not UPLOAD_DIR.is_dir():
        return []
    found: list[Path] = []
    roots = [UPLOAD_DIR, *(p for p in UPLOAD_DIR.iterdir() if p.is_dir())]
    for folder in roots:
        found.extend(
            p for p in folder.glob("*.json")
            if p.is_file() and PRODUCT.match(p.name)
        )
    return found


def _ready(path: Path) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size < 20:
        return False
    if time.time() - stat.st_mtime < 0.8:
        return False
    return True


def _note(result: str, name: str = "") -> None:
    with _STATS_LOCK:
        if result in ("saved", "ok", "sent"):
            _STATS["sent"] += 1
        elif result not in ("skip", "queued", "manual", "no key"):
            _STATS["fail"] += 1
        _STATS["last_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if name:
            _STATS["last_file"] = name


def stats() -> dict:
    with _STATS_LOCK:
        snap = dict(_STATS)
    snap["queue_depth"] = _QUEUE.qsize()
    with _OFFER_LOCK:
        snap["offered"] = len(_OFFERED)
    return snap


def _offer(path: Path) -> bool:
    if not path.is_file() or not PRODUCT.match(path.name):
        return False
    key = path.name
    with _OFFER_LOCK:
        if key in _OFFERED:
            return False
        _OFFERED.add(key)
    _QUEUE.put(path)
    with _STATS_LOCK:
        _STATS["queued"] += 1
    return True


def enqueue(path: Path, *, try_now: bool = True) -> str:
    """Queue a product JSON for upload (called as soon as harvest saves it)."""
    ensure_started()
    if (load_layout().get("mode") or "auto") != "auto":
        return "manual"
    if not path.is_file() or not PRODUCT.match(path.name):
        return "skip"
    if try_now:
        try:
            from _sever_bridge import deliver

            result = deliver(path)
            _note(result, path.name)
            if result in ("saved", "ok", "sent", "skip"):
                return result
        except Exception as exc:  # noqa: BLE001
            _note(str(exc)[:80], path.name)
    if _offer(path):
        return "queued"
    return "queued"


def scan_unsent(*, limit: int = 12) -> int:
    """Find local product files not yet in the sent registry and enqueue them."""
    ensure_started()
    if (load_layout().get("mode") or "auto") != "auto":
        return 0
    try:
        from _sever_bridge import _load_sent
    except Exception:
        return 0
    sent = _load_sent()
    added = 0
    for path in _products():
        if path.name in sent:
            continue
        if not _ready(path):
            continue
        if _offer(path):
            added += 1
        if added >= max(1, limit):
            break
    return added


def _worker() -> None:
    from _sever_bridge import deliver

    while True:
        path = _QUEUE.get()
        try:
            if (load_layout().get("mode") or "auto") != "auto":
                continue
            delay = 0.5
            for attempt in range(6):
                try:
                    result = deliver(path)
                    _note(result, path.name)
                    if result in ("saved", "ok", "sent", "skip"):
                        break
                    if result == "no key":
                        time.sleep(8)
                        continue
                except Exception as exc:  # noqa: BLE001
                    _note(str(exc)[:80], path.name)
                time.sleep(min(30.0, delay))
                delay *= 1.8
        finally:
            with _OFFER_LOCK:
                _OFFERED.discard(path.name)
            _QUEUE.task_done()


def _sweeper() -> None:
    while True:
        time.sleep(6)
        try:
            scan_unsent(limit=16)
        except Exception:
            pass


def ensure_started() -> None:
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True
        for i in range(WORKERS):
            threading.Thread(
                target=_worker,
                name=f"upload-manager-{i}",
                daemon=True,
            ).start()
        threading.Thread(target=_sweeper, name="upload-sweep", daemon=True).start()
