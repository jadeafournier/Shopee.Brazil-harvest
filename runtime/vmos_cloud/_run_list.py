# -*- coding: utf-8 -*-
"""새 상품 목록을 폰 3대에 나눠 바로 수집한다.

목록 없이 실행하면 준비 상태만 보여 준다.
목록을 주면 기본방식으로 즉시 수집하고, 끝난 뒤 미저장만 한 번 더 돈다.

  python _run_list.py
  python _run_list.py --prime
  python _run_list.py C:\\path\\products.csv
"""
from __future__ import annotations

import csv
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
HERE = Path(__file__).resolve().parent
CLIENT4 = ROOT / "client 4 api integration"
OUT = ROOT / "Shopee.Brazil"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from collect_layout import UPLOAD_DIR

FIXED_RUN = UPLOAD_DIR
RUN_POINTER = HERE / "received_run.txt"
CAPTURE = CLIENT4 / "br_emulator" / "vmos_app_capture.py"
PY = sys.executable
ADB = ROOT / "emu_setup" / "platform-tools" / "adb.exe"
PHONES_PATH = HERE / "phones.json"
SESSION_DIR = OUT / "Sessions"


def load_phones() -> list[dict]:
    phones = json.loads(PHONES_PATH.read_text(encoding="utf-8"))
    phones.sort(key=lambda p: int(p["shard"]))
    return phones


def session_ids() -> list[str]:
    if not SESSION_DIR.is_dir():
        return []
    return sorted(p.stem for p in SESSION_DIR.glob("*.tgz") if p.stem.isdigit())


def sessions_for(shard: int) -> list[str]:
    rest = {
        part.strip()
        for part in os.environ.get("SHOPEE_SESSION_REST", "").split(",")
        if part.strip()
    }
    try:
        import session_ledger

        name = next((p["name"] for p in load_phones() if int(p["shard"]) == shard), "")
        if name:
            pool = session_ledger.harvest_pool_userids(name, int(shard))
            return [u for u in pool if u not in rest]
    except Exception:
        pass
    return []


def newest_session(uids: list[str]) -> str:
    best = ""
    best_ts = -1.0
    for uid in uids:
        meta = SESSION_DIR / f"{uid}.json"
        ts = 0.0
        if meta.is_file():
            try:
                ts = float(json.loads(meta.read_text(encoding="utf-8")).get("saved_at") or 0)
            except (OSError, ValueError, json.JSONDecodeError):
                ts = 0.0
        if ts >= best_ts:
            best = uid
            best_ts = ts
    return best


def collection_dir(*, create: bool = False) -> Path:
    """상품 JSON은 Upload 폴더에 바로 쓴다."""
    if create:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DIR


