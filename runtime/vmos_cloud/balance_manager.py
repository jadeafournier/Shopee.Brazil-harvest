# -*- coding: utf-8 -*-
"""Fleet balance: low-queue top-up, solo finish mode, server list detection, rotation handoff."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_PATH = HERE / "balance_state.json"

LOW_QUEUE = 50
SOLO_TOTAL_MAX = 200
SERVER_MATCH_TOLERANCE = 10

_ROTATE_MARKERS = (
    "계정 전환",
    "보관 세션으로 전환",
    "세션 PC 보관",
    "오늘 보관 세션 순환",
    "새 계정 로그인 대기",
)
def _load_state() -> dict:
    if not STATE_PATH.is_file():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(data: dict) -> None:
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def _registered_shards() -> list[int]:
    import phone_fleet

    return phone_fleet.registered_shards()


def _phone_rows() -> list[dict]:
    import phone_fleet

    return phone_fleet.load_phones_raw()


def _shard_name(shard: int) -> str:
    for row in _phone_rows():
        if int(row["shard"]) == int(shard):
            return str(row.get("name") or "")
    return ""


def _sessions_available(shard: int) -> int:
    name = _shard_name(shard)
    if not name:
        return 0
    try:
        import session_ledger

        row = session_ledger.phone_session_counts(name, int(shard))
        return int(row.get("sessions_available") or 0)
    except Exception:
        return 0


def _sent_names() -> set[str]:
    try:
        from _sever_bridge import _load_sent

        return set(_load_sent().keys())
    except Exception:
        return set()


def _saved_not_uploaded_on_list(ids: list[tuple[int, int]]) -> int:
    import list_progress

    saved = list_progress._saved_pairs()
    sent = _sent_names()
    count = 0
    for shop, item in ids:
        if (shop, item) not in saved:
            continue
        if f"{shop}_{item}.json" in sent:
            continue
        count += 1
    return count


def local_inventory(ids: list[tuple[int, int]]) -> dict:
    import list_progress

    pending_n = len(list_progress.pending_pairs_from_ids(ids))
    not_up = _saved_not_uploaded_on_list(ids)
    server_total = len(ids)
    local_sum = pending_n + not_up
    reg = _registered_shards()
    queued = int(list_progress.queue_left_unassigned())
    for shard in reg:
        queued += int(list_progress.queue_left(shard))
    return {
        "queued": queued,
        "pending_not_saved": pending_n,
        "saved_not_uploaded": not_up,
        "local_sum": local_sum,
        "server_total": server_total,
        "delta": abs(local_sum - server_total),
        "within_tolerance": abs(local_sum - server_total) <= SERVER_MATCH_TOLERANCE,
    }


def analyze_server_list_change(
    old_total: int | None,
    new_total: int,
    ids: list[tuple[int, int]],
) -> dict:
    """Detect a new server list vs normal shrink (upload) vs partial removal."""
    inv = local_inventory(ids)
    is_new = False
    reason = "unchanged"
    if old_total is None:
        reason = "first_watch"
    elif new_total > old_total:
        is_new = True
        reason = "server_grew"
    elif new_total < old_total:
        if inv["within_tolerance"]:
            reason = "normal_shrink_upload"
        else:
            reason = "partial_removal"
    else:
        reason = "same_count"
    return {
        "is_new_list": is_new,
        "reason": reason,
        "inventory": inv,
        "old_total": old_total,
        "new_total": new_total,
    }


def handle_incoming_server_list(
    phone_count: int,
    *,
    old_total: int | None,
    new_total: int,
    ids: list[tuple[int, int]],
) -> dict:
    """Called when tasks.json changes. Replaces ±100 sudden-delta detection."""
    import list_progress

    analysis = analyze_server_list_change(old_total, new_total, ids)
    out = {"ok": True, "analysis": analysis, "redistributed": False}
    if analysis["is_new_list"]:
        list_progress.withdraw_unregistered_phone_queues()
        list_progress.redistribute_pending_evenly(phone_count)
        out["redistributed"] = True
        _save_state({})
    elif analysis["reason"] == "partial_removal":
        list_progress.prune_saved_from_queues(phone_count)
        with list_progress._file_lock():
            list_progress._sync_claims_from_queues(phone_count)
    return out


def server_pending_total() -> int:
    import list_progress
    import task_lists

    source = task_lists.selected_path()
    if source is None:
        return 0
    ids = list_progress._item_ids(source)
    return len(list_progress.pending_pairs_from_ids(ids))


def solo_state() -> dict:
    return _load_state()


def is_solo_mode() -> bool:
    return bool(_load_state().get("solo_mode"))


def solo_collector_shard() -> int | None:
    raw = _load_state().get("primary_shard")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def should_skip_low_extend(shard: int) -> bool:
    if not is_solo_mode():
        return False
    primary = solo_collector_shard()
    return primary is not None and int(shard) == primary


def _collecting_shards() -> list[int]:
    import list_progress

    reg = _registered_shards()
    return [s for s in reg if list_progress._default_shard_collecting(s)]


def enter_solo_mode_if_needed() -> dict:
    """When total pending <= 200, one collector (most sessions left) takes all queues."""
    import harvest_control
    import list_progress

    pending = server_pending_total()
    if pending > SOLO_TOTAL_MAX:
        if is_solo_mode():
            _save_state({})
        return {"ok": True, "solo": False, "pending": pending}

    collecting = _collecting_shards()
    if len(collecting) <= 1:
        if is_solo_mode() and collecting:
            st = _load_state()
            st["solo_mode"] = True
            st["primary_shard"] = collecting[0]
            st["waiting_shards"] = []
            _save_state(st)
        return {"ok": True, "solo": len(collecting) == 1, "pending": pending}

    ranked = sorted(collecting, key=lambda s: (_sessions_available(s), -s), reverse=True)
    primary = ranked[0]
    waiting = [s for s in ranked[1:]]

    moved_total = 0
    for donor in waiting:
        moved = list_progress.transfer_queue_between(donor, primary, fraction=1.0)
        moved_total += int(moved.get("moved") or 0)
        name = _shard_name(donor)
        if name:
            harvest_control.stop(name)

    rest = [s for s in _registered_shards() if s not in ranked]
    rest.sort(key=lambda s: _sessions_available(s), reverse=True)
    for donor in rest:
        if list_progress.queue_left(donor) <= 0:
            continue
        moved = list_progress.transfer_queue_between(donor, primary, fraction=1.0)
        moved_total += int(moved.get("moved") or 0)

    _save_state(
        {
            "solo_mode": True,
            "primary_shard": primary,
            "waiting_shards": waiting + rest,
            "pending_at_enter": pending,
        }
    )
    return {
        "ok": True,
        "solo": True,
        "primary_shard": primary,
        "waiting_shards": waiting + rest,
        "moved": moved_total,
        "pending": pending,
    }


def extend_queue_if_low(shard: int, phone_count: int) -> dict:
    """When queue <= 50: idle donor / busy half / unassigned pool (not in solo-primary)."""
    import list_progress

    phone_count = list_progress._count(phone_count)
    if should_skip_low_extend(shard):
        return {"ok": True, "skipped": "solo_primary", "moved": 0}

    enter_solo_mode_if_needed()
    if is_solo_mode() and solo_collector_shard() != int(shard):
        return {"ok": True, "skipped": "solo_waiting", "moved": 0}

    left = list_progress.queue_left(int(shard))
    if left > LOW_QUEUE:
        return {"ok": True, "moved": 0, "left": left}

    reg = set(_registered_shards())
    result = list_progress.auto_rebalance_for_shard(
        int(shard),
        phone_count,
        low_threshold=LOW_QUEUE,
        registered=reg,
    )
    moved = int(result.get("moved") or 0)
    un = list_progress.queue_left_unassigned()
    if list_progress.queue_left(int(shard)) <= LOW_QUEUE and un > 0:
        extra = list_progress.transfer_assign(int(shard), un, phone_count)
        moved += int(extra.get("moved") or 0)
        result["unassigned_pull"] = extra
    result["moved_total"] = moved
    return result


def _log_tail(shard: int, *, max_lines: int = 80) -> list[str]:
    path = HERE / f"list_{int(shard)}.log"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-max_lines:]


def check_rotation_handoff(shard: int) -> dict:
    """Solo primary entering session rotation → move queue to next waiting phone."""
    st = _load_state()
    if not st.get("solo_mode"):
        return {"ok": True, "handoff": False}
    primary = st.get("primary_shard")
    if primary is None or int(primary) != int(shard):
        return {"ok": True, "handoff": False}

    path = HERE / f"list_{int(shard)}.log"
    pos_key = f"log_byte_{shard}"
    pos = int(st.get(pos_key) or 0)
    if not path.is_file():
        return {"ok": True, "handoff": False}
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"ok": True, "handoff": False}
    st[pos_key] = len(raw)
    _save_state(st)
    new_part = raw[pos:] if pos <= len(raw) else raw
    if not any(marker in new_part for marker in _ROTATE_MARKERS):
        return {"ok": True, "handoff": False}

    st = _load_state()
    waiting = [int(s) for s in (st.get("waiting_shards") or []) if int(s) in _registered_shards()]
    if not waiting:
        return {"ok": True, "handoff": False, "note": "no_waiting_phone"}

    nxt = waiting[0]
    rest = waiting[1:]
    import harvest_control
    import list_progress

    moved = list_progress.transfer_queue_between(int(shard), nxt, fraction=1.0)
    next_name = _shard_name(nxt)
    if next_name:
        harvest_control.start(next_name)

    st["primary_shard"] = nxt
    st["waiting_shards"] = rest + [int(shard)]
    _save_state(st)
    return {"ok": True, "handoff": True, "from": int(shard), "to": nxt, "moved": moved}


def tick() -> dict:
    """Periodic: solo mode entry + rotation handoff for active collectors."""
    enter = enter_solo_mode_if_needed()
    handoffs = []
    for shard in _collecting_shards():
        h = check_rotation_handoff(shard)
        if h.get("handoff"):
            handoffs.append(h)
    return {"solo": enter, "handoffs": handoffs}
