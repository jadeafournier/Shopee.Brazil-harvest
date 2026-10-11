# -*- coding: utf-8 -*-
"""세션 창조·수집 이용 기록.

Shopee.Brazil/Sessions/_ledger.json 에 남긴다.
세션 파일(숫자.tgz)과는 이름이 겹치지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path(__file__).resolve().parent
SESSION_DIR = ROOT / "Shopee.Brazil" / "Sessions"
LEDGER = SESSION_DIR / "_ledger.json"
PHONES = RUNTIME / "phones.json"
REGION_LOG = RUNTIME / "_run_br_three.log"
# VMOS display names changed; session files may still use old labels.
_PHONE_NAME_ALIASES: dict[str, set[str]] = {
    "Phone1": {"Samsung1", "Phone1"},
    "Phone2": {"Samsung2", "Phone2"},
    "Phone3": {"Phone3", "Samsung3"},
}


def _phones() -> list[dict]:
    if not PHONES.is_file():
        return []
    try:
        rows = json.loads(PHONES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows.sort(key=lambda row: int(row.get("shard") or 0))
    return rows


def _pad_for(name: str = "", serial: str = "") -> str:
    for row in _phones():
        if name and row.get("name") == name:
            return str(row.get("pad") or "")
        if serial and row.get("serial") == serial:
            return str(row.get("pad") or "")
    return ""


def _empty() -> dict:
    return {"backfilled": False, "rounds": [], "created": {}, "assigned": {}, "uses": []}


def _load() -> dict:
    if not LEDGER.is_file():
        return _empty()
    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    data.setdefault("rounds", [])
    data.setdefault("created", {})
    data.setdefault("assigned", {})
    data.setdefault("uses", [])
    return data


def _write(data: dict) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, LEDGER)


def _locked(fn):
    lock = LEDGER.with_suffix(".lock")
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    fd = None
    for _ in range(40):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            time.sleep(0.05)
    if fd is None:
        try:
            lock.unlink()
        except OSError:
            pass
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_RDWR)
    try:
        return fn()
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def begin_round(phones: list[dict], task_ids: list[int] | None = None) -> None:
    """Change Region 한 번을 연다. 이후 그 패드에서 만든 세션이 여기 붙는다."""

    def run() -> None:
        data = _load()
        _backfill(data)
        ids = [int(x) for x in (task_ids or [])]
        if data["rounds"] and ids and data["rounds"][-1].get("task_ids") == ids:
            _write(data)
            return
        data["rounds"].append({
            "id": str(int(time.time())),
            "started_at": time.time(),
            "task_ids": ids,
            "phones": [
                {
                    "name": p.get("name") or "",
                    "pad": p.get("pad") or "",
                    "serial": p.get("serial") or "",
                    "shard": int(p.get("shard") or 0),
                    "sessions": [],
                }
                for p in phones
            ],
        })
        _write(data)

    _locked(run)


def note_created(
    userid: str,
    phone: str,
    serial: str = "",
    pad: str = "",
    username: str = "",
) -> None:
    """방금 보관한 세션을 현재 Change Region 의 패드에 붙인다."""
    userid = str(userid or "").strip()
    if not userid:
        return
    pad = pad or os.environ.get("VMOS_PAD_CODE") or _pad_for(phone, serial)
    phone = phone or os.environ.get("SHOPEE_PAD_NAME") or ""

    def run() -> None:
        data = _load()
        _backfill(data)
        if userid in data["created"]:
            info = data["created"][userid]
            if phone and not info.get("phone"):
                info["phone"] = phone
                info["pad"] = pad or info.get("pad") or ""
                info["serial"] = serial or info.get("serial") or ""
                _write(data)
            return
        now = time.time()
        data["created"][userid] = {
            "at": now,
            "phone": phone,
            "pad": pad,
            "serial": serial,
        }
        target = None
        for rnd in reversed(data["rounds"]):
            for item in rnd.get("phones") or []:
                if pad and item.get("pad") == pad:
                    target = item
                    break
                if phone and item.get("name") == phone:
                    target = item
                    break
            if target:
                break
        if target is None:
            if not data["rounds"]:
                data["rounds"].append({
                    "id": "manual",
                    "started_at": now,
                    "task_ids": [],
                    "phones": [],
                })
            rnd = data["rounds"][-1]
            target = {
                "name": phone,
                "pad": pad,
                "serial": serial,
                "shard": 0,
                "sessions": [],
            }
            rnd.setdefault("phones", []).append(target)
        if userid not in target["sessions"]:
            target["sessions"].append(userid)
        _write(data)

    _locked(run)


def note_assigned(phone: str, pad: str, shard: int, userids: list[str]) -> None:
    def run() -> None:
        data = _load()
        data["assigned"][str(shard)] = {
            "phone": phone,
            "pad": pad,
            "shard": int(shard),
            "userids": [str(u) for u in userids],
            "at": time.time(),
        }
        _write(data)

    _locked(run)


def _meta_saved_at(uid: str) -> float:
    meta = SESSION_DIR / f"{uid}.json"
    if meta.is_file():
        try:
            return float(json.loads(meta.read_text(encoding="utf-8")).get("saved_at") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    path = SESSION_DIR / f"{uid}.tgz"
    if path.is_file():
        return path.stat().st_mtime
    return 0.0


def _phone_name_keys(phone: str) -> set[str]:
    phone = str(phone or "").strip()
    if not phone:
        return set()
    keys = set(_PHONE_NAME_ALIASES.get(phone, {phone}))
    keys.add(phone)
    return keys


def _session_owner_match(info: dict, phone: str, pad: str, name_keys: set[str]) -> bool:
    owner = str(info.get("phone") or "").strip()
    if owner and owner in name_keys:
        return True
    opad = str(info.get("pad") or "").strip()
    return bool(pad and opad and opad == pad)


def _userids_for_phone_data(data: dict, phone: str) -> list[str]:
    phone = str(phone or "").strip()
    if not phone:
        return []
    pad = _pad_for(phone)
    name_keys = _phone_name_keys(phone)
    seen: set[str] = set()
    out: list[str] = []
    for uid, info in (data.get("created") or {}).items():
        if not _session_owner_match(info, phone, pad, name_keys):
            continue
        path = SESSION_DIR / f"{uid}.tgz"
        if not path.is_file() or path.stat().st_size < 10_000:
            continue
        seen.add(str(uid))
        out.append(str(uid))
    for meta in SESSION_DIR.glob("*.json"):
        if meta.name.startswith("_"):
            continue
        uid = meta.stem
        if not uid.isdigit() or uid in seen:
            continue
        try:
            row = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not _session_owner_match(row, phone, pad, name_keys):
            continue
        path = SESSION_DIR / f"{uid}.tgz"
        if not path.is_file() or path.stat().st_size < 10_000:
            continue
        seen.add(uid)
        out.append(uid)
    out.sort(key=lambda u: _meta_saved_at(u), reverse=True)
    return out


def userids_for_phone(phone: str) -> list[str]:
    """PC에 있는 세션 중 이 폰에서 만든 userid 목록 (최신순). 파일은 삭제하지 않는다."""

    def run() -> list[str]:
        return _userids_for_phone_data(_load(), phone)

    return _locked(run) or []


def phone_assigned_ids(data: dict, name: str, shard: int, ids: list[str]) -> list[str]:
    """Same pool as Sessions tab roster (session_ledger.view)."""
    del ids  # kept for callers; legacy shard::3 rotation is no longer used
    name = str(name or "").strip()
    phone_allow = _userids_for_phone_data(data, name) if name else []
    assigned = (data.get("assigned") or {}).get(str(int(shard))) or {}
    if phone_allow:
        return list(phone_allow)
    ledger = assigned.get("userids") or []
    return [str(uid) for uid in ledger if str(uid).strip()]


def harvest_pool_userids(phone: str, shard: int) -> list[str]:
    """Harvest/UI session pool: this phone's sessions or explicit ledger assign only."""

    def run() -> list[str]:
        data = _load()
        return phone_assigned_ids(data, str(phone or "").strip(), int(shard), session_ids())

    return _locked(run) or []


