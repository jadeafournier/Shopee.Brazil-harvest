# -*- coding: utf-8 -*-
"""Phones take the next free item from the live server list.

The server list is one file. A later payload overwrites it. Each phone claims
the next item that is still in that file, not saved, and not held by another phone.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from collect_layout import UPLOAD_DIR
import task_lists

HERE = Path(__file__).resolve().parent
NAME = re.compile(r"^(?:output_)?(\d+)_(\d+)\.json$")
CLAIMS_PATH = HERE / "list_claims.json"
LOCK_PATH = HERE / "list_claims.lock"
PHONE_ONLY_PATH = HERE / "list_phone_only.json"
UNASSIGNED_PATH = HERE / "list_urls_unassigned.txt"
MISSED_PATH = HERE / "list_urls_missed.txt"
WATCH_PATH = HERE / "list_watch_state.json"
POOL_PATH = HERE / "list_pool.json"
SHARD_UNASSIGNED = -1
_QUEUE = re.compile(r"/product/(\d+)/(\d+)\s*$")
_IDS: dict = {"key": None, "rows": []}
_SAVED: dict = {"at": 0.0, "pairs": set()}
_MISSED: dict = {"at": 0.0, "pairs": set()}


def _item_ids(path: Path) -> list[tuple[int, int]]:
    key = (str(path), path.stat().st_mtime_ns)
    if _IDS["key"] == key:
        return _IDS["rows"]
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks") if isinstance(data, dict) else None
    seen: set[tuple[int, int]] = set()
    rows: list[tuple[int, int]] = []
    if isinstance(tasks, list):
        for row in tasks:
            if not isinstance(row, dict):
                continue
            try:
                pair = (int(row.get("shop_id")), int(row.get("item_id")))
            except (TypeError, ValueError):
                continue
            if pair in seen:
                continue
            seen.add(pair)
            rows.append(pair)
    _IDS["key"] = key
    _IDS["rows"] = rows
    return rows


def _saved_pairs() -> set[tuple[int, int]]:
    now = time.time()
    if now - float(_SAVED["at"]) < 6.0:
        return _SAVED["pairs"]
    pairs: set[tuple[int, int]] = set()
    folders: list[Path] = []
    if UPLOAD_DIR.is_dir():
        folders.append(UPLOAD_DIR)
        folders.extend(path for path in UPLOAD_DIR.iterdir() if path.is_dir())
    client = HERE.parents[1] / "client 4 api integration"
    for name in (
        "br_deliverable_getpc",
        "br_deliverable_20260927",
        "br_deliverable_row127",
        "br_deliverable_row128",
        "br_deliverable_rows25_27",
        "br_deliverable_inbox",
        "br_deliverable_received",
    ):
        folder = client / name
        if folder.is_dir():
            folders.append(folder)
    received = client / "br_deliverable_received"
    if received.is_dir():
        folders.extend(path for path in received.iterdir() if path.is_dir())
    seen_dirs: set[Path] = set()
    for folder in folders:
        try:
            key = folder.resolve()
        except OSError:
            continue
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        try:
            names = list(folder.glob("*.json"))
        except OSError:
            continue
        for path in names:
            match = NAME.match(path.name)
            if match:
                pairs.add((int(match.group(1)), int(match.group(2))))
    _SAVED["at"] = now
    _SAVED["pairs"] = pairs
    return pairs


def _invalidate_missed_cache() -> None:
    _MISSED["at"] = 0.0


def _missed_pairs() -> set[tuple[int, int]]:
    now = time.time()
    if now - float(_MISSED["at"]) < 2.0:
        return _MISSED["pairs"]
    pairs: set[tuple[int, int]] = set()
    for line in _read_queue_lines(MISSED_PATH):
        pair = _pair_from_line(line)
        if pair is not None:
            pairs.add(pair)
    _MISSED["at"] = now
    _MISSED["pairs"] = pairs
    return pairs


def _fleet_count() -> int:
    try:
        import phone_fleet

        return int(phone_fleet.fleet_slot_count())
    except Exception:
        return 10


def _registered_shards() -> list[int]:
    """Shards that currently have a registered phone (gap-safe, any fleet size)."""
    try:
        import phone_fleet

        reg = [int(s) for s in phone_fleet.registered_shards()]
        if reg:
            return sorted(reg)
    except Exception:
        pass
    return list(range(_fleet_count()))


def _count(phone_count: int | None) -> int:
    """Resolve a caller-supplied phone count; <=0/None means 'scan the whole fleet'.

    Scanning the full fleet of queue files is always safe (empty slots yield
    nothing), so this never under-counts when a phone is added. Distribution of
    unclaimed items is handled separately by _registered_shards() so items never
    land on an empty slot regardless of the number passed here.
    """
    if phone_count is None or int(phone_count) <= 0:
        return _fleet_count()
    return max(int(phone_count), _fleet_count())


def _collected_pairs() -> set[tuple[int, int]]:
    saved = _saved_pairs()
    if phone_only_shard(_fleet_count()) is not None:
        return saved
    return saved | _missed_pairs()


def missed_pairs() -> set[tuple[int, int]]:
    return _missed_pairs()


def pending_pairs_from_ids(ids: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Server list rows still to collect (not saved and not recorded as missed)."""
    collected = _collected_pairs()
    skip: set[tuple[int, int]] = set()
    try:
        import list_cycle_manager

        skip = list_cycle_manager.pairs_excluded_from_primary(ids)
    except Exception:
        skip = set()
    return [pair for pair in ids if pair not in collected and pair not in skip]


