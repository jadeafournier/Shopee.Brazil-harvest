# -*- coding: utf-8 -*-
"""로그인 세션 삭제 + 새 BR 프록시를 붙인 뒤 그 IP 위에서 쇼피를 기동한다."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "client 4 api integration"))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))

from br_emulator import vmos_cloud  # noqa: E402
from br_emulator.vmos_app_capture import (  # noqa: E402
    DEFAULT_PORT,
    RUNTIME,
    _log,
    _sh,
    install_system_ca,
    reset_shopee_login,
    setup_reverse_proxy,
    start_mitm,
)
import proxy_pool  # noqa: E402


def probe(url: str, timeout: float = 12.0) -> tuple[bool, float, str]:
    u = urlsplit(url)
    proxy = f"{u.scheme or 'http'}://{u.hostname}:{u.port or 80}"
    auth = f"{u.username}:{u.password or ''}" if u.username else ""
    opener = build_opener(ProxyHandler({"http": url, "https": url}))
    req = Request("https://api.ipify.org", headers={"User-Agent": "Mozilla/5.0"})
    t0 = time.time()
    try:
        with opener.open(req, timeout=timeout) as resp:
            ip = (resp.read() or b"").decode("utf-8", "replace").strip()[:64]
            return resp.status == 200 and bool(ip), time.time() - t0, ip
    except Exception as exc:
        return False, time.time() - t0, f"{type(exc).__name__}"
    finally:
        del auth, proxy


def pick_quality(tries: int = 8) -> str:
    """앞에서부터 한 줄씩 꺼내 시험한다. 집은 줄은 이미 파일에서 삭제된다."""
    proxy_pool.set_region("BR")
    last_err = ""
    for n in range(tries):
        try:
            url, key, left = proxy_pool.consume_next()
        except Exception as exc:
            last_err = str(exc)
            break
        ok, sec, ip = probe(url)
        _log(f"  프록시 시험 {n + 1}/{tries}  {key}  {sec:.2f}s  {ip}  {'OK' if ok else 'FAIL'}  남은 {left}")
        if ok:
            (RUNTIME / "upstream.txt").write_text(url, encoding="utf-8")
            host = url.split("@")[-1] if "@" in url else url
            _log(f"  채택 {key}  {sec:.2f}s  ip={ip}  → {host}")
            return url
    if last_err:
        raise RuntimeError(f"프록시 풀 소진: {last_err}")
    raise RuntimeError("프로브 성공 프록시가 없습니다")


def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    _log("새 브라질 프록시 품질 시험")
    upstream = pick_quality()
    rec = vmos_cloud.ensure_connected()
    serial = rec["serial"]
    _log(f"  기기 {serial}  {rec.get('padName')}")
    install_system_ca(serial)
    start_mitm(DEFAULT_PORT, upstream=upstream)
    setup_reverse_proxy(serial, DEFAULT_PORT)
    cur = _sh(serial, "settings get global http_proxy")
    _log(f"  로그인·수집 출구 http_proxy={cur} → 같은 브라질 sticky")
    reset_shopee_login(serial)
    _log("로그인 세션 삭제 완료. 쇼피 홈이 브라질 프록시 위에서 열려 있습니다.")
    _log("이 화면에서 로그인하세요. mitm을 끄면 출구가 바뀝니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
