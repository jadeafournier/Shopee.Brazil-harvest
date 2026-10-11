# -*- coding: utf-8 -*-
"""연속 VIEW 후보 1~3. HTTP 위조 없음. 프로세스는 유지."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from br_emulator import _save
from br_emulator.vmos_app_capture import (
    DEFAULT_PORT,
    HOME_ACT,
    MITM_LOG,
    PROXY_ACT,
    RUNTIME,
    SHOPEE_PKG,
    _log,
    _sh,
    _warm_app,
    ensure_device,
    install_system_ca,
    load_targets,
    setup_reverse_proxy,
    start_mitm,
    stop_mitm,
)

# NEW_TASK|MULTIPLE_TASK
FLAG_NEW_MULTI = "0x18000000"
# NEW_TASK|MULTIPLE_TASK|NEW_DOCUMENT
FLAG_NEW_DOC = "0x18080000"


def snap() -> int:
    try:
        return MITM_LOG.stat().st_size
    except OSError:
        return 0


def mitm_hit(item_id: int, offset: int) -> str:
    try:
        data = MITM_LOG.read_text(encoding="utf-8", errors="replace")[offset:]
    except Exception:
        return "no-log"
    hit = False
    blocked = False
    for ln in data.splitlines():
        if "pdp/get" not in ln or str(item_id) not in ln:
            continue
        hit = True
        if "err=90309999" in ln:
            blocked = True
    if blocked:
        return "418"
    if hit:
        return "pdp"
    return "none"


def wait_hit(item_id: int, offset: int, sec: float = 12.0) -> tuple[str, float]:
    t0 = time.time()
    last = "none"
    while time.time() - t0 < sec:
        last = mitm_hit(item_id, offset)
        if last in ("pdp", "418"):
            return last, time.time() - t0
        time.sleep(0.2)
    return last, time.time() - t0


def view(serial: str, url: str, flags: str = "") -> str:
    q = url.replace("&", "\\&")
    extra = f" -f {flags}" if flags else ""
    return _sh(
        serial,
        f'am start{extra} -n {PROXY_ACT} '
        f'-a android.intent.action.VIEW '
        f'-c android.intent.category.BROWSABLE -d "{q}"',
    )


def product_url(sid: int, iid: int) -> str:
    return f"https://shopee.com.br/product/{sid}/{iid}"


def run_pair(serial: str, items: list, label: str, before) -> list[str]:
    results = []
    for n, (sid, iid) in enumerate(items, 1):
        before(serial, n)
        off = snap()
        t0 = time.time()
        out = view(serial, product_url(sid, iid))
        _log(f"  {label} {n} i.{sid}.{iid} am={(out or '')[:120]}")
        hit, dt = wait_hit(iid, off)
        _log(f"  결과 {hit} {dt:.1f}s")
        results.append(hit)
        time.sleep(0.8)
    return results


def main() -> int:
    pending = [
        (s, i)
        for s, i, _ in load_targets(RUNTIME / "tk_br_rows23_120_part1_urls.txt")
        if not _save.already_saved(s, i)
    ][:8]
    if len(pending) < 8:
        _log(f"미저장 부족 {len(pending)}")
        return 2
    rec = ensure_device()
    serial = rec["serial"]
    install_system_ca(serial)
    up = (RUNTIME / "upstream.txt").read_text(encoding="utf-8").strip()
    mitm = start_mitm(DEFAULT_PORT, upstream=up)
    summary = []
    try:
        setup_reverse_proxy(serial, DEFAULT_PORT)

        _log("=== 1 런처로 나간 뒤 VIEW ===")
        _warm_app(serial, DEFAULT_PORT, 8.0)

        def leave_home(_serial: str, _n: int) -> None:
            _sh(_serial, "input keyevent KEYCODE_HOME")
            time.sleep(1.0)

        r1 = run_pair(serial, pending[0:2], "HOME+VIEW", leave_home)
        summary.append(("1 HOME+VIEW", r1))

        if r1.count("pdp") < 2:
            _log("=== 1b 설정 앱으로 나간 뒤 VIEW ===")
            _sh(serial, "am start -a android.settings.SETTINGS")
            time.sleep(1.2)

            def leave_settings(_serial: str, _n: int) -> None:
                _sh(_serial, "am start -a android.settings.SETTINGS")
                time.sleep(1.2)

            r1b = run_pair(serial, pending[2:4], "SETTINGS+VIEW", leave_settings)
            summary.append(("1b SETTINGS+VIEW", r1b))
        else:
            pending = pending[0:2] + pending[4:]

        _log("=== 2 NEW_TASK|MULTIPLE_TASK ===")
        _warm_app(serial, DEFAULT_PORT, 6.0)

        def no_leave(_serial: str, _n: int) -> None:
            return

        r2 = []
        for n, (sid, iid) in enumerate(pending[4:6], 1):
            off = snap()
            out = view(serial, product_url(sid, iid), FLAG_NEW_MULTI)
            _log(f"  MULTI {n} i.{sid}.{iid} am={(out or '')[:120]}")
            hit, dt = wait_hit(iid, off)
            _log(f"  결과 {hit} {dt:.1f}s")
            r2.append(hit)
            time.sleep(0.8)
        summary.append(("2 MULTIPLE_TASK", r2))

        if r2.count("pdp") < 2:
            _log("=== 2b NEW_DOCUMENT ===")
            r2b = []
            for n, (sid, iid) in enumerate(pending[6:8], 1):
                off = snap()
                out = view(serial, product_url(sid, iid), FLAG_NEW_DOC)
                _log(f"  NEWDOC {n} i.{sid}.{iid} am={(out or '')[:140]}")
                hit, dt = wait_hit(iid, off)
                _log(f"  결과 {hit} {dt:.1f}s")
                r2b.append(hit)
                time.sleep(0.8)
            summary.append(("2b NEW_DOCUMENT", r2b))

        _log("=== 3 홈 URL 경유 후 상품 VIEW ===")
        _warm_app(serial, DEFAULT_PORT, 6.0)
        extra = [
            (s, i)
            for s, i, _ in load_targets(RUNTIME / "tk_br_rows23_120_part1_urls.txt")
            if not _save.already_saved(s, i)
        ]
        used = {iid for _, iid in pending}
        extra = [(s, i) for s, i in extra if i not in used][:2]
        r3 = []
        for n, (sid, iid) in enumerate(extra, 1):
            view(serial, "https://shopee.com.br/")
            time.sleep(2.5)
            off = snap()
            out = view(serial, product_url(sid, iid))
            _log(f"  HOMEURL {n} i.{sid}.{iid} am={(out or '')[:120]}")
            hit, dt = wait_hit(iid, off)
            _log(f"  결과 {hit} {dt:.1f}s")
            r3.append(hit)
            time.sleep(0.8)
        summary.append(("3 HOMEURL+VIEW", r3))

        _log("=== 요약 ===")
        for name, rows in summary:
            ok = rows.count("pdp")
            _log(f"  {name}: {rows}  ({ok}/{len(rows)} pdp)")
    finally:
        stop_mitm(mitm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