def _session_counts_from_data(data: dict, name: str, shard: int, ids: list[str]) -> dict[str, int]:
    uses = data.get("uses") or []
    open_by_user: set[str] = set()
    for use in uses:
        if use.get("ended_at"):
            continue
        uid = str(use.get("userid") or "").strip()
        if uid:
            open_by_user.add(uid)
    assigned_ids = phone_assigned_ids(data, name, shard, ids)
    total = len(assigned_ids)
    today = _local_day(time.time())
    available = 0
    for uid in assigned_ids:
        if uid in open_by_user:
            continue
        used = False
        for use in uses:
            if str(use.get("userid") or "") != uid:
                continue
            if name and str(use.get("phone") or "") != name:
                continue
            ts = use.get("started_at") or use.get("ended_at")
            if ts and _local_day(float(ts)) == today:
                used = True
                break
        if used:
            continue
        available += 1
    return {"total": total, "available": available}


def phone_session_counts(phone: str, shard: int | None = None) -> dict[str, int]:
    """Harvest pool: available (not open, not used today) / total assigned on disk."""
    name = str(phone or "").strip()
    if not name:
        return {"total": 0, "available": 0}

    def run() -> dict[str, int]:
        data = _load()
        ids = session_ids()
        return _session_counts_from_data(data, name, int(shard or 0), ids)

    return _locked(run) or {"total": 0, "available": 0}


