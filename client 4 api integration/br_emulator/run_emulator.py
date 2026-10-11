# -*- coding: utf-8 -*-
"""에뮬레이터 수집 통합 CLI — 실기기 없이 Shopee BR 데이터 완주.

서브커맨드:
  check    환경 점검(adb 기기/Shopee 앱/mitmdump/frida/CA 안내)
  capture  mitmdump(서명 응답 캡처) 자동 기동 + adb 딥링크 드라이버 순회  ← 기본 경로
  fleet    Frida 앱 서명 + 차단쿠키 결합 모바일 플릿(서명 확정 후 스케일)
  status   저장 현황

권장 흐름:
  1) python -m br_emulator.run_emulator check
  2) (에뮬 프록시 → PC:8080, mitm CA 설치, Shopee 앱 로그인)
  3) python -m br_emulator.run_emulator capture
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

_DIR = Path(__file__).resolve().parent
for _p in (str(_DIR), str(_DIR.parent), str(_DIR.parent.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from br_emulator import _save  # noqa: E402

VENV_SCRIPTS = _save.ROOT / ".venv" / "Scripts"
MITMDUMP = VENV_SCRIPTS / ("mitmdump.exe" if os.name == "nt" else "mitmdump")
PY = VENV_SCRIPTS / ("python.exe" if os.name == "nt" else "python")
ADDON = _DIR / "mitm_capture.py"


def _log(msg: str) -> None:
    print(msg, flush=True)


def cmd_check() -> int:
    _log("=" * 68)
    _log("【에뮬 환경 점검】")
    ok = True

    # mitmdump
    if MITMDUMP.is_file():
        _log(f"  ✅ mitmdump: {MITMDUMP}")
    else:
        _log("  ❌ mitmdump 없음 — pip install mitmproxy")
        ok = False

    # frida
    try:
        import frida
        _log(f"  ✅ frida 파이썬: {frida.__version__}")
    except Exception:
        _log("  ⚠ frida 파이썬 없음(mitm+adb 경로는 불필요, fleet 경로만 필요)")

    # adbutils + device
    try:
        import adbutils
        client = adbutils.AdbClient()
        devs = client.device_list()
        if not devs:
            try:
                from br_emulator import vmos_cloud
                rec = vmos_cloud.ensure_connected()
                if rec.get("serial"):
                    client.connect(rec["serial"], timeout=5.0)
                    _log(f"  ✅ VMOS ADB: {rec['serial']} {rec.get('padName') or ''}")
            except Exception as exc:
                _log(f"  ⚠ VMOS 자동연결: {exc}")
            if not client.device_list():
                for hp in ("127.0.0.1:5555", "127.0.0.1:62001", "127.0.0.1:21503"):
                    try:
                        client.connect(hp, timeout=2.0)
                    except Exception:
                        pass
            devs = client.device_list()
        if devs:
            d = devs[0]
            _log(f"  ✅ adb 기기: {d.serial}")
            try:
                pkgs = d.shell("pm list packages")
                shp = [p for p in pkgs.splitlines() if "shopee" in p]
                if shp:
                    _log(f"  ✅ Shopee 앱: {', '.join(s.replace('package:','') for s in shp)}")
                else:
                    _log("  ❌ Shopee 앱 미설치 — 에뮬에 Shopee BR 앱 설치 후 로그인 필요")
                    ok = False
            except Exception as e:
                _log(f"  ⚠ 패키지 조회 실패: {e}")
        else:
            _log("  ❌ adb 기기 없음 — 에뮬레이터 실행 후 'adb connect <host:port>'")
            ok = False
    except Exception as e:
        _log(f"  ❌ adbutils 오류: {e}")
        ok = False

    _log("-" * 68)
    _log("  프록시/CA 체크리스트(수동):")
    _log("   · 에뮬 Wi-Fi 프록시 = 이 PC IP : 8080")
    _log("   · http://mitm.it 에서 CA 설치(에뮬 브라우저) → 시스템 인증서로 신뢰")
    _log("   · Android 7+ 는 앱이 사용자 CA 를 무시할 수 있음 → 루팅+시스템 CA 또는")
    _log("     objection/frida SSL-unpinning 필요(README 참고)")
    _log("=" * 68)
    _log("점검 통과 — capture 실행 가능" if ok else "일부 항목 미충족 — 위 안내대로 준비 후 재점검")
    return 0 if ok else 1


def cmd_capture(args) -> int:
    if not MITMDUMP.is_file():
        _log("❌ mitmdump 가 없습니다. pip install mitmproxy")
        return 1
    env = dict(os.environ)
    if args.harvest_urls:
        env["BR_EMU_HARVEST_URLS"] = "1"
    if args.inject_cookies:
        env["BR_EMU_INJECT_COOKIES"] = str(args.inject_cookies)
    if args.verbose:
        env["BR_EMU_VERBOSE"] = "1"

    _log(f"▶ mitmdump 기동 (포트 {args.port}) — 애드온 {ADDON.name}")
    mitm = subprocess.Popen(
        [str(MITMDUMP), "-s", str(ADDON), "--listen-port", str(args.port),
         "--set", "stream_large_bodies=2m", "-q"],
        env=env,
    )
    time.sleep(3)
    if mitm.poll() is not None:
        _log("❌ mitmdump 가 즉시 종료됨 — 포트 충돌/설치 확인")
        return 2
    _log("  mitmdump 실행 중. 에뮬 프록시가 이 PC:{}을 가리키는지 확인하세요.".format(args.port))

    try:
        from br_emulator import adb_driver
        rc = adb_driver.run(
            Path(args.urls), serial=args.device, limit=args.limit, resume=not args.no_resume,
        )
    except KeyboardInterrupt:
        rc = 0
    finally:
        _log("mitmdump 종료 중…")
        try:
            mitm.terminate()
            mitm.wait(timeout=8)
        except Exception:
            try:
                mitm.kill()
            except Exception:
                pass
    return rc


def cmd_fleet(args) -> int:
    from br_emulator import emulator_fleet
    return emulator_fleet.run(
        Path(args.urls), workers=args.workers, limit=args.limit, profile=args.profile,
        use_signer=not args.no_signer, attach_pkg=args.attach, device=args.device,
        include_blocked=not args.no_blocked,
    )


def cmd_status() -> int:
    n = _save.count_saved()
    _log("=" * 68)
    _log("【에뮬 수집 현황】")
    _log(f"  저장된 상품: {n:,}")
    _log(f"  출력 폴더: {_save.OUT_DIR}")
    _log(f"  인덱스: {_save.INDEX_JSONL}")
    _log("=" * 68)
    return 0


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    default_urls = _save.CLIENT4 / "br_active_product_urls_100k.txt"
    ap = argparse.ArgumentParser(description="에뮬레이터 Shopee BR 수집 통합 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="환경 점검")

    c = sub.add_parser("capture", help="mitm 캡처 + adb 딥링크 순회(기본 경로)")
    c.add_argument("--urls", default=str(default_urls))
    c.add_argument("--port", type=int, default=8080)
    c.add_argument("--device", default=None)
    c.add_argument("--limit", type=int, default=0)
    c.add_argument("--harvest-urls", action="store_true", help="리스팅에서 URL 추가 수확")
    c.add_argument("--inject-cookies", default=None, help="차단 쿠키 jar(JSON) 경로")
    c.add_argument("--verbose", action="store_true")
    c.add_argument("--no-resume", action="store_true")

    f = sub.add_parser("fleet", help="Frida 서명 + 차단쿠키 모바일 플릿")
    f.add_argument("--urls", default=str(default_urls))
    f.add_argument("--workers", type=int, default=8)
    f.add_argument("--limit", type=int, default=0)
    f.add_argument("--profile", default="ios", choices=["ios", "ios_safari", "rn_android"])
    f.add_argument("--no-signer", action="store_true")
    f.add_argument("--attach", default="com.shopee.br")
    f.add_argument("--device", default=None)
    f.add_argument("--no-blocked", action="store_true")

    sub.add_parser("status", help="저장 현황")

    args = ap.parse_args()
    if args.cmd == "check":
        return cmd_check()
    if args.cmd == "capture":
        return cmd_capture(args)
    if args.cmd == "fleet":
        return cmd_fleet(args)
    if args.cmd == "status":
        return cmd_status()
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
