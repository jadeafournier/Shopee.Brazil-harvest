# -*- coding: utf-8 -*-
"""무로그인 URL 수확 — SPC_U(로그인) 제거, 기기 인증서만으로 검색/샵 표면 크롤.

검증(_br_search_nologin.py): 무로그인 검색은 traffic/captcha 0 으로 상품 목록을 반환.
계정 연동 캡차가 사라지므로 대규모 URL 발굴이 지속 가능하다.
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

import _br_url_harvest as H  # noqa: E402
from _br_probe import BR_TEST, parse_br_test  # noqa: E402
from br_cookies import normalize_br_cookies, enforce_br_locale  # noqa: E402


def _log(m): print(m, flush=True)


def _device_jar() -> dict:
    ck, _, _ = parse_br_test(BR_TEST)
    ck = enforce_br_locale(normalize_br_cookies(ck, fresh_cert=False))
    return {k: v for k, v in ck.items() if k != "SPC_U" and v}


def main() -> int:
    if sys.platform == "win32":
        try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    ap = argparse.ArgumentParser(description="Login-less BR URL harvest (device-cert only)")
    ap.add_argument("--target", type=int, default=10000)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--max-rounds", type=int, default=200)
    args = ap.parse_args()

    jar = _device_jar()
    if not jar.get("AC_CERT_D"):
        _log("기기 인증서(AC_CERT_D) 없음 — 중단.")
        return 1
    _log(f"무로그인 URL 수확 시작 (SPC_U 제거, AC_CERT_D=yes) target={args.target:,}")

    rounds = 0
    while rounds < args.max_rounds:
        rounds += 1
        code = H.harvest(
            args.target,
            resume=True,
            fast=args.fast,
            cookies_override=dict(jar),
            account_id="device_nologin",
            shift_pages=0,
        )
        _log(f"[round {rounds}] harvest rc={code}")
        if code == 0:
            _log("목표 도달/소진 — 완료.")
            break
        if code == 1:
            _log("오류 반환 — 중단.")
            break
        if code == 2:
            # 무로그인에선 드물지만, 발생 시 짧게 쉬고 재개(계정 소모 아님).
            _log("일시적 위험 신호 — 3분 휴식 후 재개.")
            time.sleep(180)
            continue
        # code == 3: 시프트 완료 → 즉시 재개
        time.sleep(10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