def clear_missed(phone_count: int = 0) -> dict:
    """Remove missed (unsaved) records only; does not refill Unassigned."""
    phone_count = _count(phone_count)
    with _file_lock():
        cleared = len(_read_queue_lines(MISSED_PATH))
        _write_queue_lines(MISSED_PATH, [])
        _invalidate_missed_cache()
    return {"ok": True, "cleared": cleared, "balance": queue_balance(phone_count)}


def recollect_missed(phone_count: int = 0) -> dict:
    """Clear missed lines and push those items back into the Unassigned pool (pending)."""
    phone_count = _count(phone_count)
    with _file_lock():
        cleared = len(_read_queue_lines(MISSED_PATH))
        if cleared:
            _write_queue_lines(MISSED_PATH, [])
            _invalidate_missed_cache()
    balance = on_server_tasks_updated(phone_count)
    return {"ok": True, "cleared": cleared, "balance": balance}


def reset_collection_state(phone_count: int = 0) -> dict:
    """Clear queues, claims, missed, and watch state; refill Unassigned from the live list."""
    phone_count = _count(phone_count)
    set_phone_only(None, phone_count)
    with _file_lock():
        _write_queue_lines(MISSED_PATH, [])
        _invalidate_missed_cache()
        _write_queue_lines(UNASSIGNED_PATH, [])
        for shard in range(phone_count):
            _write_queue_lines(_queue_path(shard), [])
            retry = HERE / f"list_retry_{int(shard)}.txt"
            try:
                retry.write_text("", encoding="utf-8")
            except OSError:
                pass
        _write_claims({})
        _write_watch({})
    withdraw_unregistered_phone_queues()
    balance = on_server_tasks_updated(phone_count)
    _SAVED["at"] = 0.0
    _IDS["key"] = None
    return {"ok": True, "balance": balance}


def record_outcome(pair: tuple[int, int], outcome: str, phone_count: int = 0) -> bool:
    """엔진 한 건 결과를 목록 사이클에 반영한다.

    outcome: ``saved`` | ``missed`` | ``gone``

    * mode2 + 사이클 매니저: 저장이면 임시 집합에서 제거, 미저장/없음이면 임시
      파일에 적재하고 lease 를 비운다(풀로 복귀시키지 않는다 — 재수집은
      wind-down 단계에서 일괄 수행).
    * 그 외(mode1 등): 저장이면 큐에서 제거, 미저장/없음이면 기존 missed 기록.
    """
    pair = (int(pair[0]), int(pair[1]))
    try:
        import collect_layout

        if collect_layout.list_mode() == "mode2":
            import list_cycle_manager
            import list_pool_manager

            list_cycle_manager.record_capture_result(pair, outcome)
            # 저장·미저장·없음 모두 이 폰의 lease 는 비운다. 재수집은 wind-down
            # 단계에서 임시 파일 → 풀로 일괄 주입되므로 여기서 풀 복귀는 하지 않는다.
            list_pool_manager.clear_pair(pair)
            return True
    except Exception:
        pass
    if outcome == "saved":
        return remove_pair_from_queues(pair, phone_count)
    return mark_pair_missed(pair, phone_count)


def mark_pair_missed(pair: tuple[int, int], phone_count: int = 0) -> bool:
    """Record a miss and drop the item from phone/unassigned worklists."""
    phone_count = _count(phone_count)
    try:
        import collect_layout

        if collect_layout.list_mode() == "mode2":
            import list_pool_manager

            return list_pool_manager.mark_missed(pair)
    except Exception:
        pass
    if phone_only_shard(phone_count) is not None or phone_only_shard(_fleet_count()) is not None:
        return False
    key = _pair_key(pair)
    added = False
    with _file_lock():
        missed = set(_missed_pairs())
        if pair not in missed:
            rows = _read_queue_lines(MISSED_PATH)
            rows.append(_url(pair))
            _write_queue_lines(MISSED_PATH, rows)
            missed.add(pair)
            added = True
            _invalidate_missed_cache()
        changed = False
        for path in [UNASSIGNED_PATH] + [_queue_path(i) for i in range(phone_count)]:
            lines = _read_queue_lines(path)
            kept: list[str] = []
            dropped = False
            for line in lines:
                p = _pair_from_line(line)
                if not dropped and p == pair:
                    dropped = True
                    changed = True
                    continue
                kept.append(line)
            if dropped:
                _write_queue_lines(path, kept)
        if changed:
            claims = _read_claims()
            claims.pop(key, None)
            _write_claims(claims)
    return added or changed


def _pair_key(pair: tuple[int, int]) -> str:
    return f"{pair[0]}/{pair[1]}"


