# -*- coding: utf-8 -*-
"""리스팅 카드 연속 수확 · 계정 회전 (쿠키 보호형).

목적: 실기기 없이, 브라우저가 서명하는 search/recommend 리스팅 API 응답에서
상품 데이터 카드(제목·가격·판매량·평점·상점·이미지)를 목표 수량까지 자율 수집.

쿠키 보호:
  * 한 번에 브라우저 1개 / 쿠키 1개
  * 계정당 짧은 시프트(카드 예산)로 과열 전에 미리 회전
  * 캡차/traffic → 해당 계정만 쿨다운, 나머지로 계속
  * 모두 쿨다운이면 가장 빨리 풀리는 시각까지 대기 후 자동 재개

Usage:
  python "_br_cards_rotate.py" --target 10000
  python "_br_cards_rotate.py" --status
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
ROOT = DIR.parent
for p in (str(ROOT), str(ROOT / "shopee_getpc_crawler"), str(ROOT / "client 4 api integration")):
    if p not in sys.path:
        sys.path.insert(0, p)

import _br_listing_cards as C  # noqa: E402
from br_account_pool import AccountPool, load_br_test_cookies  # noqa: E402

SHIFT_CARDS = 1200          # 계정당 시프트 카드 예산(과열 전 회전)
REST_AFTER_BUDGET_S = 6 * 60
MAX_IDLE_SLEEP_S = 20 * 60


def _log(msg: str) -> None:
    print(msg, flush=True)


def _account_cookies(pool: AccountPool, aid: str, primary: str | None) -> dict:
    if aid == primary:
        ck = load_br_test_cookies()
        if ck.get("SPC_U"):
            return ck
    return pool.load_cookies(aid)


def _soft_rest(pool: AccountPool, aid: str, seconds: int) -> None:
    rec = pool._acc(aid)
    rec["cooldown_until"] = time.time() + seconds
    rec["shift_ok"] = int(rec.get("shift_ok") or 0) + 10
    rec["last_status"] = "resting"
    pool.save()


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BR 리스팅 카드 연속 수확(계정 회전)")
    ap.add_argument("--target", type=int, default=10_000)
    ap.add_argument("--shift-cards", type=int, default=SHIFT_CARDS)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--no-fast", action="store_true")
    args = ap.parse_args()

    if args.status:
        _log(f"listing cards: {C._count_cards():,} / {args.target:,}")
        return 0

    fast = not args.no_fast
    pool = AccountPool()
    ck = load_br_test_cookies()
    if ck.get("SPC_U"):
        aid = pool.register_br_test()
        _log(f"신규 쿠키 등록(primary): {aid} SPC_U={ck.get('SPC_U')}")

    _log("=" * 70)
    _log("【BR 리스팅 카드 연속 수확】 쿠키 보호 · 계정 회전")
    _log(f"  목표={args.target:,} 카드 | 시프트 카드={args.shift_cards} | 계정={len(pool.summary())}")
    _log("=" * 70)

    consecutive_idle = 0
    while True:
        have = C._count_cards()
        if have >= args.target:
            _log(f"\n✅ 목표 달성: {have:,} / {args.target:,} 카드")
            return 0

        primary = pool.state.get("primary_account")
        pool.reset_quota_exhausted()
        aid = pool.pick()
        if not aid:
            wait = pool.next_available_wait_s()
            wait = min(max(wait, 60), MAX_IDLE_SLEEP_S)
            consecutive_idle += 1
            _log(f"모든 계정 쿨다운 — {int(wait/60)+1}분 대기 (idle #{consecutive_idle}, 현재 {have:,})")
            time.sleep(wait + 5)
            continue

        consecutive_idle = 0
        cookies = _account_cookies(pool, aid, primary)
        if not cookies.get("SPC_U"):
            _soft_rest(pool, aid, REST_AFTER_BUDGET_S)
            continue

        _log("-" * 70)
        _log(f"▶ 시프트: {aid} (SPC_U={cookies.get('SPC_U')}) | 현재 {have:,} 카드")
        try:
            code = C.harvest_cards(
                args.target, fast=fast,
                cookies_override=cookies, account_id=aid,
                shift_cards=args.shift_cards,
            )
        except Exception as e:
            _log(f"  시프트 예외: {e!r}")
            code = 1

        gained = C._count_cards() - have
        if code == 0:
            if C._count_cards() >= args.target:
                _log(f"✅ 목표 달성: {C._count_cards():,}")
                return 0
            _log(f"  키워드 소진(+{gained}) — 계정 휴식 후 계속")
            _soft_rest(pool, aid, REST_AFTER_BUDGET_S)
        elif code == 2:
            reason = C._load_state().get("stopped_reason") or "traffic_error"
            rzn = reason if reason in ("captcha", "traffic_error") else "traffic_error"
            pool.mark_risk(aid, rzn, cookies)
            _log(f"⚠ {aid}: {rzn} (+{gained}) — 계정 쿨다운, 다음 계정 회전")
        elif code == 3:
            pool.record_ok(aid, cookies)
            _soft_rest(pool, aid, REST_AFTER_BUDGET_S)
            _log(f"↻ {aid}: 시프트 예산 도달 (+{gained}) — 휴식 후 회전")
        else:
            _log(f"  {aid}: 코드 {code} (+{gained}) — 짧은 휴식 후 회전")
            _soft_rest(pool, aid, REST_AFTER_BUDGET_S)

        time.sleep(6)


if __name__ == "__main__":
    raise SystemExit(main())
