# -*- coding: utf-8 -*-
"""이미 소비한 상류 프록시를 재사용해 ADB를 다시 붙이고 로그인 화면을 연다."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "client 4 api integration"))

os.environ.setdefault("VMOS_PAD_CODE", "AC20250306MHUQVN")
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from br_emulator import vmos_cloud  # noqa: E402
from br_emulator.vmos_app_capture import (  # noqa: E402
    DEFAULT_PORT,
    RUNTIME,
    _log,
    _sh,
    install_system_ca,
    reset_shopee_login,
    setup_reverse_proxy,
    start_mitm,
)


def main() -> int:
    saved = RUNTIME / "upstream.txt"
    if not saved.is_file():
        raise RuntimeError("upstream.txt 없음")
    upstream = saved.read_text(encoding="utf-8").strip()
    if "proxy001.com" not in upstream:
        raise RuntimeError(f"저장된 상류가 proxy001 이 아님: {upstream}")
    _log(f"저장 상류 재사용 {upstream.split('@')[-1]}")
    rec = vmos_cloud.ensure_connected()
    serial = rec["serial"]
    _log(f"  기기 {serial}  {rec.get('padName')}")
    install_system_ca(serial)
    start_mitm(DEFAULT_PORT, upstream=upstream)
    setup_reverse_proxy(serial, DEFAULT_PORT)
    cur = _sh(serial, "settings get global http_proxy")
    _log(f"  로그인·수집 출구 http_proxy={cur} → 같은 브라질 sticky")
    reset_shopee_login(serial)
    _log("로그인 세션 삭제 완료. 쇼피 홈이 브라질 프록시 위에서 열려 있습니다.")
    _log("이 화면에서 로그인하세요. mitm을 끄면 출구가 바뀝니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
