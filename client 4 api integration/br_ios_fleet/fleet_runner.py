# -*- coding: utf-8 -*-
"""iOS/모바일 플릿 실행기 — 대량 병렬 수집 (한국어 로그).

목표 구조 (Client 1 '하루 100만' APP 방안과 동일한 운영 모델):
  - 다수 워커가 동시에 모바일 API 호출
  - 계정(쿠키) 풀 + 프록시 풀 회전
  - 성공분 Client 1 봉투로 저장
  - 서명 차단 시 즉시 중단(계정 보호) 또는 브라우저 폴백 큐에 적재

사용법:
  python -m br_ios_fleet.fleet_runner --probe
  python -m br_ios_fleet.fleet_runner --urls ../br_active_product_urls_1000.txt --workers 20 --limit 50
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

DIR = Path(__file__).resolve().parent
CLIENT4 = DIR.parent
ROOT = CLIENT4.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CLIENT4))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))

from br_ios_fleet.ios_client import IosMobileClient, MobileResult  # noqa: E402
from _br_probe import BR_TEST, parse_br_test, parse_url_ids  # noqa: E402
from br_cookies import enforce_br_locale, normalize_br_cookies  # noqa: E402

OUT_DIR = CLIENT4 / "br_ios_fleet_output"
FALLBACK_QUEUE = CLIENT4 / "br_ios_fallback_queue.txt"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_proxy(line: str) -> str:
    if "://" in line:
        return line
    host, port, user, pwd = line.split(":", 3)
    return f"http://{quote(user, safe='')}:{quote(pwd, safe='')}@{host}:{port}"


def _load_urls(path: Path, limit: int = 0) -> list[str]:
    urls = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if limit > 0:
        return urls[:limit]
    return urls


def _out_path(shop_id: int, item_id: int) -> Path:
    return OUT_DIR / f"output_{shop_id}_{item_id}.json"


def _wrap_client1(url: str, data: dict) -> dict:
    return {
        "task_link": url,
        "token": "br_ios_fleet",
        "api_key": "br_ios_fleet",
        "bff_meta": None,
        "error": None,
        "error_msg": None,
        "data": data,
        "source": "ios_mobile_api",
        "captured_at": _iso(),
    }


def _save_ok(url: str, data: dict) -> Path:
    sid, iid = parse_url_ids(url)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _out_path(sid, iid)
    path.write_text(json.dumps(_wrap_client1(url, data), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _append_fallback(url: str, reason: str) -> None:
    FALLBACK_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with FALLBACK_QUEUE.open("a", encoding="utf-8") as f:
        f.write(f"{url}\t{reason}\t{_iso()}\n")


def cmd_probe() -> int:
    """쿠키로 BR 모바일 엔드포인트 스모크 테스트 (한국어 출력)."""
    cookies, proxies, test_urls = parse_br_test(BR_TEST)
    if not cookies.get("SPC_U"):
        _log("❌ BR TEST.txt 에 SPC_U 쿠키가 없습니다.")
        return 1
    cookies = enforce_br_locale(normalize_br_cookies(cookies, fresh_cert=False))
    px = _encode_proxy(random.choice(proxies)) if proxies else None

    # 테스트 URL 또는 시드 파일
    urls = list(test_urls)
    seed = CLIENT4 / "br_active_product_urls_1000.txt"
    if seed.is_file():
        urls.extend(_load_urls(seed, limit=3))
    if not urls:
        _log("❌ 테스트할 URL이 없습니다.")
        return 1

    _log("=" * 70)
    _log("【iOS/모바일 프로브】 Shopee BR")
    _log(f"  계정 SPC_U={cookies.get('SPC_U')}")
    _log(f"  AC_CERT={'있음' if cookies.get('AC_CERT_D') else '없음'}")
    _log(f"  프록시={'사용' if px else '없음'}")
    _log("=" * 70)

    profiles = ["ios", "ios_safari", "rn_android"]
    results: list[dict] = []
    for profile in profiles:
        client = IosMobileClient(cookies, region="BR", proxy_url=px, profile=profile)
        _log(f"\n▶ 프로파일: {profile}")
        for url in urls[:2]:
            sid, iid = parse_url_ids(url)
            _log(f"  상품 i.{sid}.{iid}")
            for label, fn in [
                ("item/get", lambda: client.fetch_item_get(sid, iid)),
                ("get_rw", lambda: client.fetch_get_rw(sid, iid)),
                ("get_pc", lambda: client.fetch_get_pc(sid, iid)),
            ]:
                r = fn()
                _log(f"    [{label}] {r.summary_ko()}")
                results.append({
                    "profile": profile, "url": url, "endpoint": label,
                    "ok": r.ok, "status": r.status, "http": r.http,
                    "error": r.error, "data_keys": r.data_keys, "item_keys": r.item_keys,
                })
                if r.ok:
                    _save_ok(url, r.data or {})
                    _log(f"    💾 저장 완료 → {_out_path(sid, iid).name}")

    out = OUT_DIR / "probe_summary.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "finished_at": _iso(),
        "spc_u": cookies.get("SPC_U"),
        "results": results,
        "ok_count": sum(1 for x in results if x["ok"]),
        "need_app_sign": sum(1 for x in results if x["status"] == "need_app_sign"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"\n📋 프로브 요약 저장: {out}")
    ok_n = sum(1 for x in results if x["ok"])
    sign_n = sum(1 for x in results if x["status"] == "need_app_sign")
    _log(f"결과: 성공 {ok_n} / 서명필요 {sign_n} / 전체 {len(results)}")
    if ok_n == 0 and sign_n:
        _log("⚠️  모바일 API가 앱 서명을 요구합니다.")
        _log("   → Client 1 iOS APP 방안 = 실기기/Frida 서명기 필요")
        _log("   → 현재 시스템은 브라우저 get_pc 폴백으로 계속 수집합니다.")
    return 0 if ok_n else 2


def _worker_job(
    url: str,
    cookies: dict,
    proxies: list[str],
    profile: str,
) -> dict[str, Any]:
    sid, iid = parse_url_ids(url)
    if _out_path(sid, iid).is_file():
        return {"url": url, "status": "skip_exists", "ok": True}
    px = _encode_proxy(random.choice(proxies)) if proxies else None
    client = IosMobileClient(cookies, region="BR", proxy_url=px, profile=profile)
    r = client.fetch_best(sid, iid)
    if r.ok and r.data:
        _save_ok(url, r.data)
        return {"url": url, "status": "ok", "ok": True, "endpoint": r.endpoint, "title": r.title}
    if r.status == "need_app_sign":
        _append_fallback(url, "need_app_sign")
        return {"url": url, "status": "need_app_sign", "ok": False}
    _append_fallback(url, r.status)
    return {"url": url, "status": r.status, "ok": False, "error": r.error}


def cmd_fleet(urls_path: Path, *, workers: int, limit: int, profile: str) -> int:
    cookies, proxies, _ = parse_br_test(BR_TEST)
    if not cookies.get("SPC_U"):
        _log("❌ BR TEST.txt 에 쿠키가 없습니다.")
        return 1
    cookies = enforce_br_locale(normalize_br_cookies(cookies, fresh_cert=False))
    urls = _load_urls(urls_path, limit=limit)
    if not urls:
        _log("❌ URL 목록이 비어 있습니다.")
        return 1

    _log("=" * 70)
    _log("【iOS/모바일 플릿】 대량 수집 시작")
    _log(f"  목표 URL: {len(urls):,}건")
    _log(f"  병렬 워커: {workers}")
    _log(f"  프로파일: {profile}")
    _log(f"  계정: SPC_U={cookies.get('SPC_U')}")
    _log(f"  출력: {OUT_DIR}")
    _log("=" * 70)

    ok = fail = skip = sign = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(_worker_job, u, cookies, proxies, profile) for u in urls]
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                res = fut.result()
            except Exception as e:
                fail += 1
                _log(f"  [{done}/{len(urls)}] 예외: {e}")
                continue
            st = res.get("status")
            if st == "ok":
                ok += 1
                _log(f"  [{done}/{len(urls)}] ✅ 성공 {res.get('endpoint')} | {res.get('title', '')[:40]}")
            elif st == "skip_exists":
                skip += 1
            elif st == "need_app_sign":
                sign += 1
                if sign <= 3 or sign % 20 == 0:
                    _log(f"  [{done}/{len(urls)}] 🔐 앱 서명 필요 → 폴백 큐 적재")
                # 서명 차단이 연속이면 계정 보호를 위해 조기 중단
                if sign >= 5 and ok == 0:
                    _log("⛔ 연속 서명 차단 — 플릿 중단 (계정 보호)")
                    for f in futs:
                        f.cancel()
                    break
            else:
                fail += 1
                _log(f"  [{done}/{len(urls)}] ❌ {st}")

    elapsed = time.time() - t0
    summary = {
        "finished_at": _iso(),
        "ok": ok, "fail": fail, "skip": skip, "need_app_sign": sign,
        "total": len(urls), "workers": workers, "profile": profile,
        "elapsed_s": round(elapsed, 1),
        "rate_per_min": round(ok / max(0.001, elapsed / 60), 1),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "fleet_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    _log("\n" + "=" * 70)
    _log("【플릿 완료】")
    _log(f"  성공 {ok:,} | 스킵 {skip:,} | 서명필요 {sign:,} | 실패 {fail:,}")
    _log(f"  소요 {elapsed:.0f}s | 속도 ~{summary['rate_per_min']}/분")
    if sign and ok == 0:
        _log("  → 다음 단계: _br_long_crawl.py 브라우저 경로로 폴백 큐 처리")
    _log("=" * 70)
    return 0 if ok or skip else 2


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="Shopee iOS/모바일 플릿 (한국어)")
    ap.add_argument("--probe", action="store_true", help="모바일 API 스모크 테스트")
    ap.add_argument("--urls", type=Path, help="상품 URL 목록")
    ap.add_argument("--workers", type=int, default=20, help="병렬 워커 수")
    ap.add_argument("--limit", type=int, default=0, help="최대 처리 건수 (0=전체)")
    ap.add_argument("--profile", default="ios", choices=["ios", "ios_safari", "rn_android"])
    args = ap.parse_args()

    if args.probe:
        return cmd_probe()
    if args.urls:
        return cmd_fleet(args.urls, workers=args.workers, limit=args.limit, profile=args.profile)
    ap.print_help()
    _log("\n예시:")
    _log('  python -m br_ios_fleet.fleet_runner --probe')
    _log('  python -m br_ios_fleet.fleet_runner --urls "br_active_product_urls_1000.txt" --workers 20 --limit 50')
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
