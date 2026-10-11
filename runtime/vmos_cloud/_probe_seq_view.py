# -*- coding: utf-8 -*-
"""어제 482건 방식: 앱 한 번 기동 후 BACK 없이 VIEW만 연속. mitm에 pdp/get이 나가는지 본다."""
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
    RUNTIME,
    _log,
    _view,
    _warm_app,
    ensure_device,
    install_system_ca,
    load_targets,
    setup_reverse_proxy,
    start_mitm,
    stop_mitm,
)


def mitm_has(item_id: int, since: float) -> bool:
    try:
        if MITM_LOG.stat().st_mtime < since - 1:
            return False
        tail = MITM_LOG.read_text(encoding="utf-8", errors="replace")[-12000:]
    except Exception:
        return False
    return f"item_id={item_id}" in tail and "pdp/get" in tail


def main() -> int:
    urls = RUNTIME / "tk_br_rows23_120_part1_urls.txt"
    pending = [(s, i) for s, i, _ in load_targets(urls) if not _save.already_saved(s, i)][:3]
    if len(pending) < 3:
        _log("미저장 대상이 3건 미만")
        return 2
    rec = ensure_device()
    serial = rec["serial"]
    install_system_ca(serial)
    up = (RUNTIME / "upstream.txt").read_text(encoding="utf-8").strip()
    mitm = start_mitm(DEFAULT_PORT, upstream=up)
    try:
        setup_reverse_proxy(serial, DEFAULT_PORT)
        _log("PROBE 어제방식 — 재기동 1회, BACK 없음, VIEW만 연속")
        _warm_app(serial, DEFAULT_PORT, 8.0)
        for n, (sid, iid) in enumerate(pending, 1):
            _log(f"PROBE {n}/3 VIEW i.{sid}.{iid} (BACK 없음)")
            t0 = time.time()
            _view(serial, f"https://shopee.com.br/product/{sid}/{iid}")
            hit = False
            while time.time() - t0 < 12.0:
                if mitm_has(iid, t0):
                    hit = True
                    break
                time.sleep(0.2)
            _log(
                f"PROBE {n}  {'pdp/get 있음' if hit else 'pdp/get 없음'}  "
                f"{time.time() - t0:.1f}s"
            )
            time.sleep(1.2)
    finally:
        stop_mitm(mitm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
