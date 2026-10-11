# -*- coding: utf-8 -*-
"""연속 수확 · 계정 회전 감독자 (쿠키 보호형).

핵심 아이디어 (다른 팀의 일 10만 방식과 동일):
  * 한 번에 브라우저 1개 / 쿠키 1개만 사용 (병렬 금지 → traffic_error 방지)
  * 짧은 시프트(페이지 예산)로 캡차 임계 이전에 미리 멈춤
  * 소프트 traffic 신호 2회면 캡차 전에 계정을 선제 회전
  * 시프트 후 계정을 잠시 쉬게 하고 다음 준비된 계정으로 회전
  * 캡차/traffic_error 발생 시 해당 계정만 장시간 쿨다운 (나머지는 계속)
  * 모두 쿨다운이면 가장 빨리 풀리는 시각까지 대기 후 자동 재개

이렇게 하면 어떤 쿠키도 과열되지 않고 목표까지 연속 수확이 가능합니다.

Usage:
  python "_br_url_harvest_rotate.py" --target 100000
  python "_br_url_harvest_rotate.py" --status
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
ROOT = DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

import _br_url_harvest as H  # noqa: E402
from br_account_pool import (  # noqa: E402
    AccountPool,
    load_br_test_cookies,
)

# Cookie-safety knobs — tuned so a single account never reaches captcha.
SHIFT_PAGES = 90           # hard page cap per account per shift
SOFT_ROTATE_AT = 2         # rotate after this many soft traffic signals (pre-captcha)
REST_AFTER_BUDGET_S = 8 * 60      # short rest when shift ended on page budget
REST_AFTER_SOFT_S = 25 * 60      # longer rest when account showed heat
MAX_IDLE_SLEEP_S = 20 * 60       # cap on wait when all accounts cooling


def _log(msg: str) -> None:
    print(msg, flush=True)


def _count() -> int:
    return H._count_output()


def _state_reason() -> str | None:
    try:
        return H._load_state().get("stopped_reason")
    except Exception:
        return None


def _account_cookies(pool: AccountPool, aid: str, primary: str | None) -> dict:
    """Primary uses live BR TEST.txt cookies; others use their session jar."""
    if aid == primary:
        ck = load_br_test_cookies()
        if ck.get("SPC_U"):
            return ck
    return pool.load_cookies(aid)


def _soft_rest(pool: AccountPool, aid: str, seconds: int, *, bump_shift: bool = True) -> None:
    """Rest an account WITHOUT a risk penalty so rotation prefers others briefly."""
    rec = pool._acc(aid)
    rec["cooldown_until"] = time.time() + seconds
    if bump_shift:
        # Nudge shift_ok so pick() prefers a fresher account next.
        rec["shift_ok"] = int(rec.get("shift_ok") or 0) + 10
    rec["last_status"] = "resting"
    pool.save()


def cmd_status(pool: AccountPool, target: int) -> int:
    import json
    n = _count()
    _log("=" * 70)
    _log("【BR URL 연속 수확 · 계정 회전 현황】")
    _log(f"  수집 URL: {n:,} / {target:,}")
    ready = 0
    soonest = None
    for row in pool.summary():
        if row["available"]:
            ready += 1
        else:
            cu = row.get("cooldown_until")
            if cu and (soonest is None or cu < soonest):
                soonest = cu
    _log(f"  준비된 계정: {ready} / {len(pool.summary())}")
    if ready == 0 and soonest:
        _log(f"  다음 가용: {soonest}")
    _log("=" * 70)
    _log(json.dumps(pool.summary(), ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BR URL 연속 수확 (계정 회전)")
    ap.add_argument("--target", type=int, default=100_000)
    ap.add_argument("--shift-pages", type=int, default=SHIFT_PAGES)
    ap.add_argument("--soft-rotate-at", type=int, default=SOFT_ROTATE_AT)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--no-fast", action="store_true")
    args = ap.parse_args()

    fast = not args.no_fast
    pool = AccountPool()

    # Register the freshly-updated BR TEST.txt cookie as primary (clears its stale cooldown).
    ck = load_br_test_cookies()
    if ck.get("SPC_U"):
        aid = pool.register_br_test()
        _log(f"신규 쿠키 등록(primary): {aid} SPC_U={ck.get('SPC_U')}")

    if args.status:
        return cmd_status(pool, args.target)

    _log("=" * 70)
    _log("【BR URL 연속 수확 감독자】 쿠키 보호 · 계정 회전")
    _log(f"  목표={args.target:,} | 시프트 페이지={args.shift_pages} | 선제회전={args.soft_rotate_at}")
    _log(f"  계정 수={len(pool.summary())}")
    _log("=" * 70)

    consecutive_idle = 0
    while True:
        n = _count()
        if n >= args.target:
            _log(f"\n✅ 목표 달성: {n:,} / {args.target:,}")
            return 0

        primary = pool.state.get("primary_account")
        # Auto-clear quota-exhausted (non-risk) accounts so rotation keeps flowing.
        pool.reset_quota_exhausted()
        aid = pool.pick()
        if not aid:
            wait = pool.next_available_wait_s()
            if wait <= 0:
                wait = 60
            wait = min(wait, MAX_IDLE_SLEEP_S)
            consecutive_idle += 1
            _log(f"모든 계정 쿨다운 중 — {int(wait/60)+1}분 대기 후 재개 (idle #{consecutive_idle})")
            time.sleep(wait + 5)
            continue

        consecutive_idle = 0
        cookies = _account_cookies(pool, aid, primary)
        if not cookies.get("SPC_U"):
            _log(f"  {aid}: 쿠키 없음 — 건너뜀")
            _soft_rest(pool, aid, REST_AFTER_BUDGET_S, bump_shift=True)
            continue

        _log("-" * 70)
        _log(f"▶ 시프트 시작: {aid} (SPC_U={cookies.get('SPC_U')}) | 현재 {n:,} URL")
        try:
            code = H.harvest(
                args.target,
                fast=fast,
                cookies_override=cookies,
                account_id=aid,
                shift_pages=args.shift_pages,
                soft_rotate_at=args.soft_rotate_at,
            )
        except Exception as e:
            _log(f"  시프트 예외: {e!r}")
            code = 1

        gained = _count() - n
        reason = _state_reason()

        if code == 0:
            _log(f"✅ 목표 달성 또는 소진: {_count():,}")
            if _count() >= args.target:
                return 0
            # exhausted but under target — rest primary, keep rotating
            _soft_rest(pool, aid, REST_AFTER_BUDGET_S)
        elif code == 2:
            # Hard risk — cool this account (captcha 12h / traffic 4h), keep others working.
            rzn = reason if reason in ("captcha", "traffic_error") else "traffic_error"
            pool.mark_risk(aid, rzn, cookies)
            _log(f"⚠ {aid}: {rzn} — 계정 쿨다운. (+{gained} URL) 다음 계정으로 회전.")
        elif code == 3:
            rest = REST_AFTER_SOFT_S if reason == "soft_traffic_preempt" else REST_AFTER_BUDGET_S
            pool.record_ok(aid, cookies)
            _soft_rest(pool, aid, rest, bump_shift=(reason == "soft_traffic_preempt"))
            _log(
                f"↻ {aid}: 시프트 종료({reason}) +{gained} URL — "
                f"{int(rest/60)}분 휴식 후 회전 (쿠키 보호)"
            )
        else:
            _log(f"  {aid}: 코드 {code} (+{gained} URL) — 짧은 휴식 후 회전")
            _soft_rest(pool, aid, REST_AFTER_BUDGET_S)

        # Small gap between shifts (new browser/proxy warmup spacing).
        time.sleep(8)


if __name__ == "__main__":
    raise SystemExit(main())