def enrich_phone_session_counts(phones: list[dict]) -> None:
    """Set sessions_total / sessions_available on each phone row (one ledger lock)."""

    def run() -> dict[str, dict[str, int]]:
        data = _load()
        ids = session_ids()
        out: dict[str, dict[str, int]] = {}
        for row in phones:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            shard = int(row.get("shard") or 0)
            out[name] = _session_counts_from_data(data, name, shard, ids)
        return out

    counts = _locked(run) or {}
    for row in phones:
        name = str(row.get("name") or "").strip()
        block = counts.get(name) or {"total": 0, "available": 0}
        row["sessions_total"] = int(block.get("total") or 0)
        row["sessions_available"] = int(block.get("available") or 0)


def _local_day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def used_today(userid: str, phone: str = "") -> bool:
    """이 폰에서 해당 userid 세션을 오늘(로컬 날짜) 이미 썼는지."""
    uid = str(userid or "").strip()
    name = str(phone or "").strip()
    if not uid:
        return False
    today = _local_day(time.time())

    def run() -> bool:
        data = _load()
        for use in data["uses"]:
            if str(use.get("userid") or "") != uid:
                continue
            if name and str(use.get("phone") or "") != name:
                continue
            ts = use.get("started_at") or use.get("ended_at")
            if ts and _local_day(float(ts)) == today:
                return True
        return False

    return bool(_locked(run))


def start_use(userid: str, reason: str = "") -> None:
    userid = str(userid or "").strip()
    if not userid:
        return
    phone = os.environ.get("SHOPEE_PAD_NAME") or ""
    pad = os.environ.get("VMOS_PAD_CODE") or ""
    serial = os.environ.get("SHOPEE_SERIAL") or ""

    def run() -> None:
        data = _load()
        now = time.time()
        for use in data["uses"]:
            if use.get("ended_at"):
                continue
            if phone and use.get("phone") == phone:
                use["ended_at"] = now
                use["reason"] = use.get("reason") or "교체"
            elif use.get("userid") == userid:
                use["ended_at"] = now
                use["reason"] = use.get("reason") or "다시 시작"
        data["uses"].append({
            "userid": userid,
            "phone": phone,
            "pad": pad,
            "serial": serial,
            "started_at": now,
            "ended_at": None,
            "saved": 0,
            "reason": reason or "이용중",
        })
        _write(data)

    _locked(run)


def add_saved(n: int = 1) -> None:
    phone = os.environ.get("SHOPEE_PAD_NAME") or ""

    def run() -> None:
        data = _load()
        for use in reversed(data["uses"]):
            if use.get("ended_at"):
                continue
            if phone and use.get("phone") != phone:
                continue
            use["saved"] = int(use.get("saved") or 0) + int(n)
            _write(data)
            return

    _locked(run)


def end_use(userid: str, reason: str) -> None:
    userid = str(userid or "").strip()
    phone = os.environ.get("SHOPEE_PAD_NAME") or ""

    def run() -> None:
        data = _load()
        now = time.time()
        for use in reversed(data["uses"]):
            if use.get("ended_at"):
                continue
            if userid and use.get("userid") != userid:
                continue
            if phone and use.get("phone") != phone:
                continue
            use["ended_at"] = now
            use["reason"] = reason or "종료"
            _write(data)
            return

    _locked(run)