def begin_run(paths: list[Path]) -> Path:
    """상품 JSON은 Upload\\목록파일이름 폴더에 쓴다."""
    run = collection_dir(create=True)
    RUN_POINTER.write_text(str(run), encoding="utf-8")
    (run / "_run.json").write_text(
        json.dumps(
            {
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "sources": [str(p) for p in paths],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return run


def current_run_dir() -> Path:
    return collection_dir(create=True)


def _save_mod():
    os.environ["SHOPEE_OUT_DIR"] = str(current_run_dir())
    os.environ["SHOPEE_WEB_HOST"] = "shopee.com.br"
    if str(CLIENT4) not in sys.path:
        sys.path.insert(0, str(CLIENT4))
    from br_emulator import _save
    return _save


def load_todo(paths: list[Path]) -> tuple[int, list[str]]:
    save = _save_mod()
    seen: set[tuple[int, int]] = set()
    todo: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() == ".csv" or text.lstrip().lower().startswith("item_id"):
            for row in csv.DictReader(text.splitlines()):
                try:
                    shop_id = int(row["shop_id"])
                    item_id = int(row["item_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                if (shop_id, item_id) in seen:
                    continue
                seen.add((shop_id, item_id))
                if save.already_saved(shop_id, item_id):
                    continue
                todo.append(f"https://shopee.com.br/product/{shop_id}/{item_id}")
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ids = save.parse_ids(line)
            if not ids or ids in seen:
                continue
            seen.add(ids)
            if save.already_saved(*ids):
                continue
            todo.append(f"https://shopee.com.br/product/{ids[0]}/{ids[1]}")
    return len(seen), todo


def prepare_urls(paths: list[Path]) -> int:
    unique, todo = load_todo(paths)
    shards = [int(p["shard"]) for p in load_phones()] or [0]
    n = len(shards)
    for pos, shard in enumerate(shards):
        rows = todo[pos::n]
        path = HERE / f"list_urls_{shard}.txt"
        path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
        print(f"shard {shard} {len(rows)}", flush=True)
    print(f"unique={unique} todo={len(todo)} phones={n}", flush=True)
    return len(todo)


def unsaved_from(urls: Path) -> list[str]:
    save = _save_mod()
    missed: set[tuple[int, int]] = set()
    try:
        import list_progress

        pc = len(load_phones())
        if list_progress.phone_only_shard(pc) is None:
            missed = list_progress.missed_pairs()
    except Exception:
        missed = set()
    left: list[str] = []
    if not urls.is_file():
        return left
    for line in urls.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        ids = save.parse_ids(line)
        if not ids or save.already_saved(*ids) or ids in missed:
            continue
        left.append(f"https://shopee.com.br/product/{ids[0]}/{ids[1]}")
    return left


def _record_remaining_misses(urls: Path, phone_count: int = 3) -> None:
    import list_progress

    if list_progress.phone_only_shard(phone_count) is not None:
        return
    for line in unsaved_from(urls):
        ids = _save_mod().parse_ids(line)
        if ids:
            list_progress.mark_pair_missed(ids, phone_count)


def _run_harvest_cycle(phone: dict, shard: int, urls: Path, log_path: Path, phone_count: int) -> None:
    code = run_harvest(phone, urls, log_path)
    left = unsaved_from(urls)
    retry = HERE / f"list_retry_{shard}.txt"
    retry.write_text("\n".join(left) + ("\n" if left else ""), encoding="utf-8")
    with log_path.open("a", encoding="utf-8") as log:
        if not left:
            log.write(f"완료 저장 미저장 없음 — 재수집 생략 (1차 종료코드 {code})\n")
            return
        log.write(f"\n---- list shard {shard} 재수집 {len(left)}건 ----\n")
        log.write(f"1차 종료코드 {code} · 미저장 {len(left)}건 재수집 시작\n")
    code2 = run_harvest(phone, retry, log_path)
    still = unsaved_from(retry)
    _record_remaining_misses(retry, phone_count)
    _record_remaining_misses(urls, phone_count)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"재수집 끝 종료코드 {code2} · 남은 미저장 {len(still)} (미저장 목록 반영)\n")


def _port_open(port: int) -> bool:
    sock = socket.socket()
    sock.settimeout(0.4)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def connect_phone(phone: dict) -> str:
    if str(CLIENT4) not in sys.path:
        sys.path.insert(0, str(CLIENT4))
    from br_emulator.vmos_cloud import enable_adb, start_ssh_tunnel

    data = enable_adb(phone["pad"])
    serial = (data.get("adb") or "").split()[-1]
    port = int(serial.rsplit(":", 1)[-1])
    if not _port_open(port):
        start_ssh_tunnel(data["command"], data["key"])
        time.sleep(5)
    env = os.environ.copy()
    env["ANDROID_ADB_SERVER_PORT"] = phone["adb_port"]
    server_port = int(phone["adb_port"])
    if _port_open(server_port):
        print(f"  {phone['name']} adb 서버 재사용 port={server_port}", flush=True)
    else:
        try:
            subprocess.run(
                [str(ADB), "-P", phone["adb_port"], "start-server"],
                env=env, capture_output=True, text=True, timeout=20,
            )
        except subprocess.TimeoutExpired:
            print(f"  {phone['name']} adb start-server 시간 초과", flush=True)
    msg = ""
    for _ in range(4):
        try:
            out = subprocess.run(
                [str(ADB), "-P", phone["adb_port"], "connect", serial],
                env=env, capture_output=True, text=True, timeout=20,
            )
        except subprocess.TimeoutExpired:
            msg = "connect timeout"
            time.sleep(3)
            continue
        msg = ((out.stdout or "") + (out.stderr or "")).strip()
        if "connected" in msg and "cannot" not in msg:
            break
        time.sleep(3)
    phone["serial"] = serial
    print(f"  {phone['name']} {serial} {msg}", flush=True)
    return serial


def run_harvest(phone: dict, urls: Path, log_path: Path) -> int:
    try:
        import upload_manager

        upload_manager.ensure_started()
    except Exception:
        pass
    serial = connect_phone(phone)
    allow = sessions_for(int(phone["shard"]))
    env = os.environ.copy()
    for key in ("HARVEST_HUMAN", "HARVEST_MODE", "BLOCKED_USERIDS", "PHONE_EGRESS"):
        env.pop(key, None)
    try:
        import session_ledger
        session_ledger.note_assigned(phone["name"], phone["pad"], int(phone["shard"]), allow)
    except Exception as exc:
        print(f"  세션 할당 기록 실패: {exc}", flush=True)
    env.update({
        "VMOS_PAD_CODE": phone["pad"],
        "SHOPEE_SERIAL": serial,
        "SHOPEE_PAD_NAME": phone["name"],
        "SHOPEE_PKG": "com.shopee.br",
        "SHOPEE_WEB_HOST": "shopee.com.br",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "HARVEST_LOG": str(log_path),
        "SHOPEE_MITM_LOG": str(HERE / f"mitm_list_{phone['port']}.log"),
        "SHOPEE_SESSION_ALLOW": ",".join(allow),
        "SHOPEE_SESSION_ROTATE_MIN": os.environ.get("SHOPEE_SESSION_ROTATE_MIN") or "1",
        "SHOPEE_OUT_DIR": str(current_run_dir()),
        "ANDROID_ADB_SERVER_PORT": phone["adb_port"],
        "SHOPEE_RUNTIME": str(HERE),
        "SHOPEE_LIST_SHARD": str(phone["shard"]),
    })
    try:
        import collect_layout

        if collect_layout.list_mode() == "mode2":
            # 목록관리2: 엔진이 전역 풀에서 한 건씩 당기며 연속 수집(앱 재실행 없음)
            env["SHOPEE_PULL_SHARD"] = str(phone["shard"])
            env["SHOPEE_LIST_MODE"] = "mode2"
    except Exception:
        pass
    proc = subprocess.run(
        [
            PY, "-u", str(CAPTURE),
            "--method", "기본방식",
            "--no-proxy",
            "--go",
            "--port", phone["port"],
            "--urls", str(urls),
        ],
        env=env,
        cwd=str(CLIENT4),
    )
    return proc.returncode


def prime() -> int:
    """각 폰에 자기 몫의 가장 최근 세션을 올려 로그인 상태로 둔다."""
    if str(CLIENT4) not in sys.path:
        sys.path.insert(0, str(CLIENT4))
    os.environ.setdefault("SHOPEE_WEB_HOST", "shopee.com.br")
    from br_emulator.vmos_app_capture import restore_session

    for phone in load_phones():
        allow = sessions_for(int(phone["shard"]))
        uid = newest_session(allow)
        serial = connect_phone(phone)
        os.environ["ANDROID_ADB_SERVER_PORT"] = phone["adb_port"]
        os.environ["SHOPEE_SERIAL"] = serial
        print(f"  {phone['name']} 세션 {uid} 복원", flush=True)
        ok = restore_session(serial, uid)
        print(f"  {phone['name']} 복원 {'됨' if ok else '실패'}", flush=True)
    return 0


def show_ready() -> int:
    phones = load_phones()
    ids = session_ids()
    print(f"준비됨  폰 {len(phones)}대  보관 세션 {len(ids)}개", flush=True)
    print("방식 기본방식  상류 프록시 없음  미저장 1회 재수집", flush=True)
    print(f"저장 폴더 {FIXED_RUN}", flush=True)
    for phone in phones:
        allow = sessions_for(int(phone["shard"]))
        print(
            f"  {phone['name']}  pad {phone['pad']}  세션 {len(allow)}개  최근 {newest_session(allow)}",
            flush=True,
        )
    print("목록을 주면 바로 수집합니다.", flush=True)
    return 0


def launch_all(paths: list[Path]) -> int:
    n = prepare_urls(paths)
    if n <= 0:
        print("수집할 새 상품이 없습니다.", flush=True)
        return 0
    run = begin_run(paths)
    print(f"이번 저장 폴더 {run}", flush=True)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    for phone in load_phones():
        shard = int(phone["shard"])
        log_path = HERE / f"list_{shard}.log"
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n---- list shard {shard} {phone['name']} ----\n")
        subprocess.Popen(
            [PY, "-u", str(Path(__file__).resolve()), "--shard", str(shard)],
            cwd=str(HERE),
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"수집 시작 shard {shard} {phone['name']}", flush=True)
    return 0


def assign_shard_urls(shard: int, phone_count: int = 3) -> int:
    """이 폰 몫만 파일로 나눈다. 재분배(claims)가 있으면 그 몫을 따른다."""
    import list_progress

    return list_progress.refresh_worklist_shard(int(shard), phone_count)


def run_shard_pool(phone: dict, shard: int, log_path: Path, urls: Path, source_name: str) -> int:
    """목록관리2: pull batches from one shared global pool (work-stealing)."""
    import list_pool_manager as pool
    import upload_manager

    try:
        upload_manager.ensure_started()
    except Exception:
        pass
    # 목록관리2: 엔진의 연속 pull 모드로 돌린다.
    #  - run_harvest 가 SHOPEE_PULL_SHARD 를 켜므로, 엔진은 전역 풀에서 한 건씩
    #    (list_progress.claim_next → list_pool_manager.lease) 당기며 멈추지 않고
    #    수집한다. 40건마다 12초 휴식은 엔진 내부에서 그대로 일어나고, 그 사이
    #    다음 건을 풀에서 가져오므로 배치 재실행(앱 재기동) 비용이 없다.
    #  - 저장되면 _drop_from_worklist → remove_pair_from_queues → clear_pair 로,
    #    미저장이면 _mark_missed_in_worklist → mark_pair_missed → mark_missed 로
    #    리스가 정리된다.
    #  - 풀이 비면 엔진은 "목록 갱신 대기"로 유휴 상태가 되어, 서버에서 새 목록이
    #    오면(reconcile_with_server 가 풀 보충) 자동으로 이어서 수집한다.
    # 초기 시드: 첫 배치를 미리 써 둬서 엔진이 곧바로 시작하게 한다(없어도 pull 로 채움).
    seed = pool.lease(int(shard), pool.LEASE_BATCH)
    urls.write_text("\n".join(seed) + ("\n" if seed else ""), encoding="utf-8")
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n---- list {source_name} shard {shard} (목록관리2·연속 pull) ----\n")
        snap = pool.snapshot()
        log.write(
            f"전역 풀 {snap['pool_remaining']}건 · 시드 {len(seed)}건 — 연속 수집 시작\n"
        )
    return run_harvest(phone, urls, log_path)


def run_shard(shard: int) -> int:
    phone = load_phones()[shard]
    log_path = HERE / f"list_{shard}.log"
    urls = HERE / f"list_urls_{shard}.txt"
    phone_count = len(load_phones())
    import list_progress

    only = list_progress.phone_only_shard(phone_count)
    if only is not None and int(shard) != only:
        urls.write_text("", encoding="utf-8")
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n---- list phone-only shard {only} — shard {shard} skip ----\n")
        return 0
    source_name = ""
    try:
        import task_lists
        source = task_lists.selected_path()
        source_name = source.name if source is not None else ""
    except Exception:
        source_name = ""
    if not source_name:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n---- list {source_name} shard {shard} ----\n")
            log.write("대상 없음 — 수집 생략\n")
        return 0
    try:
        import collect_layout
        if collect_layout.list_mode() == "mode2":
            return run_shard_pool(phone, int(shard), log_path, urls, source_name)
    except Exception as exc:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"목록관리2 진입 실패 — 기존 방식 사용: {exc}\n")
    wait_mode = list_progress.pool_enabled()
    last_wait_log = 0.0
    import balance_manager

    while True:
        balance_manager.tick()
        if list_progress.pool_enabled():
            list_progress.prune_saved_from_queues(phone_count)
        assigned = assign_shard_urls(shard, phone_count)
        if assigned <= 0:
            if only is None:
                reb = balance_manager.extend_queue_if_low(int(shard), phone_count)
                if int(reb.get("moved") or 0) > 0:
                    mode = reb.get("mode") or ""
                    donor = reb.get("donor")
                    with log_path.open("a", encoding="utf-8") as log:
                        if mode == "idle_donor":
                            log.write(
                                f"자동 균형 — 미수집 폰 shard {donor} 잔량 {reb.get('moved')}건 전부 가져옴\n"
                            )
                        else:
                            log.write(
                                f"자동 균형 — shard {donor} 잔량의 절반 {reb.get('moved')}건 가져옴\n"
                            )
                    if mode == "busy_half" and donor is not None:
                        try:
                            import harvest_control

                            donor_row = load_phones()[int(donor)]
                            harvest_control.stop(str(donor_row["name"]))
                            harvest_control.start(str(donor_row["name"]))
                        except Exception as exc:
                            with log_path.open("a", encoding="utf-8") as log:
                                log.write(f"자동 균형 — donor 재시작 실패: {exc}\n")
                    continue
            if not wait_mode:
                with log_path.open("a", encoding="utf-8") as log:
                    log.write("목록 잔량 0 — 수집 종료\n")
                return 0
            now = time.time()
            if now - last_wait_log >= 15:
                with log_path.open("a", encoding="utf-8") as log:
                    log.write("목록 잔량 0 — 할당 대기 중 (자동·수동 배정 시 재개)\n")
                last_wait_log = now
            time.sleep(5)
            continue
        last_wait_log = 0.0
        balance_manager.extend_queue_if_low(int(shard), phone_count)
        balance_manager.check_rotation_handoff(int(shard))
        assigned = assign_shard_urls(shard, phone_count)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n---- list {source_name} shard {shard} ----\n")
            log.write(f"이 폰 목록 {assigned}건\n")
            log.write("1차 수집 시작\n")
        _run_harvest_cycle(phone, shard, urls, log_path, phone_count)
        balance_manager.extend_queue_if_low(int(shard), phone_count)
        balance_manager.check_rotation_handoff(int(shard))
        if only is not None and assign_shard_urls(shard, phone_count) > 0:
            time.sleep(45)
        if not wait_mode:
            return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        return show_ready()
    if args[0] == "--prime":
        return prime()
    if args[0] == "--shard":
        return run_shard(int(args[1]))
    paths = [Path(a) for a in args]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        print("목록 파일을 찾지 못했습니다: " + ", ".join(missing), flush=True)
        return 1
    return launch_all(paths)


if __name__ == "__main__":
    raise SystemExit(main())
