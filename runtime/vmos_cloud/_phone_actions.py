# -*- coding: utf-8 -*-
"""선택한 폰만 Change Region 후 설치하거나, 세션 보관만 시작한다."""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

RUNTIME = Path(__file__).resolve().parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
import _run_br_three as run
import phone_activity

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
SESSION_DIR = ROOT / "Shopee.Brazil" / "Sessions"


def _clear(names: list[str], action: str) -> None:
    jobs = phone_activity._jobs()
    for name in names:
        job = jobs.get(name)
        if isinstance(job, dict) and job.get("mode") == action:
            jobs.pop(name, None)
    phone_activity._save_jobs(jobs)
    if action == "region":
        phone_activity.clear_progress(names)


def _persist_serial(phone: dict) -> None:
    serial = str(phone.get("serial") or "").strip()
    if not serial:
        return
    try:
        import phone_fleet

        rows = phone_fleet.load_phones_raw()
        changed = False
        for row in rows:
            if str(row.get("name") or "") == str(phone.get("name") or ""):
                row["serial"] = serial
                changed = True
                break
        if changed:
            phone_fleet.save_phones_raw(rows)
    except Exception:
        pass


def _stop_collectors(names: list[str]) -> None:
    _region, session = phone_activity._parse(phone_activity._commands())
    for name in names:
        serial = session.get(name)
        if not serial:
            continue
        stop = SESSION_DIR / f"STOP_{serial.replace(':', '_')}"
        stop.write_text("stop\n", encoding="utf-8")
        run._log(f"{name} 세션 보관 중지 요청")
    if any(name in session for name in names):
        time.sleep(2)


def _reload_phones(names: list[str]) -> list[dict]:
    try:
        import phone_fleet

        synced = phone_fleet.sync_pad_codes_by_name()
        for name in synced:
            run._log(f"{name} VMOS padCode 갱신")
    except Exception:
        pass
    return [phone for phone in phone_activity._phones() if phone["name"] in names]


def _region(names: list[str]) -> None:
    phones = _reload_phones(names)
    if not phones:
        return
    phone_activity.set_progress(names, "preparing", 10)
    _stop_collectors(names)
    phone_activity.set_progress(names, "resetting", 22)
    run._log("==== 선택 Change Region " + ", ".join(phone["name"] for phone in phones) + " ====")
    task_ids = run.change_region(phones)

    def _tasks(done: int, total: int) -> None:
        phone_activity.set_progress(names, "resetting", 22 + int(32 * done / max(total, 1)))

    if task_ids:
        run.wait_tasks(task_ids, on_tick=_tasks)
    else:
        phone_activity.set_progress(names, "resetting", 54)

    def _online(ready: int, total: int) -> None:
        phone_activity.set_progress(names, "online", 60 + int(14 * ready / max(total, 1)))

    phone_activity.set_progress(names, "online", 60)
    run.wait_online(phones, on_tick=_online)
    vm = run._vmos()
    phone_activity.set_progress(names, "connecting", 80)

    def _connect(phone: dict) -> None:
        run.connect_phone(vm, phone)
        phone_activity.set_progress([phone["name"]], "installing", 92)

    with ThreadPoolExecutor(max_workers=len(phones)) as pool:
        futures = [pool.submit(_connect, phone) for phone in phones]
        for fut in as_completed(futures):
            fut.result()

    def _install(phone: dict) -> None:
        run.install_shopee(phone, vm=vm)
        phone_activity.set_progress([phone["name"]], "done", 100)

    with ThreadPoolExecutor(max_workers=len(phones)) as pool:
        futures = [pool.submit(_install, phone) for phone in phones]
        for fut in as_completed(futures):
            fut.result()
    run._log("==== 선택 지역 변경·설치 완료 ====")


def _adb(names: list[str]) -> None:
    phones = _reload_phones(names)
    if not phones:
        return
    run._log("==== ADB reconnect " + ", ".join(phone["name"] for phone in phones) + " ====")
    vm = run._vmos()

    def _one(phone: dict) -> None:
        if not str(phone.get("pad") or "").strip():
            run._log(f"{phone['name']} no pad — skip")
            return
        try:
            run.connect_phone(vm, phone)
            _persist_serial(phone)
        except Exception as exc:
            run._log(f"{phone['name']} ADB reconnect failed: {exc}")

    with ThreadPoolExecutor(max_workers=len(phones)) as pool:
        futures = [pool.submit(_one, phone) for phone in phones]
        for fut in as_completed(futures):
            fut.result()
    run._log("==== ADB reconnect done ====")


def _reinstall(names: list[str]) -> None:
    phones = _reload_phones(names)
    if not phones:
        return
    run._log("==== 선택 Shopee 재설치 " + ", ".join(phone["name"] for phone in phones) + " ====")
    labels = [phone["name"] for phone in phones]
    phone_activity._stop_harvest_for_region(labels)
    phone_activity._stop_session_collect(labels)
    _stop_collectors(labels)
    time.sleep(2)
    vm = run._vmos()

    def _one(phone: dict) -> None:
        run.connect_phone(vm, phone)
        _persist_serial(phone)
        run.reinstall_shopee(phone, vm=vm)

    with ThreadPoolExecutor(max_workers=len(phones)) as pool:
        futures = [pool.submit(_one, phone) for phone in phones]
        for fut in as_completed(futures):
            fut.result()
    run._log("==== Shopee 재설치 완료 ====")


def _session(names: list[str]) -> None:
    phones = [phone for phone in phone_activity._phones() if phone["name"] in names]
    if not phones:
        return
    run._log("==== 선택 세션 보관 " + ", ".join(phone["name"] for phone in phones) + " ====")
    vm = run._vmos()
    for phone in phones:
        run.connect_phone(vm, phone)
        run.start_session(phone)


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    names = [arg for arg in sys.argv[2:] if arg]
    try:
        if action == "region":
            _region(names)
        elif action == "session":
            _session(names)
        elif action == "adb":
            _adb(names)
        elif action == "reinstall":
            _reinstall(names)
        else:
            raise RuntimeError(f"unknown action {action}")
    except Exception as exc:
        run._log(f"선택 작업 실패 {action}: {exc}")
        return 1
    finally:
        _clear(names, action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