def _backfill(data: dict) -> None:
    if data.get("backfilled"):
        return
    phones = _phones()
    by_pad = {str(p.get("pad")): p for p in phones}
    by_name = {str(p.get("name")): p for p in phones}
    rounds: list[dict] = []
    if REGION_LOG.is_file():
        text = REGION_LOG.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if "replacePad" not in line or '"taskId"' not in line:
                continue
            raw = line.split("replacePad", 1)[-1].strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts = float(payload.get("ts") or 0) / 1000.0
            items = []
            for row in payload.get("data") or []:
                pad = str(row.get("padCode") or "")
                phone = by_pad.get(pad) or {}
                items.append({
                    "name": phone.get("name") or pad,
                    "pad": pad,
                    "serial": phone.get("serial") or "",
                    "shard": int(phone.get("shard") or 0),
                    "sessions": [],
                })
            if items:
                rounds.append({
                    "id": str(int(ts or time.time())),
                    "started_at": ts or time.time(),
                    "task_ids": [int(r["taskId"]) for r in (payload.get("data") or []) if r.get("taskId")],
                    "phones": items,
                })
    made: dict[str, tuple[str, float]] = {}
    for path in RUNTIME.glob("session_store_*.log"):
        phone = ""
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"폰\s+(\S+)\s+기기\s+(\S+)", line)
            if m:
                phone = m.group(1)
                continue
            m = re.search(r"보관 완료 userid=(\d+)", line)
            if m and phone and m.group(1) not in made:
                made[m.group(1)] = (phone, 0.0)
                continue
            m = re.search(r"로그인 확인 userid=(\d+)", line)
            if m and phone and m.group(1) not in made:
                made[m.group(1)] = (phone, 0.0)
    if SESSION_DIR.is_dir():
        for meta in SESSION_DIR.glob("*.json"):
            if not meta.stem.isdigit():
                continue
            try:
                row = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            uid = str(row.get("userid") or meta.stem)
            saved_at = float(row.get("saved_at") or 0)
            owner, _ = made.get(uid, ("", 0.0))
            phone_row = by_name.get(owner) or {}
            pad = str(phone_row.get("pad") or "")
            data["created"].setdefault(uid, {
                "at": saved_at,
                "phone": owner,
                "pad": pad,
                "serial": "",
            })
            chosen = None
            for rnd in rounds:
                if rnd["started_at"] and saved_at and rnd["started_at"] <= saved_at:
                    chosen = rnd
            if chosen is None:
                if not rounds or rounds[0].get("id") != "before":
                    rounds.insert(0, {
                        "id": "before",
                        "started_at": 0,
                        "task_ids": [],
                        "phones": [
                            {
                                "name": p.get("name") or "",
                                "pad": p.get("pad") or "",
                                "serial": "",
                                "shard": int(p.get("shard") or 0),
                                "sessions": [],
                            }
                            for p in phones
                        ],
                    })
                chosen = rounds[0]
            slot = None
            for item in chosen["phones"]:
                if pad and item.get("pad") == pad:
                    slot = item
                    break
                if owner and item.get("name") == owner:
                    slot = item
                    break
            if slot and uid not in slot["sessions"]:
                slot["sessions"].append(uid)
    if rounds and not data["rounds"]:
        data["rounds"] = rounds
    data["backfilled"] = True


def _saved_at(uid: str) -> float | None:
    path = SESSION_DIR / f"{uid}.json"
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        stamp = float(row.get("saved_at") or 0)
        if stamp:
            return stamp
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    tgz = SESSION_DIR / f"{uid}.tgz"
    try:
        return tgz.stat().st_mtime
    except OSError:
        return None


def _collecting(shard: int) -> bool:
    path = RUNTIME / f"list_{int(shard)}.log"
    try:
        return (time.time() - path.stat().st_mtime) < 25
    except OSError:
        return False


def session_ids() -> list[str]:
    if not SESSION_DIR.is_dir():
        return []
    return sorted(p.stem for p in SESSION_DIR.glob("*.tgz") if p.stem.isdigit())


