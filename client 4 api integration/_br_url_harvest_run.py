# -*- coding: utf-8 -*-
"""Auto-run BR URL harvest until target — multi-worker + auto-restart.

Launches N parallel browser workers (keyword-sharded), restarts any that die,
and exits only when the URL file reaches --target.

Usage:
  python "_br_url_harvest_run.py" --target 100000 --workers 3 --fast
"""
from __future__ import annotations

import argparse
import msvcrt
import os
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
ROOT = DIR.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
HARVEST = DIR / "_br_url_harvest.py"
OUT = DIR / "br_active_product_urls_100k.txt"
LOG_DIR = DIR / "br_url_harvest_logs"
SUPERVISOR_LOCK = DIR / "br_url_harvest_supervisor.lock"


def _count() -> int:
    if not OUT.is_file():
        return 0
    return sum(1 for ln in OUT.read_text(encoding="utf-8").splitlines() if ln.strip())


def _log(msg: str) -> None:
    print(msg, flush=True)


def _acquire_supervisor_lock() -> object | None:
    """Ensure only one harvest supervisor runs (duplicate supervisors stall on file lock)."""
    SUPERVISOR_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = open(SUPERVISOR_LOCK, "a+b")
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()).encode("ascii"))
        fh.flush()
        return fh
    except OSError:
        fh.close()
        return None


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=100_000)
    # Default 1: other teams' scale uses many *accounts*, not many browsers per cookie.
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--fast", action="store_true", default=True)
    ap.add_argument("--no-fast", action="store_true")
    ap.add_argument("--poll", type=float, default=30.0)
    args = ap.parse_args()
    fast = not args.no_fast
    workers = max(1, min(6, int(args.workers)))
    py = str(PY if PY.is_file() else sys.executable)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    lock_fh = _acquire_supervisor_lock()
    if lock_fh is None:
        _log("Another harvest supervisor is already running — exit.")
        _log(f"  lock={SUPERVISOR_LOCK}")
        return 1

    _log("=" * 70)
    _log("【URL 수확 감독자】 목표까지 자동 재시작 · 병렬 워커")
    _log(f"  목표={args.target:,} | 워커={workers} | 고속={fast}")
    _log(f"  출력={OUT}")
    _log("=" * 70)

    procs: dict[int, subprocess.Popen] = {}
    logs: dict[int, object] = {}
    restarts = [0] * workers

    def spawn(wid: int) -> subprocess.Popen:
        log_path = LOG_DIR / f"worker_{wid}.log"
        lf = open(log_path, "a", encoding="utf-8", errors="replace")
        logs[wid] = lf
        cmd = [
            py, str(HARVEST),
            "--target", str(args.target),
            "--worker-id", str(wid),
            "--workers", str(workers),
        ]
        if fast:
            cmd.append("--fast")
        _log(f"  워커 시작 {wid + 1}/{workers} → {log_path.name} (재시작={restarts[wid]})")
        return subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=lf,
            stderr=subprocess.STDOUT,
            text=True,
        )

    for wid in range(workers):
        procs[wid] = spawn(wid)

    last_count = _count()
    last_report = time.time()
    try:
        while True:
            n = _count()
            if n >= args.target:
                _log(f"\n✅ 목표 달성: {n:,} / {args.target:,}")
                break

            for wid, proc in list(procs.items()):
                code = proc.poll()
                if code is None:
                    continue
                _log(f"  워커 {wid + 1} 종료 (code={code})")
                try:
                    logs[wid].close()
                except Exception:
                    pass
                if _count() >= args.target:
                    break
                restarts[wid] += 1
                if restarts[wid] > 100:
                    _log(f"  워커 {wid + 1} 재시작 한도 초과 — 제외")
                    del procs[wid]
                    continue
                # code=2 → traffic/captcha: STOP (never hammer a blocked cookie).
                if code == 2:
                    _log(
                        f"  워커 {wid + 1} traffic/captcha — 전체 중단. "
                        "BR TEST.txt 에 새 로그인 쿠키를 넣은 뒤 다시 실행하세요."
                    )
                    for wid2, proc2 in list(procs.items()):
                        try:
                            proc2.terminate()
                        except Exception:
                            pass
                    procs.clear()
                    return 2
                time.sleep(5)
                procs[wid] = spawn(wid)

            if not procs:
                _log("모든 워커가 중지되었습니다.")
                return 1

            now = time.time()
            if now - last_report >= args.poll:
                n = _count()
                delta = n - last_count
                rate = delta / max(0.001, (now - last_report) / 60.0)
                eta_min = (args.target - n) / rate if rate > 1 else float("inf")
                eta = f"{eta_min:.0f}분" if eta_min < 1e6 else "?"
                _log(
                    f"진행 {n:,}/{args.target:,} | +{delta}/{args.poll:.0f}초 "
                    f"| ~{rate:.0f}건/분 | ETA~{eta} | 워커={len(procs)}"
                )
                last_count = n
                last_report = now

            time.sleep(2.0)

    except KeyboardInterrupt:
        _log("중단됨 — 워커 종료 중...")
    finally:
        for wid, proc in list(procs.items()):
            try:
                proc.terminate()
            except Exception:
                pass
        time.sleep(2)
        for wid, proc in list(procs.items()):
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
            try:
                logs[wid].close()
            except Exception:
                pass
        try:
            msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        try:
            lock_fh.close()
        except Exception:
            pass

    final = _count()
    _log(f"최종 수집: {final:,}건")
    return 0 if final >= args.target else 1


if __name__ == "__main__":
    raise SystemExit(main())