def phone_only_shard(phone_count: int = 0) -> int | None:
    """When set, every pending item stays on this shard (no per-shard split)."""
    phone_count = _count(phone_count)
    if not PHONE_ONLY_PATH.is_file():
        return None
    try:
        data = json.loads(PHONE_ONLY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    shard = data.get("shard")
    if shard is None:
        return None
    try:
        s = int(shard)
    except (TypeError, ValueError):
        return None
    if 0 <= s < phone_count:
        return s
    return None


def set_phone_only(shard: int | None, phone_count: int = 0) -> None:
    if shard is None:
        try:
            PHONE_ONLY_PATH.unlink()
        except OSError:
            pass
        return
    PHONE_ONLY_PATH.write_text(
        json.dumps({"shard": int(shard)}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def pool_enabled() -> bool:
    if not POOL_PATH.is_file():
        return False
    try:
        data = json.loads(POOL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and bool(data.get("enabled"))


def _ensure_pool_mode() -> None:
    if not POOL_PATH.is_file():
        POOL_PATH.write_text(json.dumps({"enabled": True}, ensure_ascii=False) + "\n", encoding="utf-8")
    if not UNASSIGNED_PATH.is_file():
        UNASSIGNED_PATH.write_text("", encoding="utf-8")
    if not MISSED_PATH.is_file():
        MISSED_PATH.write_text("", encoding="utf-8")


def _queue_path(shard: int | str) -> Path:
    if shard in (SHARD_UNASSIGNED, "unassigned"):
        return UNASSIGNED_PATH
    return HERE / f"list_urls_{int(shard)}.txt"


def _read_queue_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(line)
    return rows


def _write_queue_lines(path: Path, lines: list[str]) -> None:
    path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def _pair_from_line(line: str) -> tuple[int, int] | None:
    match = _QUEUE.search(line.replace("\\", "/"))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def queue_left_unassigned() -> int:
    return queue_left(SHARD_UNASSIGNED)


def _filter_queue_lines(
    lines: list[str],
    *,
    allowed: set[tuple[int, int]],
    excluded: set[tuple[int, int]] | None = None,
) -> list[str]:
    skip = excluded if excluded is not None else _collected_pairs()
    kept: list[str] = []
    for line in lines:
        pair = _pair_from_line(line)
        if pair is None:
            continue
        if pair in skip or pair not in allowed:
            continue
        kept.append(line if line.startswith("http") else _url(pair))
    return kept


def _pairs_in_queues(phone_count: int) -> dict[tuple[int, int], int]:
    found: dict[tuple[int, int], int] = {}
    paths: list[tuple[int, Path]] = [(SHARD_UNASSIGNED, UNASSIGNED_PATH)]
    for shard in range(phone_count):
        paths.append((shard, _queue_path(shard)))
    for owner, path in paths:
        for line in _read_queue_lines(path):
            pair = _pair_from_line(line)
            if pair is not None and pair not in found:
                found[pair] = owner
    return found


def _sync_claims_from_queues(phone_count: int) -> None:
    mapping = _pairs_in_queues(phone_count)
    claims: dict[str, int] = {}
    for pair, owner in mapping.items():
        claims[_pair_key(pair)] = int(owner)
    _write_claims(claims)


def prune_saved_from_queues(phone_count: int = 0) -> int:
    """Drop saved / stale lines from unassigned and phone url files."""
    phone_count = _count(phone_count)
    source = task_lists.selected_path()
    if source is None:
        return 0
    ids = _item_ids(source)
    allowed = set(ids)
    excluded = _collected_pairs()
    removed = 0
    with _file_lock():
        targets = [UNASSIGNED_PATH] + [_queue_path(i) for i in range(phone_count)]
        for path in targets:
            before = _read_queue_lines(path)
            after = _filter_queue_lines(before, allowed=allowed, excluded=excluded)
            if len(after) != len(before):
                removed += len(before) - len(after)
                _write_queue_lines(path, after)
        _sync_claims_from_queues(phone_count)
    return removed


def remove_pair_from_queues(pair: tuple[int, int], phone_count: int = 0) -> bool:
    """Remove one item from whichever worklist file holds it (after a successful save)."""
    phone_count = _count(phone_count)
    try:
        import collect_layout

        if collect_layout.list_mode() == "mode2":
            import list_pool_manager

            # clear the lease BEFORE taking _file_lock (lock is not reentrant)
            return list_pool_manager.clear_pair(pair)
    except Exception:
        pass
    key = _pair_key(pair)
    changed = False
    with _file_lock():
        for path in [UNASSIGNED_PATH] + [_queue_path(i) for i in range(phone_count)]:
            lines = _read_queue_lines(path)
            kept: list[str] = []
            dropped = False
            for line in lines:
                p = _pair_from_line(line)
                if not dropped and p == pair:
                    dropped = True
                    changed = True
                    continue
                kept.append(line)
            if dropped:
                _write_queue_lines(path, kept)
        if changed:
            claims = _read_claims()
            claims.pop(key, None)
            _write_claims(claims)
    return changed


def transfer_withdraw(from_shard: int, count: int, phone_count: int = 0) -> dict:
    """Move up to count unsaved lines from a phone queue to unassigned."""
    phone_count = _count(phone_count)
    result = {"ok": True, "moved": 0, "from": int(from_shard), "unassigned": 0}
    take = max(0, int(count))
    if take <= 0:
        return result
    with _file_lock():
        source = task_lists.selected_path()
        if source is None:
            return {"ok": False, "error": "no list"}
        allowed = set(_item_ids(source))
        excluded = _collected_pairs()
        from_path = _queue_path(int(from_shard))
        lines = _filter_queue_lines(_read_queue_lines(from_path), allowed=allowed, excluded=excluded)
        if not lines:
            result["from_left"] = 0
            result["unassigned"] = queue_left_unassigned()
            return result
        moving = lines[-take:]
        staying = lines[:-take]
        _write_queue_lines(from_path, staying)
        pool = _filter_queue_lines(_read_queue_lines(UNASSIGNED_PATH), allowed=allowed, excluded=excluded)
        pool.extend(moving)
        _write_queue_lines(UNASSIGNED_PATH, pool)
        _sync_claims_from_queues(phone_count)
        result["moved"] = len(moving)
        result["from_left"] = len(staying)
        result["unassigned"] = len(pool)
    return result


def transfer_assign(to_shard: int, count: int, phone_count: int = 0) -> dict:
    """Move up to count lines from unassigned to a phone queue."""
    phone_count = _count(phone_count)
    result = {"ok": True, "moved": 0, "to": int(to_shard), "unassigned": 0}
    take = max(0, int(count))
    if take <= 0:
        return result
    with _file_lock():
        source = task_lists.selected_path()
        if source is None:
            return {"ok": False, "error": "no list"}
        allowed = set(_item_ids(source))
        excluded = _collected_pairs()
        pool = _filter_queue_lines(_read_queue_lines(UNASSIGNED_PATH), allowed=allowed, excluded=excluded)
        if not pool:
            result["to_left"] = queue_left(int(to_shard))
            result["unassigned"] = 0
            return result
        moving = pool[:take]
        staying = pool[take:]
        _write_queue_lines(UNASSIGNED_PATH, staying)
        dest = _filter_queue_lines(_read_queue_lines(_queue_path(int(to_shard))), allowed=allowed, excluded=excluded)
        dest.extend(moving)
        _write_queue_lines(_queue_path(int(to_shard)), dest)
        _sync_claims_from_queues(phone_count)
        result["moved"] = len(moving)
        result["to_left"] = len(dest)
        result["unassigned"] = len(staying)
    return result


def withdraw_unregistered_phone_queues() -> dict:
    """Move queue lines (and stray claims) off fleet slots with no registered phone."""
    import phone_fleet

    reg = set(phone_fleet.registered_shards())
    fleet = phone_fleet.fleet_slot_count()
    result: dict = {"ok": True, "moved": 0, "unassigned": 0, "from_shards": []}
    if not reg:
        result["ok"] = False
        result["error"] = "no phones"
        return result
    with _file_lock():
        source = task_lists.selected_path()
        allowed: set[tuple[int, int]] = set()
        excluded = _collected_pairs()
        if source is not None:
            allowed = set(_item_ids(source))
        pool = _filter_queue_lines(_read_queue_lines(UNASSIGNED_PATH), allowed=allowed, excluded=excluded)
        pool_pairs = {_pair_from_line(line) for line in pool}
        pool_pairs.discard(None)
        for shard in range(fleet):
            if shard in reg:
                continue
            path = _queue_path(shard)
            lines = _filter_queue_lines(_read_queue_lines(path), allowed=allowed, excluded=excluded)
            if not lines:
                _write_queue_lines(path, [])
                continue
            result["from_shards"].append(int(shard))
            for line in lines:
                pair = _pair_from_line(line)
                if pair is not None and pair not in pool_pairs:
                    pool_pairs.add(pair)
                result["moved"] += 1
            pool.extend(lines)
            _write_queue_lines(path, [])
        if source is not None:
            ids = _item_ids(source)
            pending = set(pending_pairs_from_ids(ids))
            claims = _clean_claims(_read_claims(), ids, excluded)
            for key, owner in list(claims.items()):
                if int(owner) in reg:
                    continue
                parts = key.split("/", 1)
                if len(parts) != 2:
                    claims.pop(key, None)
                    continue
                try:
                    pair = (int(parts[0]), int(parts[1]))
                except ValueError:
                    claims.pop(key, None)
                    continue
                claims.pop(key, None)
                if pair not in pending or pair in pool_pairs:
                    continue
                pool.append(_url(pair))
                pool_pairs.add(pair)
                result["moved"] += 1
            _write_claims(claims)
        seen: set[tuple[int, int]] = set()
        deduped: list[str] = []
        for line in pool:
            pair = _pair_from_line(line)
            if pair is not None and pair in seen:
                continue
            if pair is not None:
                seen.add(pair)
            deduped.append(line)
        _write_queue_lines(UNASSIGNED_PATH, deduped)
        _sync_claims_from_queues(fleet)
        result["unassigned"] = len(deduped)
    return result


def auto_distribute_evenly(phone_count: int = 0) -> dict:
    """Split every pending server item evenly across registered phones; clear unassigned."""
    phone_count = _count(phone_count)
    import phone_fleet

    set_phone_only(None, phone_count)
    recall = withdraw_unregistered_phone_queues()
    out = redistribute_pending_evenly(phone_fleet.registered_phone_count() or phone_count)
    out["recalled_unregistered"] = recall
    return out


def _read_watch() -> dict:
    if not WATCH_PATH.is_file():
        return {}
    try:
        data = json.loads(WATCH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_watch(data: dict) -> None:
    WATCH_PATH.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")


def _watch_list_total(watch: dict) -> int | None:
    """Previous server tasks.json row count (full list, not pending-only)."""
    if not watch:
        return None
    raw = watch.get("list_total")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def on_server_tasks_updated(phone_count: int = 0) -> dict:
    """Reconcile url queues with the live server list (see balance_manager for new-list rules)."""
    import balance_manager

    phone_count = _count(phone_count)
    _ensure_pool_mode()
    source = task_lists.selected_path()
    if source is None:
        return {"ok": False, "error": "no list"}
    ids = _item_ids(source)
    list_total = len(ids)
    pending_pairs = pending_pairs_from_ids(ids)
    pending_set = set(pending_pairs)
    meta: dict = {}
    try:
        meta = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        meta = {}
    watch = _read_watch()
    old_list_total = _watch_list_total(watch)
    list_analysis = balance_manager.handle_incoming_server_list(
        phone_count,
        old_total=old_list_total,
        new_total=list_total,
        ids=ids,
    )
    is_new_list = bool(list_analysis.get("redistributed"))
    prune_saved_from_queues(phone_count)
    new_pairs: list[tuple[int, int]] = []
    with _file_lock():
        assigned = _pairs_in_queues(phone_count)
        new_pairs = [pair for pair in pending_pairs if pair not in assigned]
        if new_pairs and not is_new_list:
            pool = _filter_queue_lines(
                _read_queue_lines(UNASSIGNED_PATH),
                allowed=pending_set,
                excluded=_collected_pairs(),
            )
            order = {pair: index for index, pair in enumerate(pending_pairs)}
            for pair in sorted(new_pairs, key=lambda p: order.get(p, 0)):
                pool.append(_url(pair))
            _write_queue_lines(UNASSIGNED_PATH, pool)
    if not is_new_list:
        with _file_lock():
            _sync_claims_from_queues(phone_count)
    _write_watch(
        {
            "list_total": list_total,
            "pending_count": len(pending_pairs),
            "updated_at": str(meta.get("updated_at") or ""),
        }
    )
    balance = queue_balance(phone_count)
    balance["is_new_list"] = is_new_list
    balance["list_analysis"] = list_analysis.get("analysis")
    balance["list_total"] = list_total
    balance["list_total_delta"] = (
        list_total - old_list_total if old_list_total is not None else None
    )
    balance["new_to_unassigned"] = len(new_pairs) if not is_new_list else 0
    return balance


def queue_balance(phone_count: int = 0) -> dict:
    """Sum of queue lines should match server pending (unsaved) count."""
    phone_count = _count(phone_count)
    source = task_lists.selected_path()
    if source is None:
        return {"ok": False, "server_pending": 0, "queued": 0, "unassigned": 0, "phones": []}
    ids = _item_ids(source)
    server_pending = len(pending_pairs_from_ids(ids))
    unassigned = queue_left_unassigned()
    phones: list[dict] = []
    queued = unassigned
    for shard in range(phone_count):
        left = queue_left(shard)
        queued += left
        phones.append({"shard": shard, "pending": left})
    return {
        "ok": queued == server_pending,
        "server_pending": server_pending,
        "queued": queued,
        "unassigned": unassigned,
        "phones": phones,
    }


def apply_worklists(phone_count: int = 0) -> dict:
    """Rebuild list_urls_*.txt; honour phone-only mode when active."""
    phone_count = _count(phone_count)
    only = phone_only_shard(phone_count)
    if only is not None:
        return assign_all_pending_to(only, phone_count)
    if pool_enabled():
        removed = prune_saved_from_queues(phone_count)
        bal = queue_balance(phone_count)
        bal["pruned"] = removed
        return bal
    with _file_lock():
        buckets, _, _, _ = _build_buckets(phone_count)
        for shard, rows in enumerate(buckets):
            path = HERE / f"list_urls_{shard}.txt"
            path.write_text(("\n".join(rows) + "\n") if rows else "", encoding="utf-8")
        total = sum(len(rows) for rows in buckets)
    return {"ok": True, "pending": total, "phones": [{"shard": i, "count": len(buckets[i])} for i in range(phone_count)]}


def owner_shard(
    index: int,
    pair: tuple[int, int],
    claims: dict[str, int],
    phone_count: int,
    reg: list[int] | None = None,
) -> int:
    key = _pair_key(pair)
    owner = claims.get(key)
    if owner is not None and 0 <= int(owner) < phone_count:
        return int(owner)
    # Unclaimed items go only to registered shards (never an empty fleet slot).
    if reg is None:
        reg = _registered_shards()
    if reg:
        return reg[index % len(reg)]
    return index % max(1, phone_count)


def _build_buckets(phone_count: int) -> tuple[list[list[str]], list[tuple[int, int]], set[tuple[int, int]], dict[str, int]]:
    source = task_lists.selected_path()
    if source is None:
        return [[] for _ in range(phone_count)], [], set(), {}
    ids = _item_ids(source)
    saved = _saved_pairs()
    claims = _read_claims()
    reg = _registered_shards()
    size = max(phone_count, (max(reg) + 1) if reg else 0)
    buckets: list[list[str]] = [[] for _ in range(size)]
    pending_pairs: list[tuple[int, tuple[int, int]]] = []
    for index, pair in enumerate(ids):
        if pair in saved:
            continue
        shard = owner_shard(index, pair, claims, phone_count, reg)
        buckets[shard].append(_url(pair))
        pending_pairs.append((index, pair))
    return buckets, ids, saved, claims


def progress(phone_count: int = 0) -> dict:
    phone_count = _count(phone_count)
    source = task_lists.selected_path()
    empty = [{"total": 0, "saved": 0, "left": 0} for _ in range(phone_count)]
    if source is None:
        return {"name": "", "count": 0, "phones": empty}
    ids = _item_ids(source)
    saved = _saved_pairs()
    claims = _read_claims()
    phones = [{"total": 0, "saved": 0, "left": 0} for _ in range(phone_count)]
    for index, pair in enumerate(ids):
        slot = phones[owner_shard(index, pair, claims, phone_count)]
        slot["total"] += 1
        if pair in saved:
            slot["saved"] += 1
        else:
            slot["left"] += 1
    return {"name": source.name, "count": len(ids), "phones": phones}


def _url(pair: tuple[int, int]) -> str:
    return f"https://shopee.com.br/product/{pair[0]}/{pair[1]}"


@contextmanager
def _file_lock():
    HERE.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_PATH, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _read_claims() -> dict[str, int]:
    if not CLAIMS_PATH.is_file():
        return {}
    try:
        data = json.loads(CLAIMS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for key, owner in data.items():
        try:
            out[str(key)] = int(owner)
        except (TypeError, ValueError):
            continue
    return out


def _write_claims(claims: dict[str, int]) -> None:
    CLAIMS_PATH.write_text(json.dumps(claims, ensure_ascii=False) + "\n", encoding="utf-8")


def _clean_claims(claims: dict[str, int], ids: list[tuple[int, int]], saved: set[tuple[int, int]]) -> dict[str, int]:
    live = {_pair_key(pair) for pair in ids}
    saved_keys = {_pair_key(pair) for pair in saved}
    return {key: owner for key, owner in claims.items() if key in live and key not in saved_keys}


def sync_claims() -> None:
    """Drop claims the overwritten server list no longer contains."""
    source = task_lists.selected_path()
    with _file_lock():
        claims = _read_claims()
        if source is None:
            _write_claims({})
            return
        ids = _item_ids(source)
        cleaned = _clean_claims(claims, ids, _saved_pairs())
        if cleaned != claims:
            _write_claims(cleaned)


def claim_next(shard: int, held: set[tuple[int, int]] | None = None) -> str | None:
    """Give this phone the next list item that no other phone is holding."""
    try:
        import collect_layout

        if collect_layout.list_mode() == "mode2":
            import list_pool_manager

            urls = list_pool_manager.lease(int(shard), 1)
            if urls:
                return urls[0]
            # 풀 소진 — 재수집 단계(미저장→없음)로 넘어갈 수 있는지 확인
            if list_pool_manager.advance_cycle_if_drained():
                urls = list_pool_manager.lease(int(shard), 1)
                return urls[0] if urls else None
            return None
    except Exception:
        pass
    source = task_lists.selected_path()
    if source is None:
        return None
    owned = held or set()
    with _file_lock():
        ids = _item_ids(source)
        saved = _saved_pairs()
        claims = _clean_claims(_read_claims(), ids, saved)
        chosen: tuple[int, int] | None = None
        for pair in ids:
            if pair in saved or pair in owned:
                continue
            owner = claims.get(_pair_key(pair))
            if owner is None or owner == int(shard):
                chosen = pair
                break
        if chosen is None:
            _write_claims(claims)
            return None
        claims[_pair_key(chosen)] = int(shard)
        _write_claims(claims)
        return _url(chosen)


def release_shard(shard: int) -> None:
    """A stopped phone gives its unfinished items back to the list."""
    with _file_lock():
        claims = _read_claims()
        left = {key: owner for key, owner in claims.items() if owner != int(shard)}
        if left != claims:
            _write_claims(left)


def write_worklists(phone_count: int = 0) -> int:
    """Write each phone's unsaved share of the selected list."""
    phone_count = _count(phone_count)
    result = apply_worklists(phone_count)
    if not isinstance(result, dict):
        return 0
    if result.get("count") is not None:
        return int(result["count"])
    phones = result.get("phones") if isinstance(result.get("phones"), list) else []
    return sum(int(row.get("count") or 0) for row in phones if isinstance(row, dict))


def refresh_worklist_shard(shard: int, phone_count: int = 0) -> int:
    """Refresh one phone's url file from claims + default shard split."""
    phone_count = _count(phone_count)
    if pool_enabled():
        prune_saved_from_queues(phone_count)
        return queue_left(int(shard))
    with _file_lock():
        buckets, _, _, _ = _build_buckets(phone_count)
        rows = buckets[int(shard)]
        path = HERE / f"list_urls_{shard}.txt"
        path.write_text(("\n".join(rows) + "\n") if rows else "", encoding="utf-8")
        return len(rows)


def _default_shard_collecting(shard: int) -> bool:
    try:
        import harvest_control

        return bool(harvest_control._shard_running(int(shard)))
    except Exception:
        return False


def auto_rebalance_for_shard(
    target_shard: int,
    phone_count: int,
    *,
    is_collecting: Callable[[int], bool] | None = None,
    low_threshold: int = 0,
    registered: set[int] | None = None,
) -> dict:
    """Top up target when queue <= low_threshold (0 = empty only). Registered shards only."""
    target = int(target_shard)
    result: dict = {
        "ok": True,
        "moved": 0,
        "target": target,
        "donor": None,
        "mode": None,
    }
    if phone_count <= 1 or target < 0 or target >= phone_count:
        return result
    collecting = is_collecting or _default_shard_collecting
    reg = registered if registered is not None else set(range(phone_count))
    left = [queue_left(s) if s in reg else 0 for s in range(phone_count)]
    if left[target] > max(0, int(low_threshold)):
        result["target_left"] = left[target]
        return result
    idle = [
        s
        for s in reg
        if s != target and left[s] > 0 and not collecting(s)
    ]
    if idle:
        donor = max(idle, key=lambda s: left[s])
        moved = transfer_queue_between(donor, target, fraction=1.0)
        result.update(moved)
        result["mode"] = "idle_donor"
        result["donor"] = donor
        return result
    donors = [s for s in reg if s != target and left[s] > 0]
    if not donors:
        result["target_left"] = 0
        return result
    donor = max(donors, key=lambda s: left[s])
    moved = transfer_queue_between(donor, target, fraction=0.5)
    result.update(moved)
    result["mode"] = "busy_half"
    result["donor"] = donor
    return result


def transfer_queue_between(from_shard: int, to_shard: int, fraction: float = 0.5) -> dict:
    """Move a fraction of one phone's list_urls queue to another (updates claims)."""
    result = {"ok": True, "moved": 0, "from": int(from_shard), "to": int(to_shard)}
    with _file_lock():
        source = task_lists.selected_path()
        if source is None:
            result["ok"] = False
            result["error"] = "no list"
            return result
        ids = _item_ids(source)
        saved = _saved_pairs()
        claims = _clean_claims(_read_claims(), ids, saved)
        from_path = HERE / f"list_urls_{int(from_shard)}.txt"
        if not from_path.is_file():
            return result
        rows: list[tuple[tuple[int, int], str]] = []
        for line in from_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            match = _QUEUE.search(line.replace("\\", "/"))
            if not match:
                continue
            pair = (int(match.group(1)), int(match.group(2)))
            if pair in saved:
                continue
            rows.append((pair, line))
        if not rows:
            return result
        take = max(1, int(len(rows) * float(fraction)))
        moving = rows[-take:]
        for pair, _line in moving:
            claims[_pair_key(pair)] = int(to_shard)
        _write_claims(claims)
        buckets, _, _, _ = _build_buckets(max(int(from_shard), int(to_shard)) + 1)
        phone_count = max(len(buckets), int(to_shard) + 1, int(from_shard) + 1)
        buckets, _, _, _ = _build_buckets(phone_count)
        for shard, lines in enumerate(buckets):
            path = HERE / f"list_urls_{shard}.txt"
            path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
        result["moved"] = len(moving)
        result["from_left"] = queue_left(int(from_shard))
        result["to_left"] = queue_left(int(to_shard))
    return result


def assign_all_pending_to(shard: int, phone_count: int = 0) -> dict:
    """Give every unsaved list item to one phone shard."""
    phone_count = _count(phone_count)
    set_phone_only(int(shard), phone_count)
    result = {"ok": True, "pending": 0, "shard": int(shard), "count": 0}
    with _file_lock():
        source = task_lists.selected_path()
        if source is None:
            result["ok"] = False
            result["error"] = "no list"
            return result
        ids = _item_ids(source)
        saved = _saved_pairs()
        pending = pending_pairs_from_ids(ids)
        claims: dict[str, int] = {_pair_key(pair): int(shard) for pair in pending}
        _write_claims(claims)
        buckets: list[list[str]] = [[] for _ in range(phone_count)]
        for pair in pending:
            buckets[int(shard)].append(_url(pair))
        _write_queue_lines(UNASSIGNED_PATH, [])
        for s, rows in enumerate(buckets):
            path = HERE / f"list_urls_{s}.txt"
            path.write_text(("\n".join(rows) + "\n") if rows else "", encoding="utf-8")
        result["pending"] = len(pending)
        result["count"] = len(buckets[int(shard)])
    return result


def redistribute_pending_evenly(phone_count: int = 0) -> dict:
    """Split unsaved list items evenly across registered phones only."""
    phone_count = _count(phone_count)
    import phone_fleet

    set_phone_only(None, phone_count)
    shards = phone_fleet.registered_shards()
    result: dict = {"ok": True, "pending": 0, "phones": []}
    if not shards:
        result["ok"] = False
        result["error"] = "no phones"
        return result
    with _file_lock():
        source = task_lists.selected_path()
        if source is None:
            result["ok"] = False
            result["error"] = "no list"
            return result
        ids = _item_ids(source)
        pending: list[tuple[int, int]] = pending_pairs_from_ids(ids)
        claims: dict[str, int] = {}
        buckets: dict[int, list[str]] = {shard: [] for shard in shards}
        for index, pair in enumerate(pending):
            shard = shards[index % len(shards)]
            claims[_pair_key(pair)] = shard
            buckets[shard].append(_url(pair))
        _write_claims(claims)
        _write_queue_lines(UNASSIGNED_PATH, [])
        fleet = phone_fleet.fleet_slot_count()
        for slot in range(fleet):
            rows = buckets.get(slot, [])
            path = HERE / f"list_urls_{slot}.txt"
            path.write_text(("\n".join(rows) + "\n") if rows else "", encoding="utf-8")
            if slot in buckets:
                result["phones"].append({"shard": slot, "count": len(rows)})
        result["pending"] = len(pending)
        result["unassigned"] = 0
    return result


def rebalance_idle_shards(
    phone_count: int = 0,
    *,
    idle_max: int = 5,
    donor_min: int = 10,
    target_shard: int | None = None,
) -> dict:
    """When a phone has almost no list left, move half of the busiest phone's share to it."""
    phone_count = _count(phone_count)
    result: dict = {"ok": True, "moved": 0, "idle_shard": None, "donor_shard": None, "actions": []}
    with _file_lock():
        source = task_lists.selected_path()
        if source is None:
            result["ok"] = False
            result["error"] = "no list"
            return result
        ids = _item_ids(source)
        saved = _saved_pairs()
        claims = _clean_claims(_read_claims(), ids, saved)
        reg = _registered_shards()
        left = [0] * phone_count
        by_shard: list[list[tuple[int, tuple[int, int]]]] = [[] for _ in range(phone_count)]
        for index, pair in enumerate(ids):
            if pair in saved:
                continue
            shard = owner_shard(index, pair, claims, phone_count, reg)
            left[shard] += 1
            by_shard[shard].append((index, pair))
        targets = [int(target_shard)] if target_shard is not None else list(range(phone_count))
        for idle in targets:
            if idle < 0 or idle >= phone_count or left[idle] > idle_max:
                continue
            donor = max(
                (s for s in range(phone_count) if s != idle),
                key=lambda s: left[s],
                default=-1,
            )
            if donor < 0 or left[donor] < donor_min:
                continue
            take = max(1, left[donor] // 2)
            moving = by_shard[donor][-take:]
            for _index, pair in moving:
                claims[_pair_key(pair)] = idle
            left[donor] -= len(moving)
            left[idle] += len(moving)
            for _index, pair in moving:
                by_shard[idle].append((_index, pair))
            by_shard[donor] = by_shard[donor][: len(by_shard[donor]) - len(moving)]
            _write_claims(claims)
            result["moved"] += len(moving)
            result["idle_shard"] = idle
            result["donor_shard"] = donor
            result["actions"].append(
                {"idle": idle, "donor": donor, "moved": len(moving), "idle_left": left[idle], "donor_left": left[donor]}
            )
        buckets, _, _, _ = _build_buckets(phone_count)
        for shard, rows in enumerate(buckets):
            path = HERE / f"list_urls_{shard}.txt"
            path.write_text(("\n".join(rows) + "\n") if rows else "", encoding="utf-8")
    return result


def queue_left(shard: int) -> int:
    """Unsaved lines in this phone's current list_urls file (matches harvest queue)."""
    path = _queue_path(int(shard))
    if not path.is_file():
        return 0
    excluded = _collected_pairs()
    count = 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _QUEUE.search(line.replace("\\", "/"))
        if not match:
            continue
        pair = (int(match.group(1)), int(match.group(2)))
        if pair not in excluded:
            count += 1
    return count


def workload_view(phone_count: int = 0) -> dict:
    """Remaining items on the live server list (Upload dedupe), split by phone shard."""
    phone_count = _count(phone_count)
    source = task_lists.selected_path()
    empty = {
        "list_total": 0,
        "list_done": 0,
        "list_pending": 0,
        "list_missed": 0,
        "list_processed": 0,
        "progress_pct": 0.0,
        "updated_at": "",
        "pool": pool_enabled(),
        "unassigned": 0,
        "balance_ok": True,
        "phones": [{"shard": i, "pending": 0} for i in range(phone_count)],
    }
    if source is None:
        return empty
    try:
        meta = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        meta = {}
    ids = _item_ids(source)
    cycle_extra: dict = {}
    try:
        import list_cycle_manager

        cycle_extra = list_cycle_manager.workload_extras(ids)
    except Exception:
        cycle_extra = {}
    saved = _saved_pairs()
    missed = _missed_pairs()
    collected = _collected_pairs()
    total = len(ids)
    done = sum(1 for pair in ids if pair in saved)
    miss_on_list = sum(1 for pair in ids if pair in missed)
    processed = sum(1 for pair in ids if pair in collected)
    pending = len(pending_pairs_from_ids(ids))
    pct = round(100.0 * processed / total, 1) if total else 0.0  # 수행률 = (완료+미저장)/전체
    try:
        import collect_layout

        lmode = collect_layout.list_mode()
    except Exception:
        lmode = "mode1"
    if lmode == "mode2":
        try:
            import list_pool_manager

            snap = list_pool_manager.snapshot()
            lease_rows = snap.get("per_shard_lease") or {}
            reg = _registered_shards()
            phones = [{"shard": s, "pending": int(lease_rows.get(s, 0))} for s in reg]
            queue_sum = int(snap.get("pool_remaining") or 0) + int(snap.get("in_flight") or 0)
            unassigned = int(snap.get("pool_remaining") or 0)
        except Exception:
            phones = []
            queue_sum = pending
            unassigned = pending
        out = {
            "list_total": total,
            "list_done": done,
            "list_missed": miss_on_list,
            "list_processed": processed,
            "list_pending": queue_sum,
            "list_pending_server": pending,
            "queue_pending": queue_sum,
            "progress_pct": pct,
            "updated_at": str(meta.get("updated_at") or "") if isinstance(meta, dict) else "",
            "pool": True,
            "list_mode": "mode2",
            "unassigned": unassigned,
            "balance_ok": bool(cycle_extra.get("list_accounting_ok", True)),
            "phones": phones,
        }
        out.update(cycle_extra)
        return out
    bal = queue_balance(phone_count)
    phones = bal.get("phones") if isinstance(bal.get("phones"), list) else []
    queue_sum = int(bal.get("queued") or 0)
    out = {
        "list_total": total,
        "list_done": done,
        "list_missed": miss_on_list,
        "list_processed": processed,
        "list_pending": queue_sum,
        "list_pending_server": pending,
        "queue_pending": queue_sum,
        "progress_pct": pct,
        "updated_at": str(meta.get("updated_at") or "") if isinstance(meta, dict) else "",
        "pool": pool_enabled(),
        "list_mode": "mode1",
        "unassigned": int(bal.get("unassigned") or 0),
        "balance_ok": bool(bal.get("ok")) and bool(cycle_extra.get("list_accounting_ok", True)),
        "phones": phones,
    }
    out.update(cycle_extra)
    return out
