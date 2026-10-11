# -*- coding: utf-8 -*-
"""에뮬 서명 + 쿠키풀(차단 쿠키 포함) + 프록시풀 결합 대량 수집.

'차단 쿠키를 다양한 방법과 결합' 구현:
  * 브라우저에서 캡차로 막힌 쿠키도 모바일 API 채널에서는 다르게 취급될 수 있어,
    계정 풀의 모든 jar 를 회전 사용한다(낭비 없음).
  * 에뮬 안 실제 앱이 만든 서명을 signer 로 주입(IosMobileClient.signer 슬롯).
  * 프록시 풀로 출구 IP 회전.
  * 성공분은 브라우저/mitm 경로와 동일 봉투로 저장 → 하류 통합.

signer(SIGN_SPEC) 미확정 시에는 need_app_sign 이 나므로, 그때는 mitm+adb 캡처
경로(run_emulator capture)를 사용한다. 이 플릿은 서명 확정 후 스케일 경로다.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_DIR = Path(__file__).resolve().parent
for _p in (str(_DIR), str(_DIR.parent), str(_DIR.parent.parent),
           str(_DIR.parent.parent / "shopee_getpc_crawler")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from br_emulator import _save  # noqa: E402
from br_ios_fleet.ios_client import IosMobileClient  # noqa: E402
from br_cookies import enforce_br_locale, normalize_br_cookies  # noqa: E402
from _br_probe import BR_TEST, parse_br_test  # noqa: E402


def _log(msg: str) -> None:
    print(msg, flush=True)


def _encode_proxy(line: str) -> str:
    from urllib.parse import quote
    if "://" in line:
        return line
    parts = line.split(":", 3)
    if len(parts) == 4:
        host, port, user, pwd = parts
        return f"http://{quote(user, safe='')}:{quote(pwd, safe='')}@{host}:{port}"
    return f"http://{line}"


def _collect_cookie_jars(include_blocked: bool) -> list[dict]:
    """계정 풀의 모든 jar(차단 포함) + BR TEST 를 회전 리스트로."""
    jars: list[dict] = []
    seen_u: set[str] = set()

    def _add(ck: dict) -> None:
        if not ck or not ck.get("SPC_U"):
            return
        if ck["SPC_U"] in seen_u:
            return
        seen_u.add(ck["SPC_U"])
        jars.append(enforce_br_locale(normalize_br_cookies(ck, fresh_cert=False)))

    try:
        cookies, _, _ = parse_br_test(BR_TEST)
        _add(cookies)
    except Exception:
        pass
    try:
        from br_account_pool import AccountPool
        pool = AccountPool()
        for row in pool.summary():
            aid = row.get("account")
            if not aid:
                continue
            # 차단 쿠키도 모바일 채널에선 재사용 시도(옵션).
            if not include_blocked and not row.get("available"):
                continue
            ck = pool.load_cookies(aid)
            _add(ck)
    except Exception as e:
        _log(f"  계정 풀 로드 건너뜀: {e}")
    return jars


def _proxies() -> list[str]:
    try:
        _, proxies, _ = parse_br_test(BR_TEST)
        return list(proxies or [])
    except Exception:
        return []


def _job(url: str, jar: dict, proxies: list[str], signer, profile: str) -> dict:
    ids = _save.parse_ids(url)
    if not ids:
        return {"url": url, "status": "bad_url", "ok": False}
    sid, iid = ids
    if _save.already_saved(sid, iid):
        return {"url": url, "status": "skip", "ok": True}
    px = _encode_proxy(random.choice(proxies)) if proxies else None
    client = IosMobileClient(jar, region="BR", proxy_url=px, profile=profile, signer=signer)
    r = client.fetch_best(sid, iid)
    if r.ok and r.data:
        src = f"emulator_mobile_{r.endpoint.replace('/', '_')}"
        _save.save_product(_save.product_url(sid, iid), r.data, source=src)
        return {"url": url, "status": "ok", "ok": True, "endpoint": r.endpoint, "title": r.title}
    return {"url": url, "status": r.status, "ok": False, "error": r.error}


def run(urls_path: Path, *, workers: int, limit: int, profile: str,
        use_signer: bool, attach_pkg: str, device: str | None,
        include_blocked: bool) -> int:
    urls = _save.load_urls(urls_path, limit=limit, skip_saved=True)
    if not urls:
        _log("처리할 URL 이 없습니다.")
        return 0
    jars = _collect_cookie_jars(include_blocked)
    if not jars:
        _log("❌ 사용할 쿠키 jar 이 없습니다. BR TEST.txt 확인.")
        return 1
    proxies = _proxies()

    signer = None
    if use_signer:
        try:
            from br_emulator.signer_bridge import FridaSigner
            fs = FridaSigner(attach_pkg, spawn=False, device_id=device)
            fs.start()
            signer = fs.make_signer()
        except Exception as e:
            _log(f"⚠ Frida 서명 브리지 시작 실패({e}). 서명 없이 진행(대개 need_app_sign).")

    _log("=" * 68)
    _log("【에뮬 모바일 플릿】 차단쿠키 결합 · 앱 서명 주입")
    _log(f"  URL {len(urls):,} | 워커 {workers} | 프로파일 {profile}")
    _log(f"  쿠키 jar {len(jars)}개(차단 포함={include_blocked}) | 프록시 {len(proxies)}개")
    _log(f"  서명 브리지: {'활성' if signer else '비활성(need_app_sign 예상 → mitm+adb 사용)'}")
    _log("=" * 68)

    ok = fail = skip = sign = 0
    t0 = time.time()
    jar_cycle = jars * 0 + jars  # copy
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {}
        for i, u in enumerate(urls):
            jar = jar_cycle[i % len(jar_cycle)]
            futs[ex.submit(_job, u, jar, proxies, signer, profile)] = u
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                res = fut.result()
            except Exception as e:
                fail += 1
                continue
            st = res.get("status")
            if st == "ok":
                ok += 1
                _log(f"  [{done}/{len(urls)}] ✅ {res.get('endpoint')} | {res.get('title','')[:36]} (누적 {_save.count_saved():,})")
            elif st == "skip":
                skip += 1
            elif st == "need_app_sign":
                sign += 1
                if sign <= 3 or sign % 25 == 0:
                    _log(f"  [{done}/{len(urls)}] 🔐 need_app_sign (서명 미주입)")
                if sign >= 8 and ok == 0 and not signer:
                    _log("⛔ 서명 없이 모바일 API 불가 — 중단. mitm+adb 캡처 경로를 사용하세요.")
                    for f in futs:
                        f.cancel()
                    break
            else:
                fail += 1

    elapsed = time.time() - t0
    _log("\n" + "=" * 68)
    _log("【에뮬 플릿 완료】")
    _log(f"  성공 {ok:,} | 스킵 {skip:,} | 서명필요 {sign:,} | 실패 {fail:,}")
    _log(f"  소요 {elapsed:.0f}s | 총 저장 {_save.count_saved():,}")
    _log("=" * 68)
    return 0 if (ok or skip) else 2


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="에뮬 모바일 플릿(차단쿠키 결합 + 앱 서명)")
    ap.add_argument("--urls", type=Path,
                    default=_save.CLIENT4 / "br_active_product_urls_100k.txt")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--profile", default="ios", choices=["ios", "ios_safari", "rn_android"])
    ap.add_argument("--no-signer", action="store_true", help="Frida 서명 브리지 없이 실행(프로브용)")
    ap.add_argument("--attach", default="com.shopee.br", help="서명 주입용 attach 패키지명")
    ap.add_argument("--device", default=None, help="frida/adb 기기 id")
    ap.add_argument("--no-blocked", action="store_true", help="차단 쿠키 제외(가용 쿠키만)")
    args = ap.parse_args()
    return run(
        args.urls, workers=args.workers, limit=args.limit, profile=args.profile,
        use_signer=not args.no_signer, attach_pkg=args.attach, device=args.device,
        include_blocked=not args.no_blocked,
    )


if __name__ == "__main__":
    raise SystemExit(main())
