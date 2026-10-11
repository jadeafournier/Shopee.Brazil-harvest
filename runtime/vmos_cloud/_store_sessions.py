# -*- coding: utf-8 -*-
"""로그인한 계정 세션을 보관한다. 1~2번째는 다음 계정을 위해 로그아웃·SMS 화면까지 준비한다.

3번째 보관 후에는 로그아웃하지 않고 세션수집만 종료한다. STOP 파일이 생기면 종료한다.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from br_emulator.vmos_app_capture import (  # noqa: E402
    SESSION_DIR,
    _account_userid_on_device,
    _account_username_on_device,
    _session_parts_present,
    launch_shopee,
    list_sessions,
    prepare_fresh_login,
    snapshot_session,
)
_phone_path = Path(__file__).resolve().parent / "_phone_login.py"
_spec = importlib.util.spec_from_file_location("phone_login", _phone_path)
_phone_login = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_phone_login)
submit_bought_number = _phone_login.submit_bought_number

_args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
SERIAL = _args[0] if _args else "localhost:58070"
LABEL = _args[1] if len(_args) > 1 else SERIAL
STOP = SESSION_DIR / f"STOP_{SERIAL.replace(':', '_')}"
LOG = ROOT / "runtime" / "vmos_cloud" / f"session_store_{SERIAL.replace(':', '_')}.log"


def _log(msg: str) -> None:
    line = msg.rstrip()
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _wait_userid() -> str:
    """앱에 로그인 userid 가 안정적으로 보일 때까지 기다린다."""
    stable = 0
    last = ""
    noted = 0.0
    while not STOP.is_file():
        uid = _account_userid_on_device(SERIAL)
        if uid and uid == last:
            stable += 1
            if stable >= 2:
                return uid
        else:
            last = uid or ""
            stable = 1 if uid else 0
        now = time.time()
        if now - noted >= 30:
            _log("  다음 계정 로그인 대기 — 홈에서만 로그인하세요")
            noted = now
        time.sleep(3.0)
    return ""


def _remember(uid: str) -> None:
    try:
        import session_ledger
        session_ledger.note_created(uid, LABEL, SERIAL)
    except Exception as exc:
        _log(f"  세션 장부 기록 실패: {exc}")


def _keep(uid: str) -> str:
    parts = _session_parts_present(SERIAL)
    _log("  세션 패키지 " + (", ".join(parts) if parts else "없음"))
    if "shared_prefs" not in parts:
        _log("  보관 불가 — shared_prefs 없음 (로그인·쿠키 데이터 부족)")
        return ""
    try:
        saved = snapshot_session(SERIAL)
    except Exception as exc:
        _log(f"  세션 보관 예외: {exc}")
        saved = ""
    if saved:
        return saved
    path = SESSION_DIR / f"{uid}.tgz"
    if path.is_file() and path.stat().st_size >= 10_000:
        _log(f"  세션은 이미 보관됨 userid={uid}")
        return uid
    return ""


def _adb_port() -> str | None:
    port = (os.environ.get("ANDROID_ADB_SERVER_PORT") or "").strip()
    return port or None


def _open_sms_login_and_fill_number() -> bool:
    """전화번호(SMS) 로그인 화면까지 연 뒤 smscode.gg 에서 번호를 사서 입력한다."""
    cred = _HERE / "smscode_credentials.json"
    if not cred.is_file():
        _log("  번호 구매 불가 — smscode_credentials.json 없음")
        return False
    port = _adb_port()
    last_err = ""
    for attempt in range(3):
        try:
            _log(f"  SMS 로그인 화면 · 번호 구매·입력 ({attempt + 1}/3)")
            digits = submit_bought_number(SERIAL, _log, port=port)
            _log(f"  번호 입력 완료 ({digits}) — Próximo 는 누르지 않음")
            return True
        except Exception as exc:
            last_err = str(exc)
            _log(f"  전화번호 화면/구매/입력 실패: {exc}")
            time.sleep(2.5)
    _log(f"  전화번호 로그인 준비 실패: {last_err or 'unknown'}")
    return False


def _prepare_next_account() -> None:
    """앱 로그인을 지운 뒤 SMS 전화번호 화면까지 연다."""
    _log("  로그아웃·앱 초기화 후 전화번호 로그인 화면으로")
    prepare_fresh_login(SERIAL)
    time.sleep(2.0)
    if not _open_sms_login_and_fill_number():
        _log("  자동 번호 입력 실패 — session_store 로그의 마지막 SMS 줄을 확인하세요")


SESSION_LIMIT = 3


def _stop_collect_keep_login() -> None:
    """3개 보관 완료 — 로그아웃 없이 세션수집 종료(세션중지와 동일 신호)."""
    _log(f"  세션 {SESSION_LIMIT}개 보관 — 로그아웃 없이 세션수집 종료")
    STOP.parent.mkdir(parents=True, exist_ok=True)
    STOP.write_text("limit\n", encoding="utf-8")
    try:
        launch_shopee(SERIAL)
        uid = _account_userid_on_device(SERIAL)
        if uid:
            _log(f"  앱 유지 userid={uid} (로그인 상태)")
        else:
            _log("  앱 재실행 — 스냅샷 후 홈 확인")
    except Exception as exc:
        _log(f"  앱 재실행 실패: {exc}")


def main() -> int:
    wait_only = "--wait-only" in sys.argv
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    if STOP.is_file():
        STOP.unlink()
    kept = 0
    _log("---- 세션 보관 시작 ----")
    _log(f"  폰 {LABEL}  기기 {SERIAL}")
    _log("  이미 보관됨 " + (", ".join(list_sessions()) or "없음"))

    def _noted(uid: str) -> bool:
        nonlocal kept
        _remember(uid)
        kept += 1
        _log(f"  보관 완료 userid={uid}  이번 {kept}개  현재 {len(list_sessions())}개")
        if kept >= SESSION_LIMIT:
            _stop_collect_keep_login()
            return True
        return False

    if not wait_only:
        current = _account_userid_on_device(SERIAL)
        if current:
            _log(f"  현재 로그인 userid={current} — 지우기 전에 세션 보관")
            saved_now = _keep(current)
            if saved_now:
                _noted(saved_now)
        if kept < SESSION_LIMIT and not STOP.is_file():
            if not current:
                _log("  로그인 계정 없음 — 전화번호 로그인 화면부터 준비")
            _prepare_next_account()
    else:
        _log("  로그인 대기 — 화면에 넣은 번호는 유지합니다")
    while kept < SESSION_LIMIT and not STOP.is_file():
        uid = _wait_userid()
        if not uid or STOP.is_file():
            break
        _log(f"  로그인 확인 userid={uid} — 세션 보관")
        saved = _keep(uid)
        if saved:
            if _noted(saved):
                break
        else:
            _log(f"  보관 실패 userid={uid} — 앱을 비우고 다시 기다립니다")
        if STOP.is_file() or kept >= SESSION_LIMIT:
            break
        if kept < SESSION_LIMIT:
            _prepare_next_account()
    _log("---- 세션 보관 종료 ----")
    _log("  보관된 계정 " + (", ".join(list_sessions()) or "없음"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
