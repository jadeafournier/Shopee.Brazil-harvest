# -*- coding: utf-8 -*-
"""row 25·26·27 을 폰에 나눠 수집한다.

수집폰은 바로 시작한다. row_128 이 아직인 폰은 그 수집이 끝난 뒤 이어받는다.
각 폰은 한 바퀴가 끝나면 아직 저장되지 않은 상품만 한 번 더 수집한다.
"""
from __future__ import annotations

import csv
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
HERE = Path(__file__).resolve().parent
CLIENT4 = ROOT / "client 4 api integration"
OUT = CLIENT4 / "br_deliverable_rows25_27"
CAPTURE = CLIENT4 / "br_emulator" / "vmos_app_capture.py"
PY = sys.executable
CSVS = [
    Path(r"C:\Users\Administrator\Music\client5_product_urls_rows_23_120\row_25\products.csv"),
    Path(r"C:\Users\Administrator\Music\client5_product_urls_rows_23_120\row_26\products.csv"),
    Path(r"C:\Users\Administrator\Music\client5_product_urls_rows_23_120\row_27\products.csv"),
]
PHONES = [
    {
        "pad": "AC20250306MHUQVN",
        "serial": "localhost:58070",
        "name": "Phone3",
        "port": "8088",
        "adb_port": "5038",
        "sessions": "1706784820,15774375407,14856138265",
    },
    {
        "pad": "ACP251005M8NPVM5",
        "serial": "localhost:55949",
        "name": "Phone2",
        "port": "8089",
        "adb_port": "5039",
        "sessions": "14836448858,13526726098,1706785017",
    },
    {
        "pad": "ACP250719PQRGGM6",
        "serial": "localhost:53463",
        "name": "Phone1",
        "port": "8090",
        "adb_port": "5040",
        "sessions": "15670411309,14797524913",
    },
]


def _save_mod():
    os.environ["SHOPEE_OUT_DIR"] = str(OUT)
    os.environ["SHOPEE_WEB_HOST"] = "shopee.com.br"
    if str(CLIENT4) not in sys.path:
        sys.path.insert(0, str(CLIENT4))
    from br_emulator import _save
    return _save


def load_todo() -> tuple[int, list[str]]:
    save = _save_mod()
    seen: set[tuple[int, int]] = set()
    todo: list[str] = []
    for path in CSVS:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
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
    return len(seen), todo


def prepare_urls() -> int:
    unique, todo = load_todo()
    shards = [todo[i::3] for i in range(3)]
    for i, rows in enumerate(shards):
        path = HERE / f"rows2527_urls_{i}.txt"
        path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
        print(f"shard {i} {len(rows)}", flush=True)
    print(f"unique={unique} todo={len(todo)}", flush=True)
    return len(todo)


def unsaved_from(urls: Path) -> list[str]:
    save = _save_mod()
    left: list[str] = []
    if not urls.is_file():
        return left
    for line in urls.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        ids = save.parse_ids(line)
        if not ids:
            continue
        if save.already_saved(*ids):
            continue
        left.append(line if "://" in line else f"https://shopee.com.br/product/{ids[0]}/{ids[1]}")
    return left


def proc_alive(script: str, shard: int) -> bool:
    try:
        out = subprocess.check_output(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                f"Where-Object {{ $_.CommandLine -like '*{script}*' }} | "
                "ForEach-Object { $_.CommandLine }",
            ],
            text=True, timeout=25, encoding="utf-8", errors="replace",
        )
    except Exception as exc:
        print(f"  프로세스 확인 실패 — 대기 유지: {exc}", flush=True)
        return True
    for line in (out or "").splitlines():
        if re.search(rf"{re.escape(script)}\D+{shard}\s*$", line.strip()):
            return True
    return False


def wait_row128(shard: int, log) -> None:
    if shard == 0 or not proc_alive("_run_row128.py", shard):
        return
    msg = "row_128 수집이 끝날 때까지 대기"
    print(f"  {msg}", flush=True)
    log.write(msg + "\n")
    log.flush()
    while proc_alive("_run_row128.py", shard):
        time.sleep(20)
    print("  row_128 종료 — row 25–27 시작", flush=True)
    log.write("row_128 종료 — row 25–27 시작\n")
    log.flush()


