# -*- coding: utf-8 -*-
"""BR get_pc 크롤러 — 로그인 계정에서 분리된 '기기 인증서(AC_CERT_D)' 풀 로테이션.

실험으로 검증된 사실(_br_cert_test.py):
  • get_pc 는 로그인(SPC_U) 없이도 완전 데이터(item_keys=103)를 반환한다.
  • 필요한 것은 기기/안티봇 번들(AC_CERT_D + 세션토큰)이며, 이는 '태워지는 계정'이 아니다.
  • 계정이 검색/로그인에서 '차단'돼도 그 jar 의 기기 인증서로 get_pc 는 계속 열린다.

따라서 이 크롤러는:
  1. (무로그인) 모든 jar 에서 SPC_U 를 제거한 '기기 세션'으로만 get_pc 수집 → 계정 소모 0.
  2. (차단쿠키 재활용) '차단된' jar 도 기기 인증서로 재사용. 인증서가 죽으면 captcha_mint 재발급.
  3. (무중단) 여러 기기 인증서를 짧은 시프트로 로테이션, 위험 시 해당 인증서만 쿨다운.

사용:
  python "_br_getpc_devices.py" --status
  python "_br_getpc_devices.py" --target 10000 --shift-quota 40 --fast
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

import _br_batch_crawl as batch  # noqa: E402
from _br_batch_crawl import _load_urls, run_batch  # noqa: E402

# 무로그인 수확이 채우는 대규모 URL 인벤토리(46k+). 기본 URL 소스.
URL_FILE = Path(__file__).resolve().parent / "br_active_product_urls_100k.txt"
from _br_probe import BR_TEST, parse_br_test, parse_url_ids  # noqa: E402
from br_cookies import normalize_br_cookies  # noqa: E402
import proxy_pool  # noqa: E402

# 계정을 식별하는 로그인 키. get_pc 에는 불필요하므로 제거하여 계정 소모를 0 으로 만든다.
LOGIN_KEYS = ("SPC_U",)

OUT_DIR = Path(__file__).resolve().parent / "br_batch_output_100k"
SESS_DIR = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
DEVICES_LIVE = Path(__file__).resolve().parent / "br_device_sessions"
_SUFFIX = ""  # 병렬 샤드별 상태/라이브 파일 분리용 (main 에서 설정)


def _dev_state_path() -> Path:
    return OUT_DIR / f"devices_state{_SUFFIX}.json"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _sync_proxies() -> None:
    _, proxies, _ = parse_br_test(BR_TEST)
    px = ROOT / "proxy(BR shopee).txt"
    if proxies:
        px.write_text("\n".join(proxies) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()


def _strip_login(jar: dict) -> dict:
    return {k: v for k, v in jar.items() if k not in LOGIN_KEYS and v}


def collect_devices() -> list[dict]:
    """AC_CERT_D 를 가진 모든 jar 을 기기 세션으로 수집, 인증서 기준 dedup."""
    devices: list[dict] = []
    seen_cert: set[str] = set()

    def _add(name: str, jar: dict) -> None:
        if not jar or not jar.get("AC_CERT_D"):
            return
        cert = jar["AC_CERT_D"]
        key = cert[:60]
        if key in seen_cert:
            return
        seen_cert.add(key)
        dev = _strip_login(normalize_br_cookies(dict(jar), fresh_cert=False))
        devices.append({
            "id": name,
            "cookies": dev,
            "orig_spc_u": jar.get("SPC_U"),
            "cooldown_until": 0.0,
            "shift_ok": 0,
            "total_ok": 0,
        })

    # 1) BR TEST (최신 사용자 갱신 쿠키)
    try:
        ck, _, _ = parse_br_test(BR_TEST)
        _add("br_test", ck)
    except Exception as e:
        _log(f"  BR TEST parse skipped: {e}")

    # 2) 세션 파일 (live 우선)
    files = sorted(SESS_DIR.glob("*_live.json")) + sorted(SESS_DIR.glob("br_1*.json"))
    for f in files:
        if "account_pool_state" in f.name:
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            _add(f.stem, doc.get("cookies") or {})
        except Exception:
            continue
    return devices


def _load_state() -> dict:
    p = _dev_state_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _dev_state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _missing_urls(url_file: Path, *, shards: int = 1, shard_id: int = 0) -> list[str]:
    batch.OUT = OUT_DIR
    urls = _load_urls(url_file)
    out = []
    for u in urls:
        ids = parse_url_ids(u)
        if batch._out_path(*ids).is_file():
            continue
        if shards > 1 and ((ids[0] + ids[1]) % shards) != shard_id:
            continue
        out.append(u)
    return out


def _count_done() -> int:
    if not OUT_DIR.is_dir():
        return 0
    return sum(1 for _ in OUT_DIR.glob("output_*.json"))


def cmd_status(url_file: Path) -> int:
    devices = collect_devices()
    done = _count_done()
    missing = _missing_urls(url_file)
    _log("BR get_pc DEVICE-CERT pool status")
    _log(f"  outputs saved: {done}")
    _log(f"  remaining URLs: {len(missing)}")
    _log(f"  device certs (login-stripped): {len(devices)}")
    for d in devices:
        _log(f"    - {d['id']} (was SPC_U={d['orig_spc_u']})")
    return 0


def run_shift(device: dict, missing: list[str], *, shift_quota: int, human_mode: bool) -> dict:
    batch.OUT = OUT_DIR
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DEVICES_LIVE.mkdir(parents=True, exist_ok=True)
    # 기기별 live 쿠키 파일 (프록시 서브 등 회전 쿠키 보존, 계정 파일과 분리)
    batch.SESSION_LIVE = DEVICES_LIVE / f"{device['id']}{_SUFFIX}_live.json"

    shift_urls = missing[:shift_quota]
    _log("=" * 70)
    _log("BR get_pc DEVICE SHIFT — login-less (계정 비소모)")
    _log(f"  device={device['id']} (orig SPC_U={device['orig_spc_u']}) SPC_U_now=None")
    _log(f"  AC_CERT_D={'yes' if device['cookies'].get('AC_CERT_D') else 'no'} | quota={shift_quota} urls={len(shift_urls)}")
    _log("=" * 70)

    risk = {"hit": False, "reason": None}

    def on_risk(reason: str, ck: dict) -> None:
        risk["hit"] = True
        risk["reason"] = reason
        _log(f"  device {device['id']}: risk={reason} → cert cooldown")

    stats = run_batch(
        shift_urls,
        dict(device["cookies"]),
        human_mode=human_mode,
        stop_on_traffic=True,
        shift_quota=shift_quota,
        on_risk=on_risk,
    )
    stats["risk"] = risk
    return stats


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="BR get_pc device-cert rotation crawler (login-less)")
    ap.add_argument("--urls", type=Path, default=URL_FILE)
    ap.add_argument("--target", type=int, default=10000, help="총 저장 목표 개수")
    ap.add_argument("--shift-quota", type=int, default=40, help="기기당 시프트 성공 예산")
    ap.add_argument("--risk-cooldown-min", type=float, default=45.0, help="위험 시 기기 인증서 쿨다운(분)")
    ap.add_argument("--soft-rest-min", type=float, default=8.0, help="정상 시프트 후 짧은 휴식(분)")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--shards", type=int, default=1, help="병렬 워커 총 개수")
    ap.add_argument("--shard-id", type=int, default=0, help="이 워커의 샤드 인덱스(0..shards-1)")
    ap.add_argument("--no-sticky", dest="sticky", action="store_false",
                    help="(비권장) 프록시 IP 로테이션 허용 — 단일 인증서 과열 위험")
    ap.set_defaults(sticky=True)
    args = ap.parse_args()

    if args.status:
        return cmd_status(args.urls)

    global _SUFFIX
    if args.shards > 1:
        _SUFFIX = f"_s{args.shard_id}"

    if args.sticky:
        # 로그인 없는 기기 인증서 세션은 '한 대의 실기기'처럼 보여야 한다.
        # IP를 5개 URL마다 바꾸면 동일 인증서가 여러 IP로 순간이동 → /verify/traffic/error.
        # 청크/브라우저 리프레시 로테이션을 사실상 비활성화하여 한 IP에 고정한다.
        batch.SESSION_CHUNK = 10 ** 9
        batch.BROWSER_REFRESH_URLS = 10 ** 9
        _log("sticky 모드: 기기 인증서를 단일 프록시 IP에 고정(로테이션 비활성)")

    _sync_proxies()
    human_mode = not args.fast
    risk_cd = args.risk_cooldown_min * 60
    soft_cd = args.soft_rest_min * 60

    devices = collect_devices()
    if not devices:
        _log("기기 인증서(AC_CERT_D) jar 이 없습니다.")
        return 1
    _log(f"기기 인증서 풀: {len(devices)}개 (로그인 제거됨)")

    # 지속 상태(쿨다운/누적) 병합
    state = _load_state()
    for d in devices:
        rec = state.get(d["id"]) or {}
        d["cooldown_until"] = float(rec.get("cooldown_until") or 0)
        d["total_ok"] = int(rec.get("total_ok") or 0)
        d["consec_risk"] = int(rec.get("consec_risk") or 0)

    shard_tag = f" [shard {args.shard_id}/{args.shards}]" if args.shards > 1 else ""
    total_run_ok = 0
    idle_rounds = 0
    while True:
        done = _count_done()
        if done >= args.target:
            _log(f"목표 달성: {done}/{args.target} 저장 완료.")
            break
        missing = _missing_urls(args.urls, shards=args.shards, shard_id=args.shard_id)
        if not missing:
            _log("남은 URL 없음 — 완료.")
            break

        now = time.time()
        ready = [d for d in devices if d["cooldown_until"] <= now]
        if not ready:
            wait = min(d["cooldown_until"] for d in devices) - now
            wait = max(30, min(wait + 5, 1500))
            idle_rounds += 1
            _log(f"모든 기기 인증서 쿨다운 중 — {int(wait/60)+1}분 대기 후 재확인 (idle #{idle_rounds})")
            time.sleep(wait)
            continue
        idle_rounds = 0
        # 가장 오래 안 쓴(누적 적은) 기기부터
        ready.sort(key=lambda d: (d["total_ok"], d["cooldown_until"]))
        device = ready[0]

        _log(f"진행{shard_tag}: {done}/{args.target} | 남은 URL {len(missing)} | 사용 가능 인증서 {len(ready)}/{len(devices)}")
        try:
            stats = run_shift(device, missing, shift_quota=args.shift_quota, human_mode=human_mode)
        except Exception as e:
            _log(f"  시프트 예외: {e} — 기기 짧은 쿨다운")
            device["cooldown_until"] = time.time() + soft_cd
            state[device["id"]] = {"cooldown_until": device["cooldown_until"], "total_ok": device["total_ok"]}
            _save_state(state)
            continue

        ok = int(stats.get("ok") or 0)
        total_run_ok += ok
        device["total_ok"] += ok
        if stats.get("risk", {}).get("hit"):
            # 단일 인증서는 과열 시 '시간'으로만 회복된다. 연속 위험일수록 더 길게 쉰다
            # (에스컬레이팅 백오프). 정상 시프트가 나오면 consec 리셋.
            device["consec_risk"] = int(device.get("consec_risk") or 0) + 1
            back = risk_cd * (2 ** min(device["consec_risk"] - 1, 2))
            back = min(back, 45 * 60)  # 최대 45분 — 회복은 시간문제, 과도한 대기 회피
            device["cooldown_until"] = time.time() + back
            _log(f"  {device['id']} 위험 쿨다운 {int(back/60)}분 (연속위험 {device['consec_risk']})")
        else:
            if ok > 0:
                device["consec_risk"] = 0
            device["cooldown_until"] = time.time() + soft_cd
        state[device["id"]] = {
            "cooldown_until": device["cooldown_until"],
            "total_ok": device["total_ok"],
            "consec_risk": device.get("consec_risk", 0),
        }
        _save_state(state)

        _log(
            f"시프트 완료: ok={ok} fail={stats.get('fail')} "
            f"stop={stats.get('stopped_reason')} device={device['id']} "
            f"(누적 이번 실행 {total_run_ok})"
        )
        summary = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "target": args.target,
            "done": _count_done(),
            "run_ok": total_run_ok,
            "devices": {d["id"]: {"total_ok": d["total_ok"]} for d in devices},
        }
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "device_crawl_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        time.sleep(8)

    _log(f"\n종료: 저장 {_count_done()}/{args.target} | 이번 실행 ok={total_run_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
