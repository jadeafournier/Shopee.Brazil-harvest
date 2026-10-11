# -*- coding: utf-8 -*-
"""앱이 pdp/get 을 다시 내게 한다. HTTP 위조 없음.

1) VIEW + NEW_TASK|CLEAR_TASK  — 켜진 화면 스택만 지움
2) am start -S                 — 시작 전에 프로세스 중지(콜드스타트)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from br_emulator import _save
from br_emulator.vmos_app_capture import (
    DEFAULT_PORT,
    MITM_LOG,
    PROXY_ACT,
    RUNTIME,
    _log,
    _sh,
    _view,
    _warm_app,
    ensure_device,
    install_system_ca,
    load_targets,
    setup_reverse_proxy,
    start_mitm,
    stop_mitm,
)


def mitm_hit(item_id: int, since: float) -> str:
    try:
        tail = MITM_LOG.read_text(encoding="utf-8", errors="replace")[-16000:]
    except Exception:
        return "no-log"
    if f"{item_id}" not in tail or "pdp/get" not in tail:
        return "none"
    if "err=90309999" in tail and f"{item_id}" in tail:
        return "418"
    return "pdp"


def wait_hit(item_id: int, t0: float, sec: float = 12.0) -> str:
    last = "none"
    while time.time() - t0 < sec:
        last = mitm_hit(item_id, t0)
        if last in ("pdp", "418"):
            return last
        time.sleep(0.2)
    return last


def main() -> int:
    pending = [(s, i) for s, i, _ in load_targets(RUNTIME / "tk_br_rows23_120_part1_urls.txt") if not _save.already_saved(s, i)][:4]
    if len(pending) < 4:
        _log("미저장 4건 필요")
        return 2
    rec = ensure_device()
    serial = rec["serial"]
    install_system_ca(serial)
    up = (RUNTIME / "upstream.txt").read_text(encoding="utf-8").strip()
    mitm = start_mitm(DEFAULT_PORT, upstream=up)
    try:
        setup_reverse_proxy(serial, DEFAULT_PORT)
        _log("강제1 CLEAR_TASK — 프로세스 유지, 화면 스택만 지움")
        _warm_app(serial, DEFAULT_PORT, 8.0)
        for n, (sid, iid) in enumerate(pending[:2], 1):
            url = f"https://shopee.com.br/product/{sid}/{iid}"
            _log(f"CLEAR_TASK {n} i.{sid}.{iid}")
            t0 = time.time()
            out = _view(serial, url, reset_task=True)
            _log(f"  am {(out or '')[:160]}")
            hit = wait_hit(iid, t0)
            _log(f"  결과 {hit} {time.time() - t0:.1f}s")
            time.sleep(1.0)

        _log("강제2 am start -S — 시작 전 프로세스 중지")
        for n, (sid, iid) in enumerate(pending[2:4], 1):
            url = f"https://shopee.com.br/product/{sid}/{iid}"
            q = url.replace("&", "\\&")
            _log(f"START-S {n} i.{sid}.{iid}")
            t0 = time.time()
            out = _sh(
                serial,
                f'am start -S -n {PROXY_ACT} '
                f'-a android.intent.action.VIEW '
                f'-c android.intent.category.BROWSABLE -d "{q}"',
            )
            _log(f"  am {(out or '')[:160]}")
            hit = wait_hit(iid, t0)
            _log(f"  결과 {hit} {time.time() - t0:.1f}s")
            time.sleep(1.0)
    finally:
        stop_mitm(mitm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