def view() -> dict:
    def run() -> dict:
        data = _load()
        changed = not data.get("backfilled")
        _backfill(data)
        if changed:
            _write(data)
        return data

    data = _locked(run)
    ids = session_ids()
    idset = set(ids)
    uses = data.get("uses") or []
    open_by_user: dict[str, dict] = {}
    open_by_phone: dict[str, dict] = {}
    history: dict[str, list] = {}
    for use in uses:
        uid = str(use.get("userid") or "")
        history.setdefault(uid, []).append(use)
        if not use.get("ended_at"):
            open_by_user[uid] = use
            if use.get("phone"):
                open_by_phone[str(use["phone"])] = use
    phones = []
    for row in _phones():
        shard = int(row.get("shard") or 0)
        name = str(row.get("name") or "")
        assigned = (data.get("assigned") or {}).get(str(shard)) or {}
        current = open_by_phone.get(name)
        assigned_ids = phone_assigned_ids(data, name, shard, ids)
        collecting = _collecting(shard)
        roster = []
        for uid in assigned_ids:
            recs = history.get(uid) or []
            last = recs[-1] if recs else None
            open_use = uid in open_by_user
            saved = (
                int(open_by_user[uid].get("saved") or 0)
                if open_use
                else sum(int(u.get("saved") or 0) for u in recs)
            )
            if open_use and collecting:
                lamp = "live"
            elif not last or open_use or saved >= 300:
                lamp = "blue"
            else:
                lamp = "yellow"
            roster.append({
                "userid": uid,
                "used": bool(recs),
                "open": open_use,
                "lamp": lamp,
                "saved_at": _saved_at(uid),
                "last_at": None if not last else (last.get("ended_at") or last.get("started_at")),
                "saved": saved,
                "reason": "" if not last else str(last.get("reason") or ""),
            })
        phones.append({
            "name": row.get("name") or "",
            "pad": row.get("pad") or "",
            "shard": shard,
            "serial": row.get("serial") or "",
            "collecting": collecting,
            "assigned": assigned_ids,
            "assigned_at": assigned.get("at"),
            "roster": roster,
            "current": (current or {}).get("userid") or "",
            "current_saved": (current or {}).get("saved") or 0,
            "current_since": (current or {}).get("started_at"),
        })
    while len(phones) < 10:
        phones.append({
            "name": "미정폰",
            "unset": True,
            "assigned": [],
            "roster": [],
            "collecting": False,
            "current": "",
            "current_saved": 0,
            "current_since": None,
        })
    rows = []
    for uid in ids:
        info = (data.get("created") or {}).get(uid) or {}
        recs = history.get(uid) or []
        last = recs[-1] if recs else None
        if uid in open_by_user:
            state = "이용중"
        elif recs:
            state = "이용 끝"
        else:
            state = "신선"
        rows.append({
            "userid": uid,
            "phone": info.get("phone") or "",
            "pad": info.get("pad") or "",
            "created_at": info.get("at"),
            "state": state,
            "fresh": not recs,
            "on_disk": uid in idset,
            "uses": [
                {
                    "phone": u.get("phone") or "",
                    "pad": u.get("pad") or "",
                    "started_at": u.get("started_at"),
                    "ended_at": u.get("ended_at"),
                    "saved": int(u.get("saved") or 0),
                    "reason": u.get("reason") or "",
                }
                for u in recs[-8:]
            ],
            "saved_total": sum(int(u.get("saved") or 0) for u in recs),
            "ended_at": None if state == "이용중" else ((last or {}).get("ended_at")),
        })
    rows.sort(key=lambda row: float(row.get("created_at") or 0), reverse=True)
    in_use = len(open_by_user)
    fresh = sum(1 for uid in ids if uid not in history)
    rounds = []
    for rnd in reversed(data.get("rounds") or []):
        phones_out = []
        for item in rnd.get("phones") or []:
            phones_out.append({
                "name": item.get("name") or "",
                "pad": item.get("pad") or "",
                "sessions": list(item.get("sessions") or []),
            })
        rounds.append({
            "id": rnd.get("id"),
            "started_at": rnd.get("started_at"),
            "phones": phones_out,
            "count": sum(len(p["sessions"]) for p in phones_out),
        })
    return {
        "path": str(SESSION_DIR),
        "total": len(ids),
        "available": max(0, len(ids) - in_use),
        "in_use": in_use,
        "fresh": fresh,
        "rounds": rounds,
        "phones": phones,
        "sessions": rows[:200],
    }
