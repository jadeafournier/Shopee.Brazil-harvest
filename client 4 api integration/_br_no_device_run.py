# -*- coding: utf-8 -*-
"""BR 데이터 추출 — iOS 기기 없이 가능한 모든 경로를 순서대로 실행.

기기/앱 서명 없이 성공 가능한 방법만 사용합니다:
  1) URL 수확        — search/shop 브라우저 인터셉트
  2) 리스팅 카드     — 제목·가격·판매량·평점 (대량, PDP 불필요)
  3) 브라우저 PDP    — get_pc → get_rw → item/get → HTML partial
  4) 장기 시프트     — 계정 보호 쿼터 + 쿨다운

하지 않음: CapSolver, x-sap-sec/AC_CERT 위조, 기기 없는 모바일 curl (항상 90309999)

Usage:
  python "_br_no_device_run.py" --status
  python "_br_no_device_run.py" --all --url-target 100000 --card-target 50000
  python "_br_no_device_run.py" --phase cards
  python "_br_no_device_run.py" --phase pdp --pdp-limit 40
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
ROOT = DIR.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"

URL_FILE = DIR / "br_active_product_urls_100k.txt"
CARD_FILE = DIR / "br_listing_cards.jsonl"
PDP_OUT = DIR / "br_batch_output_100k"
SUMMARY = DIR / "br_no_device_summary.json"


def _py() -> str:
    return str(PY if PY.is_file() else sys.executable)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())


def _count_pdp(out_dir: Path) -> int:
    if not out_dir.is_dir():
        return 0
    return sum(1 for p in out_dir.glob("output_*.json") if p.is_file())


def _run(args: list[str], *, label: str) -> int:
    _log(f"\n>>> {label}")
    _log(f"    {' '.join(args)}")
    proc = subprocess.run(args, cwd=str(ROOT))
    _log(f"<<< {label} exit={proc.returncode}")
    return int(proc.returncode)


def cmd_status() -> int:
    urls = _count_lines(URL_FILE)
    cards = _count_lines(CARD_FILE)
    pdps = _count_pdp(PDP_OUT)
    legacy = _count_pdp(DIR / "br_batch_output_1000")
    _log("=" * 70)
    _log("【BR 무기기 추출 현황】 iOS 없이 가능한 경로만")
    _log(f"  ① 상품 URL     : {urls:,}  → {URL_FILE.name}")
    _log(f"  ② 리스팅 카드  : {cards:,}  → {CARD_FILE.name}")
    _log(f"  ③ 풀 PDP(100k) : {pdps:,}  → {PDP_OUT.name}/")
    _log(f"  ③ 풀 PDP(1k)   : {legacy:,}  → br_batch_output_1000/")
    _log("-" * 70)
    _log("가용 방법:")
    _log("  A browser_get_pc   — SPA 서명 인터셉트 (풀 데이터, 검증됨)")
    _log("  B browser_get_rw   — SPA가 쏘면 폴백으로 저장")
    _log("  C browser_item_get — SPA가 쏘면 폴백으로 저장")
    _log("  D html_partial     — OG/JSON-LD (부분 필드)")
    _log("  E listing_card     — search/recommend 카드 (대량)")
    _log("에뮬레이터 경로(실기기 대체):")
    _log("  F emulator_get_pc — 에뮬 앱이 스스로 서명 → mitm 응답 캡처 (90309999 우회)")
    emu = _count_pdp(DIR / "br_emulator_output")
    _log(f"      저장 {emu:,} → br_emulator_output/  (가이드: br_emulator/README_EMULATOR_KO.md)")
    _log("  실행: python \"br_emulator\\run_emulator.py\" check|capture|fleet")
    _log("=" * 70)
    return 0


def phase_urls(target: int, workers: int) -> int:
    return _run(
        [
            _py(), str(DIR / "_br_url_harvest_run.py"),
            "--target", str(target),
            "--workers", str(workers),
            "--fast",
            "--poll", "30",
        ],
        label=f"URL harvest → {target:,}",
    )


def phase_cards(target: int) -> int:
    return _run(
        [
            _py(), str(DIR / "_br_listing_cards.py"),
            "--target", str(target),
            "--fast",
        ],
        label=f"Listing cards → {target:,}",
    )


def phase_pdp(limit: int, shift_quota: int) -> int:
    PDP_OUT.mkdir(parents=True, exist_ok=True)
    # Prefer 100k URL list; fall back to 1000 seed.
    urls = URL_FILE if URL_FILE.is_file() else DIR / "br_active_product_urls_1000.txt"
    cmd = [
        _py(), str(DIR / "_br_long_crawl.py"),
        "--run-remaining",
        "--primary-only",
        "--urls", str(urls),
        "--out", str(PDP_OUT),
        "--shift-quota", str(shift_quota),
    ]
    if limit > 0:
        # long_crawl may not have --limit; use batch directly for capped smoke
        cmd = [
            _py(), str(DIR / "_br_batch_crawl.py"),
            "--urls", str(urls),
            "--out", str(PDP_OUT),
            "--limit", str(limit),
            "--shift-quota", str(shift_quota),
        ]
    return _run(cmd, label=f"Browser PDP multi-method (quota={shift_quota})")


def _write_summary(extra: dict | None = None) -> None:
    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "urls": _count_lines(URL_FILE),
        "listing_cards": _count_lines(CARD_FILE),
        "pdp_full_100k": _count_pdp(PDP_OUT),
        "pdp_full_1000": _count_pdp(DIR / "br_batch_output_1000"),
        "methods": [
            "browser_get_pc",
            "browser_get_rw",
            "browser_item_get",
            "html_partial",
            "listing_card",
        ],
        "blocked_without_device": ["ios_item_get", "ios_get_rw", "ios_get_pc", "curl_get_pc"],
    }
    if extra:
        payload.update(extra)
    SUMMARY.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BR no-device multi-method extract")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--all", action="store_true", help="run urls → cards → pdp pipeline")
    ap.add_argument(
        "--phase",
        choices=("urls", "cards", "pdp", "emulator"),
        help="run a single phase",
    )
    ap.add_argument("--url-target", type=int, default=100_000)
    ap.add_argument("--card-target", type=int, default=50_000)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--pdp-limit", type=int, default=0, help="0 = long crawl remaining")
    ap.add_argument("--shift-quota", type=int, default=40)
    args = ap.parse_args()

    if args.status or (not args.all and not args.phase):
        return cmd_status()

    codes: dict[str, int] = {}
    t0 = time.time()
    _log("=" * 70)
    _log("【BR 무기기 멀티메서드 추출】")
    _log("  iOS 기기 없음 → 브라우저 SAP + 리스팅 카드만 사용")
    _log("=" * 70)

    if args.all or args.phase == "urls":
        if _count_lines(URL_FILE) < args.url_target:
            codes["urls"] = phase_urls(args.url_target, args.workers)
        else:
            _log(f"URL 목표 이미 충족: {_count_lines(URL_FILE):,}")
            codes["urls"] = 0

    if args.all or args.phase == "cards":
        if _count_lines(CARD_FILE) < args.card_target:
            codes["cards"] = phase_cards(args.card_target)
        else:
            _log(f"카드 목표 이미 충족: {_count_lines(CARD_FILE):,}")
            codes["cards"] = 0

    if args.all or args.phase == "pdp":
        codes["pdp"] = phase_pdp(args.pdp_limit, args.shift_quota)

    if args.phase == "emulator":
        codes["emulator"] = _run(
            [_py(), str(DIR / "br_emulator" / "run_emulator.py"), "capture"],
            label="Emulator capture (mitm + adb deeplink)",
        )

    _write_summary({"phase_exit_codes": codes, "elapsed_s": round(time.time() - t0, 1)})
    cmd_status()
    # Success if any productive phase completed without hard crash, or status ok.
    if any(c not in (0, 1, 2) for c in codes.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
