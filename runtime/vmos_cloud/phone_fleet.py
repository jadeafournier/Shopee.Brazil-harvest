# -*- coding: utf-8 -*-
"""Fixed 10-phone fleet: runtime files, default ports, register new devices."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PHONES_PATH = HERE / "phones.json"
FLEET_SLOTS = 10
BASE_MITM_PORT = 8088
BASE_ADB_PORT = 5071


def fleet_slot_count() -> int:
    return FLEET_SLOTS


def registered_shards() -> list[int]:
    """Shard ids for phones currently in phones.json (with a pad code)."""
    rows = load_phones_raw()
    return sorted(int(r["shard"]) for r in rows if str(r.get("pad") or "").strip())


def registered_phone_count() -> int:
    return len(registered_shards())


def default_ports(shard: int) -> dict[str, str]:
    s = max(0, int(shard))
    return {"port": str(BASE_MITM_PORT + s), "adb_port": str(BASE_ADB_PORT + s)}


def ensure_fleet_runtime(slots: int | None = None) -> None:
    """Create per-shard queue + log files so a new phone can bind to a free shard immediately."""
    n = FLEET_SLOTS if slots is None else max(1, int(slots))
    HERE.mkdir(parents=True, exist_ok=True)
    for shard in range(n):
        queue = HERE / f"list_urls_{shard}.txt"
        if not queue.is_file():
            queue.write_text("", encoding="utf-8")
        log = HERE / f"list_{shard}.log"
        if not log.is_file():
            log.write_bytes(b"")


def load_phones_raw() -> list[dict]:
    if not PHONES_PATH.is_file():
        return []
    try:
        raw = json.loads(PHONES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    rows: list[dict] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            shard = int(row["shard"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(
            {
                "shard": shard,
                "pad": str(row.get("pad") or ""),
                "name": str(row.get("name") or ""),
                "port": str(row.get("port") or default_ports(shard)["port"]),
                "adb_port": str(row.get("adb_port") or default_ports(shard)["adb_port"]),
                "serial": str(row.get("serial") or ""),
            }
        )
    rows.sort(key=lambda r: int(r["shard"]))
    return rows


def save_phones_raw(rows: list[dict]) -> None:
    out = []
    for row in sorted(rows, key=lambda r: int(r["shard"])):
        entry = {
            "shard": int(row["shard"]),
            "pad": str(row.get("pad") or ""),
            "name": str(row.get("name") or ""),
            "port": str(row.get("port") or default_ports(int(row["shard"]))["port"]),
            "adb_port": str(row.get("adb_port") or default_ports(int(row["shard"]))["adb_port"]),
        }
        if row.get("serial"):
            entry["serial"] = str(row["serial"])
        out.append(entry)
    PHONES_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_pad_codes_by_name() -> list[str]:
    """After Change Region, VMOS may issue a new padCode — match by padName."""
    try:
        import _run_br_three as br

        pads = br._pads()
    except Exception:
        return []
    by_name = {
        str(p.get("padName") or "").strip(): str(p.get("padCode") or "").strip()
        for p in pads
        if str(p.get("padName") or "").strip() and str(p.get("padCode") or "").strip()
    }
    if not by_name:
        return []
    rows = load_phones_raw()
    changed: list[str] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        code = by_name.get(name)
        if not code or str(row.get("pad") or "") == code:
            continue
        row["pad"] = code
        changed.append(name)
    if changed:
        save_phones_raw(rows)
    return changed


def next_free_shard(rows: list[dict] | None = None) -> int | None:
    rows = rows if rows is not None else load_phones_raw()
    used = {int(r["shard"]) for r in rows}
    for shard in range(FLEET_SLOTS):
        if shard not in used:
            return shard
    return None


def register_phone(
    *,
    pad: str,
    name: str = "",
    shard: int | None = None,
    port: str = "",
    adb_port: str = "",
    serial: str = "",
) -> dict:
    """Append or replace one row in phones.json (run after VMOS pad is provisioned)."""
    pad = str(pad or "").strip()
    if not pad:
        return {"ok": False, "error": "pad required"}
    rows = load_phones_raw()
    if shard is not None:
        try:
            shard = int(shard)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid shard"}
    else:
        shard = next_free_shard(rows)
    if shard is None:
        return {"ok": False, "error": f"fleet full ({FLEET_SLOTS} slots)"}
    if shard < 0 or shard >= FLEET_SLOTS:
        return {"ok": False, "error": "shard out of range"}
    defaults = default_ports(shard)
    entry = {
        "shard": shard,
        "pad": pad,
        "name": (name or f"Phone{shard + 1}").strip(),
        "port": str(port or defaults["port"]),
        "adb_port": str(adb_port or defaults["adb_port"]),
    }
    if serial:
        entry["serial"] = str(serial).strip()
    rows = [r for r in rows if int(r["shard"]) != shard]
    rows.append(entry)
    save_phones_raw(rows)
    ensure_fleet_runtime()
    return {"ok": True, "phone": entry}


def _cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Register a VMOS pad into phones.json")
    parser.add_argument("--pad", default="", help="VMOS pad code")
    parser.add_argument("--name", default="", help="Display name (default PhoneN)")
    parser.add_argument("--shard", type=int, default=None, help="Shard 0..9 (default: next free)")
    parser.add_argument("--port", default="", help="MITM port (default 8088+shard)")
    parser.add_argument("--adb-port", default="", help="ADB port (default 5071+shard)")
    parser.add_argument("--serial", default="", help="ADB serial e.g. localhost:5074")
    parser.add_argument("--ensure-files", action="store_true", help="Only create queue/log files for all slots")
    args = parser.parse_args(argv)
    if args.ensure_files:
        ensure_fleet_runtime()
        print("ensure_fleet_runtime", FLEET_SLOTS, flush=True)
        return 0
    result = register_phone(
        pad=args.pad,
        name=args.name,
        shard=args.shard,
        port=args.port,
        adb_port=args.adb_port,
        serial=args.serial,
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