def run_harvest(phone: dict, urls: Path, log_path: Path) -> int:
    env = os.environ.copy()
    for key in ("HARVEST_HUMAN", "HARVEST_MODE", "BLOCKED_USERIDS", "PHONE_EGRESS"):
        env.pop(key, None)
    env.update({
        "VMOS_PAD_CODE": phone["pad"],
        "SHOPEE_SERIAL": phone["serial"],
        "SHOPEE_PAD_NAME": phone["name"],
        "SHOPEE_PKG": "com.shopee.br",
        "SHOPEE_WEB_HOST": "shopee.com.br",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "HARVEST_LOG": str(log_path),
        "SHOPEE_MITM_LOG": str(HERE / f"mitm_rows2527_{phone['port']}.log"),
        "SHOPEE_SESSION_ALLOW": phone["sessions"],
        "SHOPEE_OUT_DIR": str(OUT),
        "ANDROID_ADB_SERVER_PORT": phone["adb_port"],
    })
    attach_adb(phone, env)
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


ADB = r"D:\Luxury\Great Scraper2\Great Scraper\emu_setup\platform-tools\adb.exe"


def attach_adb(phone: dict, env: dict) -> None:
    """이 폰만 자기 ADB 서버에 붙인다. 기본 서버(5037)에서는 이 시리얼만 떼어 낸다."""
    serial = phone["serial"]
    adb_port = phone["adb_port"]
    try:
        subprocess.run(
            [ADB, "-P", "5037", "disconnect", serial],
            capture_output=True, text=True, timeout=8,
        )
    except Exception as exc:
        print(f"  기본 ADB에서 분리 생략: {exc}", flush=True)
    try:
        subprocess.run(
            [ADB, "-P", adb_port, "start-server"],
            env=env, capture_output=True, text=True, timeout=15,
        )
        out = subprocess.run(
            [ADB, "-P", adb_port, "connect", serial],
            env=env, capture_output=True, text=True, timeout=15,
        )
        msg = ((out.stdout or "") + (out.stderr or "")).strip()
        print(f"  전용 ADB {adb_port} {msg}", flush=True)
    except Exception as exc:
        print(f"  전용 ADB 연결 실패: {exc}", flush=True)


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] == "prep":
        prepare_urls()
        return 0
    shard = int(sys.argv[1])
    phone = PHONES[shard]
    log_path = HERE / f"rows2527_{shard}.log"
    urls = HERE / f"rows2527_urls_{shard}.txt"
    OUT.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n---- rows25 shard {shard} {phone['name']} port {phone['port']} adb {phone['adb_port']} ----\n")
        log.flush()
    if not urls.is_file() or not urls.read_text(encoding="utf-8").strip():
        with log_path.open("a", encoding="utf-8") as log:
            log.write("대상 없음 — 수집 생략\n")
        print("대상 없음", flush=True)
        return 0
    print(f"1차 수집 shard {shard}", flush=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("1차 수집 시작\n")
    code = run_harvest(phone, urls, log_path)
    left = unsaved_from(urls)
    retry = HERE / f"rows2527_retry_{shard}.txt"
    retry.write_text("\n".join(left) + ("\n" if left else ""), encoding="utf-8")
    with log_path.open("a", encoding="utf-8") as log:
        if not left:
            log.write(f"미저장 없음 — 재수집 생략 (1차 종료코드 {code})\n")
            print(f"shard {shard} 미저장 없음", flush=True)
            return 0
        log.write(f"\n---- rows25 shard {shard} 재수집 {len(left)}건 ----\n")
        log.write(f"1차 종료코드 {code} · 미저장 {len(left)}건 재수집 시작\n")
    print(f"재수집 shard {shard} {len(left)}건", flush=True)
    code2 = run_harvest(phone, retry, log_path)
    still = unsaved_from(retry)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"재수집 끝 종료코드 {code2} · 남은 미저장 {len(still)}\n")
    print(f"shard {shard} 재수집 끝 남은 {len(still)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
