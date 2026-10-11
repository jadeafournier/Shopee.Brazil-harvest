# -*- coding: utf-8 -*-
"""로그인 완료 후 세션만 PC에 보관하고 수집을 다시 켠다. 이전 .tgz/.json 은 삭제하지 않는다."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
HERE = Path(__file__).resolve().parent
CLIENT = ROOT / "client 4 api integration"
ADB = ROOT / "emu_setup" / "platform-tools" / "adb.exe"

if str(CLIENT) not in sys.path:
    sys.path.insert(0, str(CLIENT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import harvest_control
import list_progress
import session_ledger
from _run_list import connect_phone, load_phones
from br_emulator.vmos_app_capture import launch_shopee, snapshot_session


def _rebalance_for_phone(phone: dict, phones: list[dict]) -> None:
    shard = int(phone["shard"])
    result = list_progress.auto_rebalance_for_shard(shard, len(phones))
    if not result.get("moved"):
        return
    print("rebalance", result, flush=True)
    if result.get("mode") != "busy_half":
        return
    donor = result.get("donor")
    if donor is None:
        return
    by_shard = {int(p["shard"]): p["name"] for p in phones}
    donor_name = by_shard.get(int(donor))
    if not donor_name:
        return
    print(donor_name, "harvest restart after rebalance", flush=True)
    harvest_control.stop(donor_name)
    harvest_control.start(donor_name)


def main() -> int:
    name = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    phones = {p["name"]: p for p in load_phones()}
    if name not in phones:
        print("usage: snap_after_login.py Phone3|Phone2|Phone1", flush=True)
        return 2
    phone = phones[name]
    serial = connect_phone(phone)
    print(name, "harvest stop", harvest_control.stop(name), flush=True)
    env = os.environ.copy()
    env["ANDROID_ADB_SERVER_PORT"] = phone["adb_port"]
    subprocess.run([str(ADB), "-P", phone["adb_port"], "connect", serial], env=env, capture_output=True)
    os.environ["ANDROID_ADB_SERVER_PORT"] = phone["adb_port"]
    os.environ["SHOPEE_PAD_NAME"] = name
    os.environ["VMOS_PAD_CODE"] = phone["pad"]
    os.environ.setdefault("SHOPEE_WEB_HOST", "shopee.com.br")
    uid = snapshot_session(serial)
    if not uid:
        print(name, "snapshot failed", flush=True)
        return 1
    meta_path = ROOT / "Shopee.Brazil" / "Sessions" / f"{uid}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    session_ledger.note_created(
        uid, name, serial, phone["pad"], username=str(meta.get("username") or "")
    )
    print(name, "saved ok", "username_ok", bool(meta.get("username")), flush=True)
    launch_shopee(serial)
    all_phones = list(load_phones())
    _rebalance_for_phone(phone, all_phones)
    print(name, "harvest start", harvest_control.start(name), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
