# -*- coding: utf-8 -*-
"""ADB 딥링크 드라이버 — URL 목록을 에뮬 Shopee 앱으로 순회시켜 get_pc 유발.

동작:
  각 상품 URL 을 `am start -a VIEW -d <url>` 인텐트로 앱에 열게 하면, 앱이
  PDP 를 로드하며 스스로 서명한 get_pc 를 쏜다. 그 응답은 mitm_capture 애드온이
  저장한다. 이 드라이버는 "사람이 앱에서 상품을 하나씩 여는" 역할만 한다.

전제:
  * 에뮬레이터가 adb 로 연결됨 (예: 127.0.0.1:5555)
  * 에뮬 시스템 프록시가 mitmproxy 를 가리키고 mitmproxy CA 설치됨
  * mitm_capture.py 애드온이 별도 mitmdump 로 실행 중

실행:
  .venv/Scripts/python.exe -m br_emulator.adb_driver --urls br_active_product_urls_100k.txt
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

_DIR = Path(__file__).resolve().parent
for _p in (str(_DIR), str(_DIR.parent), str(_DIR.parent.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from br_emulator import _save  # noqa: E402

STATE = _save.OUT_DIR / "adb_driver_state.json"
SHOPEE_PKG_CANDIDATES = ("com.shopee.br", "com.shopee.id", "com.beeasy.shopee.br")

# 기본은 빠른 세션(저장 즉시 다음). 사람형 페이싱은 BR_EMU_FAST=0.
FAST = os.environ.get("BR_EMU_FAST", "1") != "0"
DWELL_S = (0.15, 0.35) if FAST else (3.5, 8.0)
LONG_REST_EVERY = (10**9, 10**9) if FAST else (40, 70)
LONG_REST_S = (1.0, 1.0) if FAST else (45.0, 120.0)
SAVE_POLL_S = 10.0 if FAST else 9.0


def _log(msg: str) -> None:
    print(msg, flush=True)


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"idx": 0, "opened": 0, "saved_seen": 0}


def _save_state(st: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def _connect(serial: str | None):
    import adbutils
    from br_emulator import vmos_cloud  # noqa: E402

    client = adbutils.AdbClient()
    serial = serial or os.environ.get("ANDROID_SERIAL")
    if not serial:
        try:
            rec = vmos_cloud.ensure_connected()
            serial = rec.get("serial")
        except Exception as exc:
            _log(f"  VMOS 자동연결 실패: {exc}")
    if serial:
        try:
            client.connect(serial, timeout=5.0)
        except Exception:
            pass
        return client.device(serial=serial)
    devs = client.device_list()
    if not devs:
        for hostport in ("127.0.0.1:5555", "127.0.0.1:62001", "127.0.0.1:21503"):
            try:
                client.connect(hostport, timeout=3.0)
            except Exception:
                pass
        devs = client.device_list()
    if not devs:
        raise RuntimeError("연결된 adb 기기가 없습니다. VMOS Cloud를 켜고 python -m br_emulator.vmos_cloud connect 하세요.")
    return devs[0]


def _detect_pkg(dev) -> str | None:
    try:
        out = dev.shell("pm list packages")
    except Exception:
        return None
    installed = {ln.replace("package:", "").strip() for ln in (out or "").splitlines()}
    for pkg in SHOPEE_PKG_CANDIDATES:
        if pkg in installed:
            return pkg
    for pkg in installed:
        if "shopee" in pkg:
            return pkg
    return None


def _open_deeplink(dev, url: str, pkg: str | None) -> None:
    q = url.replace("&", "\\&")
    if pkg == "com.shopee.br":
        cmd = (
            f'am start -n {pkg}/com.shopee.app.ui.proxy.ProxyActivity '
            f'-a android.intent.action.VIEW '
            f'-c android.intent.category.BROWSABLE -d "{q}"'
        )
    else:
        cmd = f'am start -a android.intent.action.VIEW -d "{q}"'
        if pkg:
            cmd += f" {pkg}"
    try:
        dev.shell(cmd)
    except Exception as e:
        _log(f"  인텐트 실패: {e}")


def _human_scroll(dev) -> None:
    try:
        x = random.randint(400, 700)
        y0 = random.randint(1100, 1500)
        y1 = random.randint(400, 700)
        dev.shell(f"input swipe {x} {y0} {x} {y1} {random.randint(300, 600)}")
    except Exception:
        pass


def run(urls_path: Path, *, serial: str | None, limit: int, resume: bool) -> int:
    urls = _save.load_urls(urls_path, limit=0, skip_saved=True)
    if not urls:
        _log("처리할 URL 이 없습니다(이미 전부 저장되었거나 목록이 비어있음).")
        return 0

    dev = _connect(serial)
    pkg = _detect_pkg(dev)
    _log("=" * 68)
    _log("【ADB 딥링크 드라이버】 에뮬 Shopee 앱 순회")
    _log(f"  기기: {getattr(dev, 'serial', '?')}")
    _log(f"  Shopee 패키지: {pkg or '자동감지 실패(인텐트가 기본앱으로 열림)'}")
    _log(f"  대상 URL: {len(urls):,} (미저장분)")
    _log(f"  주의: mitm_capture 애드온과 프록시/CA 설정이 되어 있어야 저장됩니다.")
    _log("=" * 68)

    st = _load_state() if resume else {"idx": 0, "opened": 0, "saved_seen": 0}
    start = st.get("idx", 0) if resume else 0
    opened = st.get("opened", 0)
    next_rest = random.randint(*LONG_REST_EVERY)
    consec_nosave = 0
    processed = 0

    for i in range(start, len(urls)):
        url = urls[i]
        ids = _save.parse_ids(url)
        if not ids:
            continue
        sid, iid = ids
        if _save.already_saved(sid, iid):
            continue

        _open_deeplink(dev, url, pkg)
        opened += 1
        processed += 1
        if not FAST:
            time.sleep(random.uniform(1.5, 2.5))
            if random.random() < 0.6:
                _human_scroll(dev)

        # get_pc 캡처(mitm 저장) 폴링
        deadline = time.time() + SAVE_POLL_S
        saved = False
        while time.time() < deadline:
            if _save.already_saved(sid, iid):
                saved = True
                break
            time.sleep(0.6)

        if saved:
            consec_nosave = 0
            total = _save.count_saved()
            _log(f"[{i+1}/{len(urls)}] ✅ i.{sid}.{iid} 저장 (누적 {total:,})")
        else:
            consec_nosave += 1
            _log(f"[{i+1}/{len(urls)}] … i.{sid}.{iid} 미저장(로딩 지연/앱 검증?) 연속 {consec_nosave}")
            if consec_nosave in (10, 25, 50):
                _log("  ⚠ 연속 미저장 — mitmdump 실행/프록시/CA/로그인 상태를 확인하세요.")

        st.update({"idx": i + 1, "opened": opened, "saved_seen": _save.count_saved()})
        _save_state(st)

        # 사람형 체류 + 주기적 긴 휴식
        time.sleep(random.uniform(*DWELL_S))
        if opened >= next_rest:
            rest = random.uniform(*LONG_REST_S)
            _log(f"  🛌 긴 휴식 {rest:.0f}s (앱/계정 과열 방지)")
            time.sleep(rest)
            next_rest = opened + random.randint(*LONG_REST_EVERY)

        if limit and processed >= limit:
            _log(f"limit {limit} 도달 — 중단.")
            break

    _log(f"\n완료: 이번 실행 오픈 {processed} | 총 저장 {_save.count_saved():,}")
    return 0


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="ADB 딥링크 드라이버(에뮬 Shopee 앱 순회)")
    ap.add_argument("--urls", type=Path,
                    default=_save.CLIENT4 / "br_active_product_urls_100k.txt")
    ap.add_argument("--serial", default=None, help="adb 기기 시리얼/호스트:포트")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()
    return run(args.urls, serial=args.serial, limit=args.limit, resume=not args.no_resume)


if __name__ == "__main__":
    raise SystemExit(main())
