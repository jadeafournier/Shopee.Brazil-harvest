# -*- coding: utf-8 -*-
"""목록 사이클 1단계: 영구 상품없음·임시 미저장/없음·건수 대조.

수집 엔진 분류(2단계)와 wind-down(2단계) 전까지 파일·상태·incoming 훅·UI 집계를 담당한다.

새 서버 목록 검출 시에는 임시 미저장/없음 목록을 새 목록에 포함된 pair 만 남기도록
갱신(교집합)하여 반복수집을 피한다. (예전의 '미확정 목록 보관' 공정은 폐지됨.)
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import list_progress as lp

HERE = Path(__file__).resolve().parent
PERMANENT_PATH = HERE / "list_gone_permanent.txt"
TEMP_MISSED_PATH = HERE / "list_missed_temp.txt"
TEMP_GONE_PATH = HERE / "list_gone_temp.txt"
STATE_PATH = HERE / "list_cycle_state.json"

PHASE_IDLE = "idle"
PHASE_PRIMARY = "primary"
PHASE_RETRY_MISSED = "retry_missed"
PHASE_RETRY_GONE = "retry_gone"

_ACCOUNT_TOLERANCE = 0

_PERM_CACHE: dict = {"at": 0.0, "pairs": set()}
_TEMP_MISS_CACHE: dict = {"at": 0.0, "pairs": set()}
_TEMP_GONE_CACHE: dict = {"at": 0.0, "pairs": set()}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _invalidate_pair_caches() -> None:
    _PERM_CACHE["at"] = 0.0
    _TEMP_MISS_CACHE["at"] = 0.0
    _TEMP_GONE_CACHE["at"] = 0.0


def _pairs_from_path(path: Path, cache: dict, ttl: float = 2.0) -> set[tuple[int, int]]:
    now = time.time()
    if now - float(cache["at"]) < ttl:
        return set(cache["pairs"])
    pairs: set[tuple[int, int]] = set()
    for line in lp._read_queue_lines(path):
        pair = lp._pair_from_line(line)
        if pair is not None:
            pairs.add(pair)
    cache["at"] = now
    cache["pairs"] = pairs
    return pairs


def permanent_pairs() -> set[tuple[int, int]]:
    return _pairs_from_path(PERMANENT_PATH, _PERM_CACHE)


def temp_missed_pairs() -> set[tuple[int, int]]:
    return _pairs_from_path(TEMP_MISSED_PATH, _TEMP_MISS_CACHE)


def temp_gone_pairs() -> set[tuple[int, int]]:
    return _pairs_from_path(TEMP_GONE_PATH, _TEMP_GONE_CACHE)


def _read_state() -> dict:
    if not STATE_PATH.is_file():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(data: dict) -> None:
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


def cycle_phase() -> str:
    phase = str(_read_state().get("phase") or PHASE_IDLE)
    if phase not in (PHASE_IDLE, PHASE_PRIMARY, PHASE_RETRY_MISSED, PHASE_RETRY_GONE):
        return PHASE_IDLE
    return phase


def _filter_temps_to_server(server_pairs: set[tuple[int, int]]) -> dict:
    """새 서버 목록 검출 시: 임시 미저장/없음에서 새 목록에 포함된 pair 만 남기고
    나머지는 제거한다. (예전의 '미확정 보관' 공정을 대체 — 반복수집 방지.)

    새 목록에 그대로 들어있는 미저장/없음 pair 는 보존되므로, 다음 수집 사이클의
    재시도 단계에서 이어서 재수집된다. 새 목록에서 사라진 pair 는 더 추적하지 않는다.
    영구 상품없음 목록은 전역 자산이라 파일 자체는 건드리지 않고, 새 목록 기준 집계
    (perm_on_server)만 갱신하여 배제시킨다(on_server_list_changed 에서 처리).
    """
    result = {
        "kept_missed": 0,
        "kept_gone": 0,
        "removed_missed": 0,
        "removed_gone": 0,
    }
    with lp._file_lock():
        for key, path in (("missed", TEMP_MISSED_PATH), ("gone", TEMP_GONE_PATH)):
            rows = lp._read_queue_lines(path)
            kept = [ln for ln in rows if lp._pair_from_line(ln) in server_pairs]
            if len(kept) != len(rows):
                lp._write_queue_lines(path, kept)
            result["kept_" + key] = len(kept)
            result["removed_" + key] = len(rows) - len(kept)
    _invalidate_pair_caches()
    return result


def stop_all_collectors() -> list[str]:
    """목록 교체 시 즉시 중단 — 모든 수집 shard 프로세스 종료."""
    stopped: list[str] = []
    try:
        import harvest_control

        for phone in harvest_control._phones():
            name = str(phone.get("name") or "")
            shard = int(phone.get("shard") or 0)
            if harvest_control._shard_running(shard):
                harvest_control.stop(name)
                stopped.append(name)
    except Exception:
        pass
    return stopped


def _rebuild_pool_after_list_replace(ids: list[tuple[int, int]]) -> None:
    try:
        import collect_layout

        if collect_layout.list_mode() != "mode2":
            return
        import list_pool_manager

        list_pool_manager.reset_pool(ids)
    except Exception:
        pass


def on_server_list_changed(
    old_pair_keys: set[str],
    new_pair_keys: set[str],
    received_at: str,
    server_ids: list[tuple[int, int]],
) -> dict:
    """tasks.json 이 바뀐 직후 (save_incoming)."""
    state = _read_state()
    phase = cycle_phase()
    new_n = len(new_pair_keys)
    list_replaced = old_pair_keys != new_pair_keys
    stopped: list[str] = []
    temp_filter = {"kept_missed": 0, "kept_gone": 0, "removed_missed": 0, "removed_gone": 0}

    if list_replaced:
        # 목록이 바뀌면 즉시 중단하고, 임시 목록을 새 목록에 맞게 갱신한다.
        stopped = stop_all_collectors()
        temp_filter = _filter_temps_to_server(set(server_ids))
        # 새 수집 사이클 — 단계·재시도 플래그 초기화.
        phase = PHASE_IDLE
        state["retry_missed_done"] = False
        state["retry_gone_done"] = False

    perm = permanent_pairs()
    perm_on_server = sum(1 for p in server_ids if p in perm)

    state.update(
        {
            "phase": phase,
            "server_pair_keys": sorted(new_pair_keys),
            "server_count": new_n,
            "perm_on_server": perm_on_server,
            "perm_global": len(perm),
            "updated_at": received_at,
            "last_list_change_at": _now_iso() if list_replaced else state.get("last_list_change_at"),
            "last_stopped_phones": stopped,
            "last_temp_filter": temp_filter,
        }
    )
    _write_state(state)

    if list_replaced:
        _rebuild_pool_after_list_replace(server_ids)

    return {
        "ok": True,
        "list_replaced": list_replaced,
        "stopped": stopped,
        "temp_filter": temp_filter,
        "perm_on_server": perm_on_server,
        "phase": phase,
    }


def note_collection_started() -> dict:
    """harvest 시작 시 primary 사이클 스냅샷 (재시도 단계 플래그 초기화).

    wind-down(재수집 단계 전환)은 mode2 전역 풀에서만 동작하므로, mode1 에서는
    phase 를 올리지 않는다(영구 primary 고착 방지).
    """
    try:
        import collect_layout

        if collect_layout.list_mode() != "mode2":
            return {"ok": True, "skipped": "mode1"}
    except Exception:
        pass
    import task_lists

    source = task_lists.selected_path()
    if source is None:
        return {"ok": False, "error": "no list"}
    ids = lp._item_ids(source)
    keys = sorted(f"{s}/{i}" for s, i in ids)
    state = _read_state()
    if cycle_phase() == PHASE_IDLE:
        state["phase"] = PHASE_PRIMARY
        state["cycle_started_at"] = _now_iso()
        state["retry_missed_done"] = False
        state["retry_gone_done"] = False
    state["server_pair_keys"] = keys
    state["server_count"] = len(ids)
    _write_state(state)
    return {"ok": True, "phase": cycle_phase(), "server_count": len(ids)}


def pairs_excluded_from_primary(server_ids: list[tuple[int, int]] | None = None) -> set[tuple[int, int]]:
    """pending/reconcile 에서 빼는 pair — phase 에 따라 달라진다.

    * primary / idle : 영구 + 임시 미저장 + 임시 없음 (아직 재수집 안 함)
    * retry_missed   : 영구 + 임시 없음 (미저장은 지금 재수집 대상)
    * retry_gone     : 영구만 (없음은 지금 재수집 대상)
    """
    skip = set(permanent_pairs())
    phase = cycle_phase()
    if phase == PHASE_RETRY_MISSED:
        skip |= temp_gone_pairs()
    elif phase == PHASE_RETRY_GONE:
        pass
    else:
        skip |= temp_missed_pairs() | temp_gone_pairs()
    if server_ids is not None:
        on_server = set(server_ids)
        skip = {p for p in skip if p in on_server}
    return skip


# --------------------------------------------------------------------------- #
# Temp set editing (저장/미저장/없음 결과 반영)                               #
# --------------------------------------------------------------------------- #
def _remove_pair(path: Path, pair: tuple[int, int]) -> bool:
    rows = lp._read_queue_lines(path)
    kept = [ln for ln in rows if lp._pair_from_line(ln) != pair]
    if len(kept) != len(rows):
        lp._write_queue_lines(path, kept)
        return True
    return False


def _add_pair(path: Path, pair: tuple[int, int]) -> bool:
    rows = lp._read_queue_lines(path)
    if pair in {lp._pair_from_line(x) for x in rows}:
        return False
    rows.append(lp._url(pair))
    lp._write_queue_lines(path, rows)
    return True


def record_capture_result(pair: tuple[int, int], outcome: str) -> None:
    """엔진 한 건 결과를 임시 집합에 반영한다 (phase 인지).

    outcome: ``saved`` | ``missed`` | ``gone``
    """
    pair = (int(pair[0]), int(pair[1]))
    phase = cycle_phase()
    with lp._file_lock():
        if outcome == "saved":
            _remove_pair(TEMP_MISSED_PATH, pair)
            _remove_pair(TEMP_GONE_PATH, pair)
        elif outcome == "gone":
            if phase == PHASE_RETRY_GONE:
                # 재확인에서도 없음 → 최종 확정, 영구로 승격
                _remove_pair(TEMP_GONE_PATH, pair)
                _remove_pair(TEMP_MISSED_PATH, pair)
                _add_pair(PERMANENT_PATH, pair)
            else:
                _remove_pair(TEMP_MISSED_PATH, pair)
                _add_pair(TEMP_GONE_PATH, pair)
        else:  # missed
            if phase == PHASE_RETRY_GONE:
                # 없음 재수집인데 이번엔 미저장 → 불확실, 미저장으로 되돌림
                _remove_pair(TEMP_GONE_PATH, pair)
                _add_pair(TEMP_MISSED_PATH, pair)
            else:
                _add_pair(TEMP_MISSED_PATH, pair)
    _invalidate_pair_caches()


# --------------------------------------------------------------------------- #
# Wind-down phase 전환 (풀이 비고 in-flight 0 일 때 list_pool_manager 가 호출)   #
# --------------------------------------------------------------------------- #
def next_retry_batch() -> tuple[str, list[tuple[int, int]]] | None:
    """다음 재수집 단계와 그 대상. 없으면 None (사이클 완료)."""
    state = _read_state()
    if not bool(state.get("retry_missed_done")):
        miss = temp_missed_pairs()
        if miss:
            return (PHASE_RETRY_MISSED, sorted(miss))
    if not bool(state.get("retry_gone_done")):
        gone = temp_gone_pairs()
        if gone:
            return (PHASE_RETRY_GONE, sorted(gone))
    return None


def mark_phase_started(phase: str) -> None:
    state = _read_state()
    state["phase"] = phase
    if phase == PHASE_RETRY_MISSED:
        state["retry_missed_done"] = True
    elif phase == PHASE_RETRY_GONE:
        state["retry_missed_done"] = True
        state["retry_gone_done"] = True
    state["phase_started_at"] = _now_iso()
    _write_state(state)


def mark_cycle_complete() -> dict:
    """모든 단계 종료 → idle. 남은 임시 미저장은 다음 목록까지 보존."""
    state = _read_state()
    was = cycle_phase()
    state["phase"] = PHASE_IDLE
    state["retry_missed_done"] = False
    state["retry_gone_done"] = False
    state["cycle_completed_at"] = _now_iso()
    _write_state(state)
    return {"ok": True, "was": was}


def add_permanent_pair(pair: tuple[int, int]) -> bool:
    pair = (int(pair[0]), int(pair[1]))
    with lp._file_lock():
        rows = lp._read_queue_lines(PERMANENT_PATH)
        have = {lp._pair_from_line(x) for x in rows}
        if pair in have:
            return False
        rows.append(lp._url(pair))
        lp._write_queue_lines(PERMANENT_PATH, rows)
    _invalidate_pair_caches()
    return True


def record_temp_missed(pair: tuple[int, int]) -> bool:
    """2단계: 엔진 미저장 → 임시 파일 (1단계 API)."""
    pair = (int(pair[0]), int(pair[1]))
    with lp._file_lock():
        rows = lp._read_queue_lines(TEMP_MISSED_PATH)
        have = {lp._pair_from_line(x) for x in rows}
        if pair in have:
            return False
        rows.append(lp._url(pair))
        lp._write_queue_lines(TEMP_MISSED_PATH, rows)
    _invalidate_pair_caches()
    return True


def record_temp_gone(pair: tuple[int, int]) -> bool:
    pair = (int(pair[0]), int(pair[1]))
    with lp._file_lock():
        rows = lp._read_queue_lines(TEMP_GONE_PATH)
        have = {lp._pair_from_line(x) for x in rows}
        if pair in have:
            return False
        rows.append(lp._url(pair))
        lp._write_queue_lines(TEMP_GONE_PATH, rows)
    _invalidate_pair_caches()
    return True


def promote_gone_to_permanent(pair: tuple[int, int]) -> bool:
    """최종 상품없음 확인 후 영구 목록 (2·3단계)."""
    pair = (int(pair[0]), int(pair[1]))
    with lp._file_lock():
        gone = lp._read_queue_lines(TEMP_GONE_PATH)
        gone = [ln for ln in gone if lp._pair_from_line(ln) != pair]
        lp._write_queue_lines(TEMP_GONE_PATH, gone)
        perm = lp._read_queue_lines(PERMANENT_PATH)
        have = {lp._pair_from_line(x) for x in perm}
        if pair not in have:
            perm.append(lp._url(pair))
            lp._write_queue_lines(PERMANENT_PATH, perm)
    _invalidate_pair_caches()
    return True


def _sent_keys() -> set[str]:
    """멤버 허브에 올린(등록된) 파일 키 집합: ``{shop}_{item}.json``."""
    try:
        from _sever_bridge import _load_sent

        return set(_load_sent().keys())
    except Exception:
        return set()


def accounting(server_ids: list[tuple[int, int]]) -> dict:
    """영구(서버상) + 임시미저장 + 임시없음 + 작업잔량 + 저장 ≈ 서버 건수."""
    saved = lp._saved_pairs()
    perm = permanent_pairs()
    t_miss = temp_missed_pairs()
    t_gone = temp_gone_pairs()
    sent = _sent_keys()
    server_n = len(server_ids)
    on_server = set(server_ids)

    perm_on = sum(1 for p in server_ids if p in perm)
    temp_miss_on = sum(1 for p in server_ids if p in t_miss)
    temp_gone_on = sum(1 for p in server_ids if p in t_gone)
    saved_on = sum(1 for p in server_ids if p in saved)
    # 이번 서버 목록 기준: 올린 수 / 저장됐지만 아직 안 올린 수
    uploaded_on = sum(1 for p in server_ids if f"{p[0]}_{p[1]}.json" in sent)
    saved_not_uploaded = sum(
        1 for p in server_ids if p in saved and f"{p[0]}_{p[1]}.json" not in sent
    )

    work = 0
    for p in server_ids:
        if p in saved or p in perm or p in t_miss or p in t_gone:
            continue
        if p in lp.missed_pairs():
            continue
        work += 1

    parts_sum = perm_on + temp_miss_on + temp_gone_on + work + saved_on
    delta = server_n - parts_sum
    ok = abs(delta) <= _ACCOUNT_TOLERANCE

    pool_left = 0
    in_flight = 0
    try:
        import list_pool_manager

        snap = list_pool_manager.snapshot()
        pool_left = int(snap.get("pool_remaining") or 0)
        in_flight = int(snap.get("in_flight") or 0)
    except Exception:
        pass

    return {
        "server_count": server_n,
        "perm_on_server": perm_on,
        "perm_global": len(perm),
        "temp_missed_on_server": temp_miss_on,
        "temp_gone_on_server": temp_gone_on,
        "saved_on_server": saved_on,
        "uploaded_on_server": uploaded_on,
        "saved_not_uploaded": saved_not_uploaded,
        "work_primary_on_server": work,
        "parts_sum": parts_sum,
        "accounting_delta": delta,
        "accounting_ok": ok,
        "cycle_phase": cycle_phase(),
        "pool_remaining": pool_left,
        "pool_in_flight": in_flight,
    }


def workload_extras(server_ids: list[tuple[int, int]]) -> dict:
    acc = accounting(server_ids)
    return {
        "list_server_count": acc["server_count"],
        "list_perm_on_server": acc["perm_on_server"],
        "list_perm_global": acc["perm_global"],
        "list_temp_missed": acc["temp_missed_on_server"],
        "list_temp_gone": acc["temp_gone_on_server"],
        "list_work_primary": acc["work_primary_on_server"],
        "list_saved_not_uploaded": acc["saved_not_uploaded"],
        "list_uploaded_on_list": acc["uploaded_on_server"],
        "list_accounting_ok": acc["accounting_ok"],
        "list_accounting_delta": acc["accounting_delta"],
        "cycle_phase": acc["cycle_phase"],
    }
