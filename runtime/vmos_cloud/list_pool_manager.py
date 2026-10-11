# -*- coding: utf-8 -*-
"""목록관리2 (list-mode 2): one global work pool; phones lease batches atomically.

Design goals
------------
* A single global remaining list (``list_urls_pool2.txt``) holds every item that
  still needs collecting. There is **no** per-phone allocation and **no** remainder
  rebalancing — collecting phones pull work directly (work-stealing), which is the
  optimal dynamic load balancer for workers of different speeds.
* Duplicate prevention is the manager's only hard job: every ``lease`` /
  ``complete`` / ``reconcile`` runs inside ``list_progress._file_lock`` — an OS
  file lock shared across all phone *processes* — so two phones can never take the
  same item. Lock hold time is a few milliseconds (read/write one text file),
  negligible next to the seconds each product takes, so it never bottlenecks speed.
* Saved items are removed from the pool synchronously (a saved pair is never
  re-leased because ``_saved_pairs`` filters it out).
* Only running collectors lease; a stopped phone simply never calls ``lease``.
* New-list detection: the server keeps re-sending "everything not yet uploaded",
  so the server list should equal the global remaining pool (within a small
  tolerance). If the server list is meaningfully larger, it is a new/added list
  and the pool is refreshed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import list_progress as lp

HERE = Path(__file__).resolve().parent
POOL_PATH = HERE / "list_urls_pool2.txt"
LEASES_PATH = HERE / "list_leases2.json"

LEASE_BATCH = 40          # items handed to one phone per capture launch
LEASE_TTL = 1800          # seconds before a crashed phone's lease is reclaimed
MAX_TRIES = 2             # attempts before an item is parked as missed
SERVER_MATCH_TOLERANCE = 10


def _now() -> float:
    return time.time()


def _read_state() -> dict:
    """{"shards": {shard: {"items": {key: {at}}}}, "tries": {key: n}}"""
    if not LEASES_PATH.is_file():
        return {"shards": {}, "tries": {}}
    try:
        data = json.loads(LEASES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"shards": {}, "tries": {}}
    if not isinstance(data, dict):
        return {"shards": {}, "tries": {}}
    # migrate the older flat {shard: {items}} layout if present
    if "shards" not in data and "tries" not in data:
        data = {"shards": data, "tries": {}}
    data.setdefault("shards", {})
    data.setdefault("tries", {})
    if not isinstance(data["shards"], dict):
        data["shards"] = {}
    if not isinstance(data["tries"], dict):
        data["tries"] = {}
    return data


def _write_state(data: dict) -> None:
    tmp = LEASES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(LEASES_PATH)


def _read_pool() -> list[str]:
    return lp._read_queue_lines(POOL_PATH)


def _write_pool(lines: list[str]) -> None:
    lp._write_queue_lines(POOL_PATH, lines)


def _leased_pairs(state: dict) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for row in (state.get("shards") or {}).values():
        for key in (row.get("items") or {}):
            try:
                shop, item = key.split("/", 1)
                out.add((int(shop), int(item)))
            except ValueError:
                continue
    return out


def _reclaim_expired(state: dict, pool: list[str], saved: set[tuple[int, int]]) -> bool:
    """Return expired leases (crashed phones) to the pool head. Caller holds lock."""
    now = _now()
    changed = False
    shards = state.get("shards") or {}
    in_pool = {lp._pair_from_line(line) for line in pool}
    for shard, row in list(shards.items()):
        items = row.get("items") or {}
        for key, meta in list(items.items()):
            try:
                shop, item = key.split("/", 1)
                pair = (int(shop), int(item))
            except ValueError:
                items.pop(key, None)
                changed = True
                continue
            if pair in saved:
                items.pop(key, None)
                state.get("tries", {}).pop(key, None)
                changed = True
                continue
            if now - float(meta.get("at") or 0) >= LEASE_TTL:
                if pair not in in_pool:
                    pool.insert(0, lp._url(pair))
                    in_pool.add(pair)
                items.pop(key, None)
                changed = True
        if not items:
            shards.pop(shard, None)
            changed = True
        else:
            row["items"] = items
    return changed


# --------------------------------------------------------------------------- #
# Lease / complete                                                            #
# --------------------------------------------------------------------------- #
def lease(shard: int, n: int = LEASE_BATCH) -> list[str]:
    """Atomically take up to ``n`` uncollected items for this shard."""
    shard = str(int(shard))
    with lp._file_lock():
        saved = lp._saved_pairs()
        pool = _read_pool()
        state = _read_state()
        _reclaim_expired(state, pool, saved)
        # drop already-saved lines
        pool = [ln for ln in pool if lp._pair_from_line(ln) not in saved]
        shards = state["shards"]
        tries = state["tries"]
        taken: list[str] = []
        rest: list[str] = []
        now = _now()
        row = shards.get(shard) or {"items": {}}
        items = row.get("items") or {}
        for line in pool:
            pair = lp._pair_from_line(line)
            if pair is None:
                continue
            if len(taken) < max(1, int(n)):
                key = lp._pair_key(pair)
                items[key] = {"at": now}
                tries[key] = int(tries.get(key) or 0) + 1
                taken.append(lp._url(pair))
            else:
                rest.append(line)
        row["items"] = items
        if items:
            shards[shard] = row
        _write_pool(rest)
        _write_state(state)
        return taken


def complete(shard: int) -> dict:
    """After a capture pass: saved → drop; unsaved → retry (back to pool) or miss."""
    shard = str(int(shard))
    result = {"ok": True, "done": 0, "retry": 0, "missed": 0}
    with lp._file_lock():
        saved = lp._saved_pairs()
        state = _read_state()
        shards = state["shards"]
        tries = state["tries"]
        row = shards.get(shard)
        if not row:
            _write_state(state)
            return result
        items = row.get("items") or {}
        pool = _read_pool()
        in_pool = {lp._pair_from_line(line) for line in pool}
        missed_add: list[str] = []
        for key in items:
            try:
                shop, item = key.split("/", 1)
                pair = (int(shop), int(item))
            except ValueError:
                continue
            if pair in saved:
                result["done"] += 1
                tries.pop(key, None)
                continue
            if int(tries.get(key) or 0) >= MAX_TRIES:
                missed_add.append(lp._url(pair))
                result["missed"] += 1
                tries.pop(key, None)
                continue
            if pair not in in_pool:
                pool.append(lp._url(pair))
                in_pool.add(pair)
                result["retry"] += 1
        shards.pop(shard, None)
        _write_pool(pool)
        _write_state(state)
        if missed_add:
            existing = lp._read_queue_lines(lp.MISSED_PATH)
            have = {lp._pair_from_line(x) for x in existing}
            for url in missed_add:
                if lp._pair_from_line(url) not in have:
                    existing.append(url)
            lp._write_queue_lines(lp.MISSED_PATH, existing)
            lp._invalidate_missed_cache()
    return result


# --------------------------------------------------------------------------- #
# Server list detection / refresh                                            #
# --------------------------------------------------------------------------- #
def clear_pair(pair: tuple[int, int]) -> bool:
    """Drop one pair from every shard lease + try-count (called on save/miss).

    NOTE: acquires ``_file_lock`` itself — never call it while already holding the
    lock (the lock is not reentrant).
    """
    key = lp._pair_key((int(pair[0]), int(pair[1])))
    changed = False
    with lp._file_lock():
        state = _read_state()
        for row in (state.get("shards") or {}).values():
            items = row.get("items") or {}
            if key in items:
                items.pop(key, None)
                row["items"] = items
                changed = True
        # prune now-empty shard rows
        for sh in [s for s, r in (state.get("shards") or {}).items() if not (r.get("items"))]:
            state["shards"].pop(sh, None)
            changed = True
        if state.get("tries", {}).pop(key, None) is not None:
            changed = True
        if changed:
            _write_state(state)
    return changed


def mark_missed(pair: tuple[int, int]) -> bool:
    """Park one pair in MISSED and clear its lease (mode2 per-item miss).

    Acquires ``_file_lock`` itself — do not call while holding the lock.
    """
    pair = (int(pair[0]), int(pair[1]))
    key = lp._pair_key(pair)
    with lp._file_lock():
        state = _read_state()
        for row in (state.get("shards") or {}).values():
            (row.get("items") or {}).pop(key, None)
        for sh in [s for s, r in (state.get("shards") or {}).items() if not (r.get("items"))]:
            state["shards"].pop(sh, None)
        state.get("tries", {}).pop(key, None)
        _write_state(state)
        existing = lp._read_queue_lines(lp.MISSED_PATH)
        if pair not in {lp._pair_from_line(x) for x in existing}:
            existing.append(lp._url(pair))
            lp._write_queue_lines(lp.MISSED_PATH, existing)
            lp._invalidate_missed_cache()
    return True


def pool_remaining() -> int:
    saved = lp._saved_pairs()
    return sum(1 for ln in _read_pool() if lp._pair_from_line(ln) not in saved)


def in_flight() -> int:
    return len(_leased_pairs(_read_state()))


def snapshot() -> dict:
    state = _read_state()
    per_shard = {
        int(shard): len(row.get("items") or {})
        for shard, row in (state.get("shards") or {}).items()
    }
    return {
        "pool_remaining": pool_remaining(),
        "in_flight": len(_leased_pairs(state)),
        "per_shard_lease": per_shard,
    }


def reconcile_with_server(ids: list[tuple[int, int]]) -> dict:
    """Feed the latest server list into the pool.

    * server list ≈ (pool + in-flight)  → same ongoing list: add only genuinely
      new items, drop saved ones.
    * server list clearly larger        → new/added list: rebuild the pool from
      server pending (keep items currently leased so active phones are not
      disrupted).
    """
    saved = lp._saved_pairs()
    pending_set = set(lp.pending_pairs_from_ids(ids))
    server_pending = [pair for pair in ids if pair in pending_set]
    server_n = len(server_pending)
    with lp._file_lock():
        state = _read_state()
        pool = _read_pool()
        _reclaim_expired(state, pool, saved)
        pool = [ln for ln in pool if lp._pair_from_line(ln) not in saved]
        leased = _leased_pairs(state)
        known = {lp._pair_from_line(ln) for ln in pool} | leased
        known.discard(None)
        remaining_known = len(pool) + len(leased)
        is_new = server_n > remaining_known + SERVER_MATCH_TOLERANCE
        order = {pair: i for i, pair in enumerate(server_pending)}
        if is_new:
            # rebuild from server, but keep in-flight leases untouched
            new_pool = [
                lp._url(pair)
                for pair in server_pending
                if pair not in leased
            ]
            _write_pool(new_pool)
            reason = "new_list"
            added = len(new_pool)
        else:
            server_set = set(server_pending)
            add = [pair for pair in server_pending if pair not in known]
            add.sort(key=lambda p: order.get(p, 0))
            # keep only items still on the server list
            pool = [ln for ln in pool if lp._pair_from_line(ln) in server_set]
            pool.extend(lp._url(pair) for pair in add)
            _write_pool(pool)
            reason = "same_list"
            added = len(add)
        _write_state(state)
    return {
        "ok": True,
        "reason": reason,
        "is_new_list": is_new,
        "server_pending": server_n,
        "remaining_known": remaining_known,
        "added": added,
        "pool_remaining": pool_remaining(),
    }


def advance_cycle_if_drained() -> bool:
    """풀이 비고 in-flight 0 이면 다음 재수집 단계를 풀에 주입한다.

    ``claim_next`` 가 빈 lease 를 받은 직후(락 밖에서) 호출한다. 한 번의 OS
    파일락 안에서 drained 판정 → cycle 매니저가 지정한 다음 단계 배치를 풀에
    적재한다. 모든 단계가 끝났으면 사이클을 완료 처리한다.

    Returns ``True`` 이면 새 배치가 적재되어 재시도 lease 가 가능하다.
    """
    try:
        import list_cycle_manager as cyc
    except Exception:
        return False
    with lp._file_lock():
        saved = lp._saved_pairs()
        pool = _read_pool()
        state = _read_state()
        _reclaim_expired(state, pool, saved)
        pool = [ln for ln in pool if lp._pair_from_line(ln) not in saved]
        in_flight = len(_leased_pairs(state))
        if pool or in_flight:
            _write_pool(pool)
            _write_state(state)
            return False
        nxt = cyc.next_retry_batch()
        if not nxt:
            _write_state(state)
            cyc.mark_cycle_complete()
            return False
        phase, pairs = nxt
        new_pool = [lp._url(p) for p in pairs if p not in saved]
        _write_pool(new_pool)
        _write_state(state)
    cyc.mark_phase_started(phase)
    return bool(new_pool)


def reset_pool(ids: list[tuple[int, int]] | None = None) -> dict:
    """Force the pool to the current server pending and clear all leases."""
    import task_lists

    if ids is None:
        source = task_lists.selected_path()
        ids = lp._item_ids(source) if source is not None else []
    pending = lp.pending_pairs_from_ids(ids if ids is not None else [])
    with lp._file_lock():
        _write_pool([lp._url(pair) for pair in pending])
        _write_state({"shards": {}, "tries": {}})
    return {"ok": True, "pool_remaining": len(pending)}
