# -*- coding: utf-8 -*-
"""VMOS Cloud Phone 앱 전용 get_pc 캡처.

웹/Patchright 없음. 클라우드폰의 Shopee 앱이 스스로 서명한
get_pc/get_rw/item/get 응답을 mitm_capture 로 저장한다.

경로:
  1) VMOS ADB 연결
  2) 이 PC mitmdump (regular) + adb reverse
     — 클라우드폰은 LAN 으로 이 PC 에 닿지 않으므로 127.0.0.1 역방향만 사용
  3) vu 로 mitm CA 를 시스템 저장소에 bind-mount
  4) 글로벌 http_proxy = 127.0.0.1:<port>
  5) 앱 딥링크로 PDP 오픈 → 저장 폴링

실행:
  python -m br_emulator.vmos_app_capture
  python -m br_emulator.vmos_app_capture --shop 1119985490 --item 21299840174
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

_DIR = Path(__file__).resolve().parent
for _p in (str(_DIR), str(_DIR.parent), str(_DIR.parent.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from br_emulator import _save, vmos_cloud  # noqa: E402

RUNTIME = _save.ROOT / "runtime" / "vmos_cloud"
MITM_LOG = Path(os.environ.get("SHOPEE_MITM_LOG") or (RUNTIME / "mitm_app.log"))
SESSION_DIR = _save.ROOT / "Shopee.Brazil" / "Sessions"
SESSION_PARTS = (
    "shared_prefs",
    "databases",
    "no_backup",
    "files/mmkv",
    "files/shopeeBR",
    "files/ccms",
    "app_webview_com.shopee.br",
)
ADDON = _DIR / "mitm_capture.py"
VENV_SCRIPTS = _save.ROOT / ".venv" / "Scripts"
MITMDUMP = VENV_SCRIPTS / ("mitmdump.exe" if os.name == "nt" else "mitmdump")
_PY_CANDIDATES = (
    Path(r"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"),
    VENV_SCRIPTS / ("python.exe" if os.name == "nt" else "python"),
)


def _working_python() -> str:
    """venv python.exe 가 깨진 home(C:\\Python314) 을 가리킬 수 있어 검증한다."""
    for cand in (Path(sys.executable), *_PY_CANDIDATES):
        if not cand or not Path(cand).is_file():
            continue
        try:
            p = subprocess.run(
                [str(cand), "-c", "import mitmproxy, mitmproxy.tools.dump"],
                capture_output=True, timeout=20,
            )
        except subprocess.TimeoutExpired:
            continue
        if p.returncode == 0:
            return str(cand)
    return sys.executable
DEFAULT_SHOP, DEFAULT_ITEM = 1119985490, 21299840174
DEFAULT_PORT = 8088
SHOPEE_PKG = os.environ.get("SHOPEE_PKG") or "com.shopee.br"
SHOPEE_WEB_HOST = os.environ.get("SHOPEE_WEB_HOST") or "shopee.com.br"
WAIT_S = 10.0
WAIT_S_SAFE = 25.0
# 콜드 재기동(-S) 직후 스플래시가 10초를 넘기면, 미저장으로 보고
# 바로 다시 -S 해서 앱이 홈까지 못 뜬다. 파일은 생기는 즉시 반환한다.
COLD_START_WAIT_S = 25.0
POLL_S = 0.15
GAP_S = 0.4
GAP_MIN = 0.4
GAP_MAX = 0.4
BROWSE_EVERY_MIN = 0
BROWSE_EVERY_MAX = 0
MITM_HTTP2 = False
RELOAD_WAIT_S = 0.0
REST_EVERY = 40
REST_S = 12.0
# 적응형 재기동: 건강하면 VIEW 만 계속, 문제(연속 미저장)일 때·신선도 유지용으로만 콜드 재기동
ADAPTIVE_BOUNCE = True
FORCE_BOUNCE_EVERY = 20
MISS_BOUNCE_STREAK = 2
# (레거시) SHOPEE_SESSION_ROTATE_MIN — 순환은 보관 1개부터, 하루 1회·오래된 순
SESSION_ROTATE_MIN = 1
TARGET_PER_HOUR = 500
TRAP_REST_S = 90.0
BOUNCE_EVERY = 4
# 인앱형(사람처럼) 모드 — 봇 신호(am start -S 콜드 재기동) 제거.
#   앱을 죽이지 않고 웜 상태로 유지, 상품마다 BACK→홈 스크롤→긴 랜덤 간격.
HUMAN_MODE = os.environ.get("HARVEST_HUMAN") == "1"
HUMAN_GAP_MIN = float(os.environ.get("HUMAN_GAP_MIN") or 12.0)
HUMAN_GAP_MAX = float(os.environ.get("HUMAN_GAP_MAX") or 30.0)
HUMAN_BOUNCE_EVERY = int(os.environ.get("HUMAN_BOUNCE_EVERY") or 8)
HOME_ABORT_S = 2.5
# PDP /pdp/get 이 status 200 + 이 error 코드 + data=null 이면 상품 삭제/없음.
# (실측: 266900002 = item not found. anti-crawler 418/90309999 와는 다르다.)
_GONE_ERR_CODES = ("266900002",)
MAX_418_ROTATES = 12
# 같은 상품이 새 IP 로도 계속 418 이면 이 횟수까지만 교체하고 스킵
MAX_ROTATE_PER_ITEM = 3
# 서로 다른 상품이 모든 IP 에서 연속 418 → 계정 차단으로 보고 중지
ACCOUNT_FLAG_BLOCKS = 6
CAPTCHA_WAIT_S = 1800.0
BLOCKED_USERIDS = frozenset(
    {"1706784820"}
    | {u.strip() for u in (os.environ.get("BLOCKED_USERIDS") or "").split(",") if u.strip()}
)
PROXY_ACT = f"{SHOPEE_PKG}/com.shopee.app.ui.proxy.ProxyActivity"
HOME_ACT = f"{SHOPEE_PKG}/com.shopee.app.ui.home.HomeActivity_"


STATUS_JSON = RUNTIME / "harvest_status.json"

# 이름 있는 수집 방식. "기본방식" 값은 고정한다.
# 나중에 동작이 바뀌어도 --method 기본방식 (또는 HARVEST_METHOD=기본방식) 이면 이 값으로 덮어쓴다.
# 새 실험은 이 딕셔너리에 다른 키를 추가한다.
NAMED_METHODS = {
    "기본방식": {
        "human": False,
        "bounce_every": 4,
        "gap_s": 0.4,
        "gap_min": 0.4,
        "gap_max": 0.4,
        "browse_every_min": 0,
        "browse_every_max": 0,
        "http2": False,
        "rest_every": 40,
        "rest_s": 12.0,
        "adaptive_bounce": True,
        "force_bounce_every": 20,
        "miss_bounce_streak": 2,
        "wait_s": 10.0,
        "home_abort_s": 2.5,
        "no_upstream": True,
        "rotate_every": 300,
    },
}
ACTIVE_METHOD = "기본방식"


def apply_named_method(name: str | None) -> str:
    """지정한 수집 방식으로 전역 동작을 맞춘다. 없는 이름이면 종료."""
    global HUMAN_MODE, BOUNCE_EVERY, GAP_S, REST_EVERY, REST_S, WAIT_S, HOME_ABORT_S
    global METHOD_LABEL, ACTIVE_METHOD
    global GAP_MIN, GAP_MAX, BROWSE_EVERY_MIN, BROWSE_EVERY_MAX, MITM_HTTP2, ROTATE_EVERY
    global ADAPTIVE_BOUNCE, FORCE_BOUNCE_EVERY, MISS_BOUNCE_STREAK
    key = (name or os.environ.get("HARVEST_METHOD") or "기본방식").strip()
    spec = NAMED_METHODS.get(key)
    if spec is None:
        known = ", ".join(NAMED_METHODS)
        raise SystemExit(f"없는 수집 방식: {key}. 사용 가능: {known}")
    ACTIVE_METHOD = key
    HUMAN_MODE = bool(spec["human"])
    BOUNCE_EVERY = int(spec["bounce_every"])
    GAP_S = float(spec["gap_s"])
    GAP_MIN = float(spec.get("gap_min", spec["gap_s"]))
    GAP_MAX = float(spec.get("gap_max", spec["gap_s"]))
    BROWSE_EVERY_MIN = int(spec.get("browse_every_min", 0))
    BROWSE_EVERY_MAX = int(spec.get("browse_every_max", 0))
    MITM_HTTP2 = bool(spec.get("http2", False))
    ROTATE_EVERY = int(spec.get("rotate_every", 0))
    REST_EVERY = int(spec["rest_every"])
    REST_S = float(spec["rest_s"])
    ADAPTIVE_BOUNCE = bool(spec.get("adaptive_bounce", False))
    FORCE_BOUNCE_EVERY = int(spec.get("force_bounce_every", 0))
    MISS_BOUNCE_STREAK = int(spec.get("miss_bounce_streak", 0))
    WAIT_S = float(spec["wait_s"])
    HOME_ABORT_S = float(spec["home_abort_s"])
    if key == "기본방식":
        bounce_desc = (
            f"적응형 재기동(건강하면 VIEW 지속·연속 미저장 {MISS_BOUNCE_STREAK}건 또는 {FORCE_BOUNCE_EVERY}건마다 재기동)"
            if ADAPTIVE_BOUNCE else f"{BOUNCE_EVERY}건 연속 저장마다 재기동"
        )
        METHOD_LABEL = (
            "기본방식 · 상류 프록시 없음 · mitm "
            f"· {bounce_desc} · 미저장은 기본 재기동 없이 다음 상품 "
            f"· 418은 캡차 대기(앱 재기동 없음) "
            f"· 저장 {int(spec.get('rotate_every', 0))}건마다 또는 막히면 다음 보관 세션 "
            "· 오래된 세션부터 하루 1회 순환, 오늘 분 모두 쓰면 새 로그인 "
            "· 페이지 사용 불가 화면이면 다른 보관 세션으로 전환, 없으면 새 로그인 "
            f"· HTTP/2 {'켬' if MITM_HTTP2 else '끔'} "
            f"· 간격 {GAP_MIN:g}~{GAP_MAX:g}s "
            f"· {REST_EVERY}건마다 {REST_S:.0f}s 휴식 "
            "· 계정 복원·새 로그인 직후 1회 세션 보관"
        )
    else:
        METHOD_LABEL = key
    _log(f"  수집 방식 = {key}")
    return key


if HUMAN_MODE:
    METHOD_LABEL = (
        "VMOS 앱 전용 · 인앱형(사람) · 콜드 재기동 없음 "
        f"· {HUMAN_BOUNCE_EVERY}건마다 인앱 리셋 · 상품 간격 {HUMAN_GAP_MIN:g}~{HUMAN_GAP_MAX:g}s 랜덤 "
        f"· 무음 차단 감지 시 중지 · 폰 회선 출구"
    )
else:
    METHOD_LABEL = (
        "VMOS 앱 전용 · 상류 프록시 없음 · 연속 VIEW "
        f"· {BOUNCE_EVERY}건마다 재기동 · 홈 감지 {HOME_ABORT_S:g}s 조기 중단 "
        f"· {REST_EVERY}건마다 {REST_S:.0f}s 휴식 · 계정 전환·로그인 직후 1회 세션 보관 "
        f"· 418은 캡차 수동 통과 후 재개 · 폰 회선 출구"
    )


def _log(msg: str) -> None:
    print(msg, flush=True)
    logp = os.environ.get("HARVEST_LOG")
    if logp:
        try:
            with open(logp, "a", encoding="utf-8") as fh:
                fh.write(msg + "\n")
        except Exception:
            pass


def _status_path(payload: dict | None = None) -> Path:
    serial = ""
    if payload:
        serial = str(payload.get("serial") or "")
    if not serial:
        serial = (os.environ.get("SHOPEE_SERIAL") or "").strip()
    if not serial:
        return STATUS_JSON
    return RUNTIME / f"harvest_status_{serial.replace(':', '_')}.json"


def write_harvest_status(payload: dict) -> None:
    try:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        path = _status_path(payload)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _adb(serial: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [vmos_cloud.adb_bin(), "-s", serial, *args],
        capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
    )


def _sh(serial: str, cmd: str, timeout: int = 30) -> str:
    p = _adb(serial, "shell", cmd, timeout=timeout)
    return ((p.stdout or "") + (p.stderr or "")).strip()


def _vu(serial: str, cmd: str, timeout: int = 40) -> str:
    # 비-TTY: vu -c
    quoted = cmd.replace("'", "'\\''")
    return _sh(serial, f"vu -c '{quoted}'", timeout=timeout)


def _mitm_ca_pem() -> Path:
    for p in (
        Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem",
        Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.cer",
    ):
        if p.is_file():
            return p
    raise RuntimeError("mitmproxy CA 가 없습니다. 한 번 mitmdump 를 실행하세요.")


def _cert_hash_name(pem_path: Path) -> tuple[str, bytes]:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    raw = pem_path.read_bytes()
    if b"BEGIN CERTIFICATE" in raw:
        cert = x509.load_pem_x509_certificate(raw, default_backend())
    else:
        cert = x509.load_der_x509_certificate(raw, default_backend())
    subject_der = cert.subject.public_bytes()
    h = hashlib.md5(subject_der).digest()
    name = f"{struct.unpack('<I', h[:4])[0]:08x}.0"
    pem = cert.public_bytes(serialization.Encoding.PEM)
    return name, pem


def ensure_device() -> dict:
    forced = (os.environ.get("SHOPEE_SERIAL") or "").strip()
    sess = dict(vmos_cloud.load_session())
    if forced:
        sess["serial"] = forced
        if os.environ.get("VMOS_PAD_CODE"):
            sess["padCode"] = os.environ["VMOS_PAD_CODE"]
        if os.environ.get("SHOPEE_PAD_NAME"):
            sess["padName"] = os.environ["SHOPEE_PAD_NAME"]
    serial = sess.get("serial") or "localhost:51032"
    adb = vmos_cloud.adb_bin()
    for attempt in range(3):
        try:
            ping = subprocess.run(
                [adb, "-s", serial, "shell", "echo", "ok"],
                capture_output=True, text=True, timeout=8,
            )
            if ping.returncode == 0 and "ok" in (ping.stdout or ""):
                _log(f"  ADB 재사용 {serial}")
                sess["serial"] = serial
                return sess
        except Exception:
            pass
        if forced:
            try:
                subprocess.run([adb, "connect", serial], capture_output=True, timeout=12)
            except Exception:
                pass
            if attempt == 1:
                # 이 프로세스의 ADB_SERVER_PORT 만 다시 띄운다. 다른 폰 서버는 건드리지 않는다.
                try:
                    subprocess.run([adb, "kill-server"], capture_output=True, timeout=8)
                    time.sleep(0.5)
                    subprocess.run([adb, "start-server"], capture_output=True, timeout=12)
                    subprocess.run([adb, "connect", serial], capture_output=True, timeout=12)
                except Exception:
                    pass
            continue
        if attempt < 2:
            try:
                subprocess.run([adb, "kill-server"], capture_output=True, timeout=8)
                time.sleep(1.0)
                subprocess.run([adb, "start-server"], capture_output=True, timeout=12)
                subprocess.run([adb, "connect", serial], capture_output=True, timeout=8)
            except Exception:
                pass
    if forced:
        _log(f"  ADB 응답 없음 — 지정 시리얼 유지 {serial}")
        sess["serial"] = serial
        return sess
    try:
        return vmos_cloud.ensure_connected()
    except Exception as exc:
        _log(f"  ADB 재연결 실패 — 기존 시리얼 유지: {exc}")
        sess["serial"] = serial
        return sess


def install_system_ca(serial: str) -> str:
    """vu bind-mount 로 시스템 CA 와 Conscrypt 저장소에 mitm 인증서를 넣는다."""
    pem_path = _mitm_ca_pem()
    name, pem = _cert_hash_name(pem_path)
    local = RUNTIME / name
    RUNTIME.mkdir(parents=True, exist_ok=True)
    local.write_bytes(pem)
    dest = f"/data/local/tmp/{name}"
    push = _adb(serial, "push", str(local), dest)
    if push.returncode != 0:
        raise RuntimeError(f"CA push 실패: {push.stderr}")
    out = []
    listed = ""
    try:
        listed = _vu(serial, f"ls -l /system/etc/security/cacerts/{name} 2>/dev/null")
    except Exception as exc:
        _log(f"  CA 확인 생략: {exc}")
    system_ready = listed.strip().startswith(("-", "l")) and "No such" not in listed
    if system_ready:
        _log(f"  시스템 CA {name} 재사용")
    else:
        steps = [
            "mkdir -p /data/local/tmp/cacerts",
            "if [ ! -f /data/local/tmp/cacerts/.seeded ]; then "
            "cp -a /system/etc/security/cacerts/. /data/local/tmp/cacerts/ && "
            "touch /data/local/tmp/cacerts/.seeded; fi",
            f"cp {dest} /data/local/tmp/cacerts/{name}",
            f"chmod 644 /data/local/tmp/cacerts/{name}",
            "mount | grep -q ' /system/etc/security/cacerts ' || "
            "mount --bind /data/local/tmp/cacerts /system/etc/security/cacerts",
            f"ls -l /system/etc/security/cacerts/{name}",
        ]
        for cmd in steps:
            out.append(_vu(serial, cmd))
        _log(f"  시스템 CA {name} 설치")
    apex = (
        "if [ -d /apex/com.android.conscrypt/cacerts ]; then "
        "mkdir -p /data/local/tmp/cacerts-apex; "
        "if [ ! -f /data/local/tmp/cacerts-apex/.seeded ]; then "
        "cp -a /apex/com.android.conscrypt/cacerts/. /data/local/tmp/cacerts-apex/ && "
        "touch /data/local/tmp/cacerts-apex/.seeded; fi; "
        f"cp {dest} /data/local/tmp/cacerts-apex/{name}; "
        f"chmod 644 /data/local/tmp/cacerts-apex/{name}; "
        "chown root:root /data/local/tmp/cacerts-apex/*; "
        "chcon u:object_r:system_file:s0 /data/local/tmp/cacerts-apex/*; "
        "mount --bind /data/local/tmp/cacerts-apex /apex/com.android.conscrypt/cacerts; "
        "for z in $(pidof zygote64); do "
        "nsenter -t $z -m mount --bind /data/local/tmp/cacerts-apex /apex/com.android.conscrypt/cacerts; "
        "done; "
        "for z in $(pidof zygote); do "
        "nsenter -t $z -m mount --bind /data/local/tmp/cacerts-apex /apex/com.android.conscrypt/cacerts; "
        "done; "
        f"ls -l /apex/com.android.conscrypt/cacerts/{name}; "
        "else echo NO_APEX; fi"
    )
    apex_out = _vu(serial, apex, timeout=40)
    _log(f"  Conscrypt CA {name} {(apex_out or '').strip()[:300]}")
    _sh(serial, f"am force-stop {SHOPEE_PKG}")
    out.append(apex_out)
    return "\n".join(x for x in out if x)


def setup_reverse_proxy(serial: str, port: int) -> None:
    p = _adb(serial, "reverse", f"tcp:{port}", f"tcp:{port}")
    if p.returncode != 0:
        raise RuntimeError(f"adb reverse 실패: {(p.stderr or p.stdout).strip()}")
    listed = _adb(serial, "reverse", "--list")
    _log(f"  adb reverse tcp:{port} → 이 PC :{port}")
    extra = (listed.stdout or "").strip()
    if extra:
        _log(f"  reverse 목록: {extra.replace(chr(10), ' | ')}")
    _sh(serial, f"settings put global http_proxy 127.0.0.1:{port}")
    cur = _sh(serial, "settings get global http_proxy")
    _log(f"  http_proxy = {cur}")


def clear_proxy(serial: str, port: int) -> None:
    try:
        _sh(serial, "settings put global http_proxy :0")
        _sh(serial, "settings delete global global_http_proxy_host")
        _sh(serial, "settings delete global global_http_proxy_port")
        _adb(serial, "reverse", "--remove", f"tcp:{port}")
    except Exception:
        pass


def _mitmdump_bin() -> str:
    name = "mitmdump.exe" if os.name == "nt" else "mitmdump"
    beside = Path(sys.executable).parent / "Scripts" / name
    if beside.is_file():
        return str(beside)
    if MITMDUMP.is_file():
        return str(MITMDUMP)
    py = Path(_working_python())
    exe = py.parent / "Scripts" / name
    if exe.is_file():
        return str(exe)
    raise RuntimeError("mitmdump 실행 파일이 없습니다.")


def pick_br_upstream() -> str:
    crawler = str(_save.ROOT / "shopee_getpc_crawler")
    if crawler not in sys.path:
        sys.path.insert(0, crawler)
    import proxy_pool  # noqa: E402
    proxy_pool.set_region("BR")
    url, key, left = proxy_pool.consume_next()
    if not url:
        raise RuntimeError("BR 프록시 풀이 비어 있습니다.")
    host = url.split("@")[-1] if "@" in url else url
    _log(f"  소비 프록시  {key}  → {host}  남은 {left}")
    (RUNTIME / "upstream.txt").write_text(url, encoding="utf-8")
    return url


def _proxy_pool():
    crawler = str(_save.ROOT / "shopee_getpc_crawler")
    if crawler not in sys.path:
        sys.path.insert(0, crawler)
    import proxy_pool  # noqa: E402
    return proxy_pool


_PROBE_PDP = (
    "https://mall.shopee.tw/api/v4/pdp/get?item_id=18481094241&shop_id=300846271"
)


def _egress_clean(proxy_url: str, timeout: float = 12.0) -> bool:
    """이 출구로 쇼피 상품 API 가 90309999 없이 열리는지 확인한다."""
    import urllib.request

    handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(
        _PROBE_PDP,
        headers={"User-Agent": "Mozilla/5.0", "x-api-source": "rn"},
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(500)
            if resp.status != 200 or b"90309999" in body:
                return False
            return b'"item"' in body
    except Exception:
        return False


def pick_rotating_upstream(region: str = "TW") -> str:
    """새 세션마다 출구 IP 를 바꾸고, 쇼피가 막지 않는 IP 만 고른다.

    같은 대만 주거용 풀이어도 출구마다 90309999 가 바로 걸린다.
    막힌 출구를 앱에 붙이면 상품 응답이 저장되지 않으므로, 붙이기 전에 버린다.
    """
    pp = _proxy_pool()
    pp.set_region(region)
    last_err = "깨끗한 출구 없음"
    for n in range(1, 21):
        url = pp.pick()
        host = url.split("@")[-1] if "@" in url else url
        if _egress_clean(url):
            _log(f"  회전 프록시 {region} 출구 통과 {n}회차 → {host[:56]}")
            (RUNTIME / "upstream.txt").write_text(url, encoding="utf-8")
            return url
        last_err = "90309999"
        _log(f"  출구 차단 — {n}회차 버리고 다음 IP")
    raise RuntimeError(f"{region} 프록시 출구 검사 실패: {last_err}")


def kill_mitmdump(port: int | None = None) -> None:
    """지정 포트의 mitmdump 만 끈다. 포트가 없으면 다른 폰 것을 끄지 않는다."""
    if port is None or os.name != "nt":
        return
    needle = f"--listen-port {port}"
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'mitmdump.exe' -and $_.CommandLine -like '*"
         + needle + "*' } | Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=20,
    )
    for token in (out.stdout or "").split():
        if token.isdigit():
            subprocess.run(["taskkill", "/F", "/PID", token], capture_output=True, text=True)
    time.sleep(0.4)


def _mitm_listening(port: int) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def start_mitm(port: int, upstream: str | None = None, *, reuse: bool = False) -> subprocess.Popen | None:
    want = (os.environ.get("SHOPEE_OUT_DIR") or "").strip()
    marker = RUNTIME / f"mitm_out_{port}.txt"
    if reuse and _mitm_listening(port):
        try:
            same = marker.read_text(encoding="utf-8").strip() == want
        except OSError:
            same = False
        if same:
            _log(f"  mitmdump 재사용  port={port}  (프록시 끊지 않음)")
            return None
        _log(f"  mitmdump 저장 폴더가 달라 다시 시작  port={port}")
    kill_mitmdump(port)
    marker.write_text(want, encoding="utf-8")
    dump = _mitmdump_bin()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    logf = MITM_LOG.open("w", encoding="utf-8", errors="replace")
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        dump,
        "-s", str(ADDON),
        "--listen-host", "127.0.0.1",
        "--listen-port", str(port),
        "--set", "stream_large_bodies=2m",
        "--set", "flow_detail=0",
        "--set", "http2=true" if MITM_HTTP2 else "http2=false",
        "--ignore-hosts", r"google\.com",
        "--ignore-hosts", r"googleapis\.com",
        "--ignore-hosts", r"gstatic\.com",
        "--ignore-hosts", r"googleusercontent\.com",
        "-q",
    ]
    if upstream:
        from urllib.parse import urlsplit
        u = urlsplit(upstream)
        server = f"{u.scheme or 'http'}://{u.hostname}:{u.port or 80}"
        cmd.extend(["--mode", f"upstream:{server}"])
        if u.username:
            auth = f"{u.username}:{u.password or ''}"
            cmd.extend(["--set", f"upstream_auth={auth}"])
    proc = subprocess.Popen(
        cmd,
        cwd=str(_DIR.parent),
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    time.sleep(1.0)
    if proc.poll() is not None:
        logf.close()
        tail = MITM_LOG.read_text(encoding="utf-8", errors="replace")[-800:]
        raise RuntimeError(f"mitmdump 즉시 종료 (포트 {port})\n{tail}")
    _log(f"  mitmdump pid={proc.pid}  port={port}  http2={'on' if MITM_HTTP2 else 'off'}")
    return proc


def stop_mitm(proc: subprocess.Popen | None) -> None:
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()


def _leave_to_launcher(serial: str) -> None:
    _sh(serial, "input keyevent KEYCODE_HOME")
    time.sleep(0.35)


def _back_from_product(serial: str) -> None:
    """PDP에서 사람처럼 뒤로 간다. 강제종료·홈 재기동 없음."""
    for _ in range(2):
        _sh(serial, "input keyevent KEYCODE_BACK")
        time.sleep(0.5)
    time.sleep(0.8)


def _human_browse_reset(serial: str, port: int) -> None:
    """사람처럼 상품에서 빠져나와 홈에서 잠깐 훑는다. 강제종료·콜드재기동 없음."""
    try:
        _back_from_product(serial)
        _ensure_home_ready(serial, port, timeout=6.0)
        # 홈 피드를 한두 번 스크롤(사람스러운 탐색 흔적)
        for _ in range(random.randint(1, 2)):
            _sh(serial, "input swipe 360 950 360 400 400")
            time.sleep(random.uniform(0.8, 1.8))
    except Exception as exc:
        _log(f"  인앱 리셋 예외: {exc}")


def _warm_app(serial: str, port: int, home_s: float) -> None:
    _sh(serial, f"am force-stop {SHOPEE_PKG}")
    time.sleep(1.0)
    try:
        setup_reverse_proxy(serial, port)
    except Exception as exc:
        _log(f"  프록시 재설정 실패: {exc}")
    _sh(serial, f"am start -n {HOME_ACT}")
    time.sleep(home_s)


def _homepage_seen() -> bool:
    try:
        tail = MITM_LOG.read_text(encoding="utf-8", errors="replace")[-8000:]
    except Exception:
        return False
    return "native/homepage" in tail and "err=0" in tail


def _ensure_home_ready(
    serial: str, port: int, timeout: float = 8.0, *, refresh_proxy: bool = False,
) -> None:
    """프로세스를 죽이지 않고 홈이 뜰 때까지 기다린다."""
    if refresh_proxy:
        try:
            setup_reverse_proxy(serial, port)
        except Exception as exc:
            _log(f"  프록시 재설정 실패: {exc}")
    try:
        _sh(serial, "input keyevent KEYCODE_BACK")
    except Exception:
        pass
    if not _sh(serial, f"pidof {SHOPEE_PKG}").strip():
        _sh(serial, f"am start -n {HOME_ACT}")
    else:
        _sh(serial, f"am start -n {HOME_ACT}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _homepage_seen():
            _log("  홈 준비 확인 (native/homepage)")
            return
        time.sleep(0.6)
    _log("  홈 대기 시간 끝 — 상품 진행")


def tap_reload(serial: str) -> None:
    """로드 실패 팝업의 Recarregar (오른쪽 버튼)."""
    _sh(serial, "input tap 810 1200")
    time.sleep(0.25)


def _view(serial: str, uri: str, *, reset_task: bool = False, stop_first: bool = False) -> str:
    q = uri.replace("&", "\\&")
    # 이 기기 am 은 --activity-* 를 모름. 0x10008000 = NEW_TASK|CLEAR_TASK
    # -S : 시작 전 프로세스 중지. 켜 둔 앱은 VIEW 를 무시하므로 상품마다 사용.
    flags = " -f 0x10008000" if reset_task else ""
    stop = " -S" if stop_first else ""
    return _sh(
        serial,
        f'am start{stop}{flags} -n {PROXY_ACT} '
        f'-a android.intent.action.VIEW '
        f'-c android.intent.category.BROWSABLE -d "{q}"',
    )


def top_activity(serial: str) -> str:
    try:
        out = _sh(serial, "dumpsys activity activities", timeout=8)
    except Exception:
        out = ""
    for key in ("topResumedActivity", "mResumedActivity"):
        for ln in out.splitlines():
            if key in ln:
                return ln
    try:
        out = _sh(serial, "dumpsys window windows", timeout=6)
    except Exception:
        return ""
    for key in ("mCurrentFocus", "mFocusedApp"):
        for ln in out.splitlines():
            if key in ln:
                return ln
    return ""


def on_traffic_trap(serial: str) -> bool:
    # dumpsys 는 ADB 터널을 자주 멈추므로 mitm 로그만 본다.
    try:
        tail = MITM_LOG.read_text(encoding="utf-8", errors="replace")[-6000:]
    except Exception:
        return False
    return any(x in tail for x in ("/verify", "traffic/error", "error/traffic"))


_LAST_TRAFFIC_LINE = ""


def _traffic_error_since(offset: int) -> bool:
    """이 구간 로그에 「Página indisponível」(verify/traffic/error) 가 있는지."""
    blob = _mitm_since(offset)
    return "verify/traffic/error" in blob or "TRAFFIC-ERROR" in blob


def _logged_out_since(offset: int) -> bool:
    """상품 페이지가 로그인되지 않았다고 답한 경우. 계정 교체 사유가 아니다."""
    return "is_logged_in=false" in _mitm_since(offset)


def _account_ready(offset: int) -> bool:
    """홈 응답이 아니라 계정 조회가 err=0 일 때만 로그인으로 본다.

    홈은 로그아웃 상태에서도 err=0 이다. 계정 조회 err=19 는 로그인 안 됨.
    """
    return _account_state(offset) == "in"


def _account_state(offset: int) -> str:
    """계정 조회의 마지막 결과. in=err=0, out=그 외 또는 로그아웃, 빈문자=아직 없음."""
    state = ""
    for line in _mitm_since(offset).splitlines():
        if "get_account_info" in line and "err=" in line:
            match = re.search(r"err=(\d+)", line)
            if match:
                state = "in" if match.group(1) == "0" else "out"
        elif "get_login_nonce" in line and '"error"' in line:
            match = re.search(r'"error"\s*:\s*(\d+)', line)
            if match:
                state = "in" if match.group(1) == "0" else "out"
        elif "is_logged_in=false" in line:
            state = "out"
    return state


def _await_account(serial: str, timeout: float = 8.0) -> str:
    """홈만 열어 계정 조회를 본다. 로그아웃이 보이면 기다리지 않고 바로 돌아온다."""
    mark = _mitm_size()
    try:
        _sh(serial, f"am start -n {HOME_ACT}")
    except Exception as exc:
        _log(f"  홈 확인 생략: {exc}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = _account_state(mark)
        if state:
            return state
        time.sleep(0.4)
    return ""


def recover_login(serial: str) -> bool:
    """로그인 필요 창을 홈으로 닫고, 앱을 죽이지 않은 채 로그인을 확인한다."""
    _log("  로그인 필요 창 — 홈에서 같은 계정을 확인")
    mark = _mitm_size()
    leave_login_wall(serial)
    deadline = time.time() + 12
    while time.time() < deadline:
        if _account_ready(mark):
            _log("  홈 로그인 확인 — 같은 계정으로 상품을 연다")
            return True
        time.sleep(0.8)
    _log("  홈에서도 로그인되지 않음")
    return False


def _logcat_traffic_error(serial: str) -> bool:
    """가장 최근 웹뷰 로드가 verify/traffic/error 이면 True. 같은 줄은 한 번만."""
    global _LAST_TRAFFIC_LINE
    try:
        out = _sh(
            serial,
            "logcat -d -s System.out:I -e onPageFinished",
            timeout=12,
        )
    except Exception:
        return False
    lines = [ln for ln in (out or "").splitlines() if "onPageFinished" in ln]
    if not lines:
        return False
    last = lines[-1]
    if "verify/traffic/error" not in last:
        return False
    if last == _LAST_TRAFFIC_LINE:
        return False
    _LAST_TRAFFIC_LINE = last
    return True


def _top_focus(serial: str) -> str:
    """지금 앞에 있는 창. 뒤에 남은 WebPageActivity 는 캡차로 보지 않는다."""
    try:
        out = _sh(
            serial,
            "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'",
            timeout=10,
        )
    except Exception:
        return ""
    current = ""
    focused = ""
    for ln in (out or "").splitlines():
        if "mCurrentFocus" in ln and "null" not in ln:
            current = ln
        elif "mFocusedApp" in ln:
            focused = ln
    return current or focused


def _error_page_foreground(serial: str) -> bool:
    """지금 화면이 Shopee WebPageActivity 인지. 지나간 로그만으로 오인하지 않는다."""
    return "WebPageActivity_" in _top_focus(serial)


def _screen_words(serial: str) -> list[str]:
    """지금 화면에 보이는 글자. 캡차 웹뷰가 빈 로고만 띄울 때를 가린다."""
    try:
        _sh(serial, "uiautomator dump /sdcard/_ui.xml", timeout=20)
        xml = _sh(serial, "cat /sdcard/_ui.xml", timeout=12)
    except Exception:
        return []
    return [part.strip() for part in re.findall(r'text="([^"]*)"', xml or "") if part.strip()]


def _captcha_ui_state(words: list[str]) -> str:
    """puzzle=넘길 캡차, expired=시간 만료, blank=제목만 있는 검증, missing=없는 상품."""
    if not words:
        return ""
    blob = " ".join(words).lower()
    if "não existe" in blob or "nao existe" in blob:
        return "missing"
    if "expirou" in blob or "tempo esgotado" in blob:
        return "expired"
    if any(token in blob for token in ("deslize", "arraste", "selecione", "quebra-cabe")):
        return "puzzle"
    kept = []
    for word in words:
        low = word.lower()
        if low.startswith("verifica") or low == "voltar" or low.startswith("id:"):
            continue
        kept.append(word)
    if not kept and any(word.lower().startswith("verifica") for word in words):
        return "blank"
    return ""


def _screen_traffic_error(serial: str) -> bool:
    """「Página indisponível」이 지금 앞에 있을 때만 True."""
    if not _error_page_foreground(serial):
        return False
    return _logcat_traffic_error(serial)


def egress_blocked_since(offset: int, serial: str = "") -> bool:
    """출구 차단은 traffic error 만 본다. pdp 418·캡차·로그인 실패는 여기 넣지 않는다."""
    if _traffic_error_since(offset):
        return True
    return bool(serial) and _screen_traffic_error(serial)


def _captcha_on_screen(serial: str) -> bool:
    """넘길 캡차가 앞에 있으면 True. 상품 요청으로 이 화면을 치우지 않는다."""
    return _captcha_ui_state(_screen_words(serial)) in {"puzzle", "blank"}


def _captcha_session_visible(serial: str) -> bool:
    """실패·만료 후 재시도 포함, 수동 캡차 웹뷰가 아직 앞에 있는지."""
    if not serial:
        return False
    state = _captcha_ui_state(_screen_words(serial))
    if state in {"puzzle", "blank", "expired"}:
        return True
    if _error_page_foreground(serial):
        blob = " ".join(_screen_words(serial)).lower()
        if "verifica" in blob:
            return True
    return False


def _login_required(serial: str) -> bool:
    """「Login Necessário」창. 상품 페이지가 로그인되지 않았다고 볼 때."""
    blob = " ".join(_screen_words(serial)).lower()
    return (
        "não está logado" in blob
        or "nao esta logado" in blob
        or "login necessário" in blob
        or "login necessario" in blob
    )


def leave_login_wall(serial: str) -> bool:
    """Entrar 는 누르지 않고 홈으로만 돌아간다."""
    try:
        _sh(serial, "uiautomator dump /sdcard/_ui.xml", timeout=20)
        xml = _sh(serial, "cat /sdcard/_ui.xml", timeout=12) or ""
    except Exception:
        xml = ""
    for tag in re.findall(r"<node\b[^>]*>", xml):
        text_m = re.search(r'text="([^"]*)"', tag)
        bounds_m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
        if not text_m or not bounds_m:
            continue
        text = text_m.group(1).lower()
        if "entrar" in text and "inicial" not in text:
            continue
        if "página inicial" not in text and "pagina inicial" not in text:
            continue
        x1, y1, x2, y2 = (int(bounds_m.group(i)) for i in range(1, 5))
        _sh(serial, f"input tap {(x1 + x2) // 2} {(y1 + y2) // 2}")
        time.sleep(1.2)
        return True
    _sh(serial, f"am start -n {HOME_ACT}")
    time.sleep(1.2)
    return False


def _consume_traffic_log(serial: str) -> None:
    """처리한 뒤 같은 화면 로그로 로그인을 반복하지 않게 버퍼를 비운다."""
    global _LAST_TRAFFIC_LINE
    _LAST_TRAFFIC_LINE = ""
    try:
        _sh(serial, "logcat -c", timeout=8)
    except Exception:
        pass


def recover_session(serial: str, port: int, *, rest_s: float) -> None:
    _log(f"  트래픽 검증/웹뷰 — 앱 재시작 후 홈에서 {rest_s:.0f}s 휴식")
    _sh(serial, f"am force-stop {SHOPEE_PKG}")
    time.sleep(1.5)
    try:
        setup_reverse_proxy(serial, port)
    except Exception as exc:
        _log(f"  프록시 재설정 실패: {exc}")
    _sh(serial, f"am start -n {HOME_ACT}")
    time.sleep(rest_s)


def _drop_stuck_zygote(serial: str) -> None:
    """force-stop 이 못 죽이는 com.shopee.br_zygote 가 남으면 스플래시에서 멈춘다."""
    try:
        raw = _sh(serial, f"pidof {SHOPEE_PKG}_zygote", timeout=8).strip()
    except Exception:
        return
    pids = [part for part in raw.split() if part.isdigit()]
    if not pids:
        return
    _log(f"  멈춘 앱 프로세스 정리 {SHOPEE_PKG}_zygote pid={' '.join(pids)}")
    try:
        _vu(serial, "kill -9 " + " ".join(pids), timeout=12)
    except Exception as exc:
        _log(f"  zygote 정리 실패: {exc}")


def open_product(
    serial: str, shop_id: int, item_id: int,
    *, restart: bool = False, reset_task: bool = False, leave: bool = False,
) -> str:
    url = f"https://{SHOPEE_WEB_HOST}/product/{shop_id}/{item_id}"
    if leave and not restart:
        _leave_to_launcher(serial)
    if restart:
        _drop_stuck_zygote(serial)
    out = _view(serial, url, reset_task=reset_task, stop_first=restart)
    if "Error" in out or "Exception" in out or "Warning" in out:
        _log(f"  인텐트 응답: {out[:200]}")
    return url


def _mitm_size() -> int:
    try:
        return MITM_LOG.stat().st_size
    except OSError:
        return 0


def _mitm_has_pdp(item_id: int, offset: int) -> bool:
    needle = str(item_id)
    for ln in _mitm_since(offset).splitlines():
        if ("/pdp/get?" in ln or "/pdp/get " in ln) and needle in ln:
            return True
    return False


def _home_seen(offset: int) -> bool:
    for ln in _mitm_since(offset).splitlines():
        if "native/homepage" in ln or "get_login_nonce" in ln:
            return True
    return False


def _response_flags(offset: int) -> set[str]:
    """이 구간의 응답 성격. captcha/block 이면 앱을 재기동하면 안 된다.

    * ``block``   — anti-crawler 418/90309999 (재시도·IP 교체 대상)
    * ``captcha`` — 캡차/안티프로드 창
    * ``silent``  — pop_up=false 소프트 차단(스켈레톤)
    * ``gone``    — PDP 가 item not found(266900002) 로 응답 → 상품 삭제/없음
    """
    flags: set[str] = set()
    for ln in _mitm_since(offset).splitlines():
        low = ln.lower()
        if "captcha" in low or "anti_fraud" in low or "anti_crawler" in low:
            flags.add("captcha")
        if "err=90309999" in ln or "st=418" in ln:
            flags.add("block")
        # 상품없음: mitm 의 PDP 응답 라인(/pdp/get)에서 전용 에러 코드일 때만.
        if ("/pdp/get" in ln or "PDP-GONE" in ln) and any(
            f"err={code}" in ln for code in _GONE_ERR_CODES
        ):
            flags.add("gone")
        if "PDP-BLOCK" in ln and "pop_up=True" in ln:
            flags.add("captcha")
        if "PDP-BLOCK" in ln and "pop_up=False" in ln:
            flags.add("silent")
    return flags


def _is_gone(flags: set[str]) -> bool:
    """상품없음 확정: gone 신호가 있고, 차단/캡차가 섞이지 않았을 때만.

    418·캡차가 함께 잡히면 실제 원인이 차단일 수 있으므로 상품없음으로
    단정하지 않고 미저장(재시도)으로 둔다.
    """
    return "gone" in flags and "block" not in flags and "captcha" not in flags


def wait_saved(
    shop_id: int, item_id: int, timeout: float,
    *, offset: int = 0, abort_no_pdp: float | None = None,
) -> Path | None:
    path = _save.out_path(shop_id, item_id)
    deadline = time.time() + timeout
    abort_at = (time.time() + abort_no_pdp) if abort_no_pdp is not None else None
    while time.time() < deadline:
        if path.is_file():
            return path
        if _traffic_error_since(offset):
            return None
        if abort_at is not None and time.time() >= abort_at:
            if _mitm_has_pdp(item_id, offset):
                abort_at = None
            elif _home_seen(offset):
                return None
        time.sleep(POLL_S)
    return path if path.is_file() else None


def load_targets(path: Path) -> list[tuple[int, int, str]]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    out: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    for ln in raw.splitlines():
        ids = _save.parse_ids(ln)
        if not ids:
            continue
        sid, iid = ids
        if (sid, iid) in seen:
            continue
        seen.add((sid, iid))
        out.append((sid, iid, _save.product_url(sid, iid)))
    return out


def copy_runtime(src: Path, shop_id: int, item_id: int) -> Path:
    dest = RUNTIME / f"product_{shop_id}_{item_id}.json"
    shutil.copy2(src, dest)
    return dest


def capture_one(
    serial: str, shop_id: int, item_id: int,
    *, restart: bool, wait_s: float, copy: bool, reset_task: bool = False,
    reload: bool = False, abort_no_pdp: float | None = None,
) -> dict:
    offset = _mitm_size()
    url = open_product(serial, shop_id, item_id, restart=restart, reset_task=reset_task)
    t0 = time.time()
    abort = abort_no_pdp if abort_no_pdp is not None else (HOME_ABORT_S if not restart else None)
    budget = max(wait_s, COLD_START_WAIT_S) if restart else wait_s
    path = wait_saved(shop_id, item_id, budget, offset=offset, abort_no_pdp=abort)
    rec = {
        "ok": bool(path),
        "shop_id": shop_id,
        "item_id": item_id,
        "url": url,
        "sec": round(time.time() - t0, 2),
        "source": "emulator_pdp_get",
        "traffic_error": _traffic_error_since(offset),
        "logged_out": _logged_out_since(offset),
    }
    if path:
        rec["saved"] = str(path)
        rec["outcome"] = "saved"
        if copy:
            rec["runtime"] = str(copy_runtime(path, shop_id, item_id))
        _log(f"✅ i.{shop_id}.{item_id}  {rec['sec']}s")
    else:
        flags = _response_flags(offset)
        if _is_gone(flags):
            rec["outcome"] = "gone"
            rec["error"] = f"상품 없음({_GONE_ERR_CODES[0]})"
            _log(f"∅ i.{shop_id}.{item_id}  {rec['sec']}s 상품없음")
        else:
            rec["outcome"] = "missed"
            rec["error"] = "앱 pdp/get 미수신"
            _log(f"… i.{shop_id}.{item_id}  {rec['sec']}s 미저장")
    return rec


def capture(shop_id: int, item_id: int, *, port: int = DEFAULT_PORT, keep_proxy: bool = False, wait_s: float = WAIT_S) -> dict:
    return capture_many(
        [(shop_id, item_id, _save.product_url(shop_id, item_id))],
        port=port, keep_proxy=keep_proxy, wait_s=wait_s, copy_runtime_each=True,
    )


def _login_seen_in_mitm() -> bool:
    try:
        tail = MITM_LOG.read_text(encoding="utf-8", errors="replace")[-12000:]
    except Exception:
        return False
    if "userid=0" in tail and "get_account_info" not in tail:
        return False
    return "get_account_info" in tail and "err=0" in tail


_PDP_LOG_OFFSET = 0
_SOLVED_CAPTCHA = False
_HOLD_FOR_CAPTCHA = False


def _mitm_since(offset: int) -> str:
    """offset 는 파일 바이트 위치(_mitm_size).

    로그에 한글이 있으면 바이트 수와 글자 수가 달라진다.
    글자 수로 자르면 그 차이만큼 뒤의 418·캡차 줄이 안 보여,
    캡차가 뜨기 전에 미저장으로 재기동한다.
    """
    try:
        with MITM_LOG.open("rb") as fh:
            fh.seek(max(0, int(offset)))
            data = fh.read()
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def pdp_blocked() -> bool:
    """이번 수집 시작 이후의 상품 /pdp/get 418/90309999 만 본다. get_vsku 는 제외."""
    for ln in _mitm_since(_PDP_LOG_OFFSET).splitlines():
        if "/pdp/get?" not in ln and "/pdp/get " not in ln:
            continue
        if "err=90309999" in ln or "st=418" in ln:
            return True
    return False


def _last_pdp_block_line() -> str:
    last = ""
    for ln in _mitm_since(_PDP_LOG_OFFSET).splitlines():
        if "PDP-BLOCK" in ln:
            last = ln
    return last


def silent_block() -> bool:
    """이번 구간의 마지막 PDP 차단이 '무음 소프트차단'(pop_up=false)인지.

    Shopee 가 캡차를 아예 띄우지 않는(pop_up=false, mfr_captcha 없음) 차단이면
    화면에서 넘길 수 있는 것이 없으므로, 캡차 대기 대신 계정/기기 교체가 답이다.
    """
    last = _last_pdp_block_line()
    if not last:
        return False
    return "pop_up=False" in last and "captcha=True" not in last


def popup_captcha_expected() -> bool:
    """넘길 캡차 창이 뜨는 418 인지. pop_up=false 이면 상품 스켈레톤만 보일 수 있다."""
    last = _last_pdp_block_line()
    if not last:
        # 메타가 없으면 418 만으로 캡cha 대기하지 않는다 (홈·스켈레톤만 보이는 경우가 많음).
        since = _mitm_since(_PDP_LOG_OFFSET)
        if "anti_fraud/captcha/generate" in since or "anti_crawler/verify_signature" in since:
            return True
        return False
    if "pop_up=True" in last or "captcha=True" in last:
        return True
    if "pop_up=False" in last and "captcha=False" in last:
        return False
    return "pop_up=False" not in last


def _home_foreground(serial: str) -> bool:
    return "HomeActivity_" in _top_focus(serial)


def _mitm_login_wall_since(offset: int) -> bool:
    blob = _mitm_since(offset)
    return "verify/traffic/error" in blob and "is_logged_in=false" in blob


def _captcha_passed(since: int) -> bool:
    """마지막 418 이후에 상품 pdp/get 이 200 이거나, 그 뒤에 캡차 검증이 끝난 경우.

    검증 뒤에 다시 418 이 오면 아직 풀린 것이 아니다. 그때 통과로 보면
    기록을 지우고 계정을 바꿔 방금 넘긴 캡차가 사라진다.
    """
    lines = _mitm_since(since).splitlines()
    last_418 = -1
    for i, ln in enumerate(lines):
        if "/pdp/get?" not in ln and "/pdp/get " not in ln:
            continue
        if "err=90309999" in ln or "st=418" in ln:
            last_418 = i
    if last_418 < 0:
        return False
    after = lines[last_418 + 1:]
    if any(("/pdp/get?" in ln or "/pdp/get " in ln) and "st=200" in ln for ln in after):
        return True
    blob = "\n".join(after)
    last_gen = blob.rfind("anti_fraud/captcha/generate")
    last_ver = max(
        blob.rfind("anti_fraud/captcha/verify_v2"),
        blob.rfind("anti_crawler/verify_signature"),
    )
    return last_ver >= 0 and last_gen <= last_ver


def _captcha_attempted(since: int) -> bool:
    blob = _mitm_since(since)
    return "anti_fraud/captcha/verify_v2" in blob or "anti_fraud/captcha/generate" in blob


def wait_manual_captcha(
    *, timeout: float = CAPTCHA_WAIT_S, on_tick=None, serial: str | None = None,
) -> str:
    """418 캡차 화면을 사용자가 넘길 때까지 앱을 건드리지 않고 대기.

    pop_up=false 418 은 캡차 창이 아니라 상품 스켈레톤만 보일 수 있다 — 대기하지 않는다.
    로그인 필요(verify/traffic/error) 도 캡차가 아니다.
    한 번 넘기다 실패해도 앱을 재기동하지 않고 같은 창에서 통과할 때까지 유지한다.
    """
    global _PDP_LOG_OFFSET, _SOLVED_CAPTCHA, _HOLD_FOR_CAPTCHA
    on_screen = bool(serial and _captcha_session_visible(serial))
    if _mitm_login_wall_since(_PDP_LOG_OFFSET):
        _log("  로그인 필요 화면 — 캡차 대기 없이 다음 보관 세션")
        return "login"
    if not on_screen and (silent_block() or not popup_captcha_expected()):
        _log("  팝업 캡차 없는 418 — 캡차 대기하지 않음")
        _PDP_LOG_OFFSET = _mitm_size()
        return "skip"
    if serial and _login_required(serial):
        _log("  로그인 필요 창 — 캡차 대기 없이 다음 보관 세션")
        return "login"
    if serial and _screen_traffic_error(serial):
        _log("  페이지 사용 불가 화면 — 캡차 대기 없이 새 로그인")
        return "relogin"
    _HOLD_FOR_CAPTCHA = True
    wait_mitm = _PDP_LOG_OFFSET
    _log("  PDP 418 — 캡차 화면을 수동으로 넘기세요. 넘길 때까지 대기 (앱 재기동 없음)")
    deadline = time.time() + timeout
    last_note = 0.0
    last_expired_note = 0.0
    while time.time() < deadline:
        if serial and _home_foreground(serial) and not _captcha_session_visible(serial):
            if not popup_captcha_expected() and not _captcha_attempted(wait_mitm):
                _log("  홈 화면 — 넘길 캡차가 없어 대기를 중단")
                _PDP_LOG_OFFSET = _mitm_size()
                _HOLD_FOR_CAPTCHA = False
                return "skip"
        if _mitm_login_wall_since(_PDP_LOG_OFFSET):
            _log("  로그인 필요 화면 — 캡차 대기 중단")
            _HOLD_FOR_CAPTCHA = False
            return "login"
        ui_hold = bool(serial and _captcha_session_visible(serial))
        tried = _captcha_attempted(wait_mitm)
        if not ui_hold and not tried and (silent_block() or not popup_captcha_expected()):
            _log("  팝업 캡차 없는 418 — 캡차 대기 중단")
            _PDP_LOG_OFFSET = _mitm_size()
            _HOLD_FOR_CAPTCHA = False
            return "skip"
        if _captcha_passed(_PDP_LOG_OFFSET):
            if pdp_blocked() and silent_block():
                _log("  검증은 됐지만 상품 API는 여전히 팝업 없이 막힘 — 이 상품은 넘김")
                _PDP_LOG_OFFSET = _mitm_size()
                _HOLD_FOR_CAPTCHA = False
                return "skip"
            _PDP_LOG_OFFSET = _mitm_size()
            _SOLVED_CAPTCHA = True
            _HOLD_FOR_CAPTCHA = False
            _log("  캡차 통과 확인 — 수집 재개")
            return "passed"
        now = time.time()
        if serial and _captcha_ui_state(_screen_words(serial)) == "expired":
            if now - last_expired_note >= 20:
                _log("  캡차 시간 만료 — 같은 창에서 다시 넘기세요 (앱 유지)")
                last_expired_note = now
        if now - last_note >= 15:
            _log(f"  캡차 대기 중… 남은 {int(deadline - now)}s")
            last_note = now
            if on_tick:
                on_tick()
        time.sleep(1.0)
    _log("  캡차 대기 구간 종료 — 화면 유지, 통과할 때까지 재개")
    return "timeout"


def _account_userid_on_device(serial: str) -> str:
    try:
        names = _vu(
            serial,
            f"ls /data/data/{SHOPEE_PKG}/shared_prefs 2>/dev/null",
            timeout=10,
        )
    except Exception:
        names = ""
    for m in re.findall(r"app_status_(\d{8,})\.xml", names or ""):
        if m != "0":
            return m
    try:
        out = _vu(
            serial,
            "grep -R -h -E 'user_id|userid|SPC_U' "
            f"/data/data/{SHOPEE_PKG}/shared_prefs 2>/dev/null | head -n 40",
            timeout=12,
        )
    except Exception:
        out = ""
    for m in re.findall(r">(\d{6,})<", out or ""):
        if m != "0":
            return m
    return ""


def _login_pref_string(serial: str, key: str) -> str:
    """shared_prefs/login.xml 의 string 항목."""
    try:
        out = _vu(
            serial,
            f"cat /data/data/{SHOPEE_PKG}/shared_prefs/login.xml 2>/dev/null",
            timeout=15,
        )
    except Exception:
        out = ""
    if not out:
        return ""
    m = re.search(rf'<string name="{re.escape(key)}">([^<]*)</string>', out)
    return m.group(1).strip() if m else ""


def _username_from_session_tgz(uid: str) -> str:
    """PC에 있는 보관 파일에서 username 을 읽는다 (기기 조회 실패 시)."""
    path = SESSION_DIR / f"{uid}.tgz"
    if not path.is_file():
        return ""
    try:
        import tarfile

        with tarfile.open(path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.name.endswith("shared_prefs/login.xml"):
                    continue
                raw = tar.extractfile(member)
                if not raw:
                    continue
                text = raw.read().decode("utf-8", errors="replace")
                m = re.search(r'<string name="username">([^<]*)</string>', text)
                if m:
                    return m.group(1).strip()
    except (OSError, tarfile.TarError):
        pass
    return ""


def _account_username_on_device(serial: str) -> str:
    """shared_prefs/login.xml 의 Shopee 사용자 이름(로그인 ID)."""
    name = _login_pref_string(serial, "username")
    if name:
        return name
    uid = _account_userid_on_device(serial)
    if uid:
        return _username_from_session_tgz(uid)
    return ""


def _write_session_meta(uid: str, *, bytes_size: int, username: str = "") -> None:
    path = SESSION_DIR / f"{uid}.json"
    prev: dict = {}
    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}
    name = (username or str(prev.get("username") or "")).strip()
    if not name:
        name = _username_from_session_tgz(uid)
    payload: dict = {
        "userid": uid,
        "bytes": bytes_size,
        "saved_at": time.time(),
    }
    if name:
        payload["username"] = name
    phone = (os.environ.get("SHOPEE_PAD_NAME") or str(prev.get("phone") or "")).strip()
    if phone:
        payload["phone"] = phone
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def session_rotate_min() -> int:
    try:
        n = int(os.environ.get("SHOPEE_SESSION_ROTATE_MIN") or SESSION_ROTATE_MIN)
    except ValueError:
        n = SESSION_ROTATE_MIN
    return max(1, n)


def session_rotation_enabled() -> bool:
    return len(list_sessions()) >= session_rotate_min()


def _sessions_oldest_first(uids: list[str]) -> list[str]:
    return sorted(uids, key=lambda uid: (_session_saved_at(uid), uid))


def _session_used_today(uid: str) -> bool:
    phone = os.environ.get("SHOPEE_PAD_NAME") or ""
    try:
        return _session_book().used_today(uid, phone)
    except Exception:
        return False


def list_sessions() -> list[str]:
    if not SESSION_DIR.is_dir():
        return []
    found = sorted(p.stem for p in SESSION_DIR.glob("*.tgz") if p.stem.isdigit())
    allow = {
        part.strip()
        for part in os.environ.get("SHOPEE_SESSION_ALLOW", "").split(",")
        if part.strip()
    }
    if allow:
        found = [uid for uid in found if uid in allow]
    return found


def _session_saved_at(uid: str) -> float:
    meta = SESSION_DIR / f"{uid}.json"
    if meta.is_file():
        try:
            return float(json.loads(meta.read_text(encoding="utf-8")).get("saved_at") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    path = SESSION_DIR / f"{uid}.tgz"
    if path.is_file():
        return path.stat().st_mtime
    return 0.0


def _sessions_newest_first() -> list[str]:
    return sorted(list_sessions(), key=_session_saved_at, reverse=True)


def _pkg_owner(serial: str) -> str:
    out = _vu(serial, f"stat -c %u /data/data/{SHOPEE_PKG} 2>/dev/null", timeout=15)
    m = re.search(r"\d+", out or "")
    return m.group(0) if m else ""


def _session_parts_present(serial: str) -> list[str]:
    found: list[str] = []
    for part in SESSION_PARTS:
        out = _vu(serial, f"ls -d /data/data/{SHOPEE_PKG}/{part} 2>/dev/null", timeout=15)
        if part in (out or "").replace("\\", "/"):
            found.append(part)
    return found


def local_ok(uid: str) -> bool:
    path = SESSION_DIR / f"{uid}.tgz"
    return path.is_file() and path.stat().st_size >= 10_000


def snapshot_session(serial: str) -> str:
    """지금 로그인된 앱 세션을 PC에 userid 별로 보관한다. 비밀번호는 저장하지 않는다.

    다른 userid 의 .tgz/.json 은 삭제하지 않는다 (세션 풀 확장·순환용).
    """
    uid = _account_userid_on_device(serial)
    if not uid:
        _log("  세션 보관 생략 — 로그인된 계정 없음")
        return ""
    username = _account_username_on_device(serial)
    parts = _session_parts_present(serial)
    if "shared_prefs" not in parts:
        _log("  세션 보관 실패 — shared_prefs 없음")
        return ""
    _sh(serial, f"am force-stop {SHOPEE_PKG}")
    time.sleep(0.8)
    remote = f"/sdcard/_shopee_session_{uid}.tgz"
    try:
        _vu(
            serial,
            f"rm -f {remote}; tar czf {remote} -C /data/data/{SHOPEE_PKG} " + " ".join(parts),
            timeout=120,
        )
    except Exception as exc:
        _log(f"  세션 보관 실패 — 묶기: {exc}")
        if local_ok(uid):
            _log(f"  세션 보관 userid={uid}  이미 있는 파일 유지")
            return uid
        return ""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    local = SESSION_DIR / f"{uid}.tgz"
    try:
        _adb(serial, "pull", remote, str(local), timeout=180)
    except Exception as exc:
        _log(f"  세션 보관 실패 — 복사: {exc}")
        return ""
    finally:
        try:
            _vu(serial, f"rm -f {remote}", timeout=15)
        except Exception:
            pass
    if not local.is_file() or local.stat().st_size < 10_000:
        _log("  세션 보관 실패 — 파일이 너무 작음")
        return ""
    size = local.stat().st_size
    if not username:
        username = _username_from_session_tgz(uid)
    _write_session_meta(uid, bytes_size=size, username=username)
    if username:
        _log(f"  세션 보관 userid={uid} username={username}  {size} bytes")
    else:
        _log(f"  세션 보관 userid={uid}  {size} bytes")
    _book_created(uid, serial, username)
    return uid


def restore_session(serial: str, userid: str) -> bool:
    """보관한 세션을 앱에 되돌려 그 계정으로 로그인된 상태를 만든다."""
    local = SESSION_DIR / f"{userid}.tgz"
    if not local.is_file():
        return False
    _log(f"  세션 복원 시작 userid={userid}")
    _sh(serial, f"am force-stop {SHOPEE_PKG}")
    time.sleep(0.6)
    owner = _pkg_owner(serial)
    if not owner:
        _sh(serial, f"am start -n {HOME_ACT}")
        time.sleep(3.0)
        _sh(serial, f"am force-stop {SHOPEE_PKG}")
        time.sleep(0.6)
        owner = _pkg_owner(serial)
    if not owner:
        _log("  세션 복원 실패 — 앱 폴더 소유자를 찾지 못함")
        return False
    remote = f"/sdcard/_shopee_session_{userid}.tgz"
    try:
        _adb(serial, "push", str(local), remote, timeout=180)
    except Exception as exc:
        _log(f"  세션 복원 실패 — 전송: {exc}")
        return False
    targets = " ".join(f"/data/data/{SHOPEE_PKG}/{part}" for part in SESSION_PARTS)
    _vu(
        serial,
        f"rm -rf {targets}; "
        f"tar xzf {remote} -C /data/data/{SHOPEE_PKG}; "
        f"chown -R {owner}:{owner} {targets} /data/data/{SHOPEE_PKG}/files; "
        f"restorecon -R /data/data/{SHOPEE_PKG}; "
        f"rm -f {remote}",
        timeout=90,
    )
    launch_shopee(serial)
    time.sleep(3.0)
    got = _account_userid_on_device(serial)
    ok = got == str(userid)
    _log(f"  세션 복원 결과 userid={got or '없음'}")
    return ok


def _restore_verified(serial: str, userid: str) -> bool:
    """파일 복원 + 홈에서 계정 조회 err=0 까지 확인한다."""
    if not restore_session(serial, userid):
        return False
    _consume_traffic_log(serial)
    state = _await_account(serial, timeout=18)
    if state == "in":
        _log("  보관 세션 서버 로그인 확인")
        return True
    if state == "out":
        _log("  보관 세션은 올렸지만 서버가 로그인으로 인정하지 않음")
    else:
        _log("  보관 세션 복원 후 계정 조회 없음")
    return False


ROTATE_EVERY = 0


def _session_book():
    path = str(_save.ROOT / "runtime" / "vmos_cloud")
    if path not in sys.path:
        sys.path.insert(0, path)
    import session_ledger
    return session_ledger


def _book_created(uid: str, serial: str, username: str = "") -> None:
    phone = os.environ.get("SHOPEE_PAD_NAME") or ""
    pad = os.environ.get("VMOS_PAD_CODE") or ""
    if not phone and not pad:
        return
    try:
        _session_book().note_created(uid, phone, serial, pad, username=username)
    except Exception:
        pass


def _book_start(uid: str, reason: str) -> None:
    try:
        _session_book().start_use(uid, reason)
    except Exception:
        pass


def _book_end(uid: str, reason: str) -> None:
    try:
        if uid:
            _session_book().end_use(uid, reason)
    except Exception:
        pass


def _book_saved() -> None:
    try:
        _session_book().add_saved(1)
    except Exception:
        pass


def _drop_from_worklist(shop: int, item: int) -> None:
    runtime = os.environ.get("SHOPEE_RUNTIME") or ""
    if not runtime:
        return
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    try:
        import list_progress

        pair = (int(shop), int(item))
        list_progress.remove_pair_from_queues(pair)
        try:
            list_progress.record_outcome(pair, "saved")
        except Exception:
            pass
    except Exception:
        pass


def _record_outcome_in_worklist(shop: int, item: int, outcome: str) -> None:
    """미저장/없음 결과를 목록 사이클에 반영한다. outcome: missed | gone."""
    runtime = os.environ.get("SHOPEE_RUNTIME") or ""
    if not runtime:
        return
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    try:
        import list_progress

        list_progress.record_outcome((int(shop), int(item)), outcome)
    except Exception:
        pass


def _mark_missed_in_worklist(shop: int, item: int) -> None:
    # 418·캡차·예외 스킵은 네트워크/차단성 미저장으로 본다.
    _record_outcome_in_worklist(shop, item, "missed")


def switch_saved_session(serial: str, reason: str) -> str:
    """담당 보관 세션을 저장 시각 오래된 순으로 바꾼다. 폰·하루당 userid 1회.

    오늘 쓸 세션이 없으면 빈 문자열(호출 측에서 새 로그인 대기).
    """
    global _PDP_LOG_OFFSET
    pool = list_sessions()
    if len(pool) < session_rotate_min():
        _log(f"  계정 전환 생략 ({reason}) — 보관 세션 없음")
        return ""
    current = _account_userid_on_device(serial) or ""
    ordered = _sessions_oldest_first(pool)
    candidates = [uid for uid in ordered if not _session_used_today(uid)]
    if current and current in candidates and len(candidates) > 1:
        candidates = [uid for uid in candidates if uid != current]
    if not candidates:
        _log(
            f"  계정 전환 생략 ({reason}) — 오늘 보관 {len(pool)}개 모두 이용 "
            "(새 로그인 필요)"
        )
        return ""
    for uid in candidates:
        if _restore_verified(serial, uid):
            _PDP_LOG_OFFSET = _mitm_size()
            _log(f"  계정 전환 ({reason}) userid={uid} — 오래된 순·하루 1회·IP 유지")
            _book_end(current, reason)
            _book_start(uid, reason)
            if snapshot_session(serial):
                _log("  복원 계정 세션 PC에 보관 — 수집 진행")
            else:
                _log("  복원 계정 세션 보관 실패 — 기기 상태 유지하고 수집 진행")
            return uid
        _log(f"  복원·서버 확인 실패 userid={uid} — 다음 보관 세션")
    _log(f"  계정 전환 실패 ({reason}) — 복원 가능한 세션이 없음")
    return ""


def _ensure_logged_in_session(serial: str) -> bool:
    """앱에 있는 계정을 먼저 본다.

    계정 조회가 로그인 성공이 아니면 상품을 열기 전에 다음 보관 세션으로 바꾸고,
    그 세션이 로그인되어 있으면 수집을 계속한다.
    """
    current_uid = _account_userid_on_device(serial)
    if current_uid:
        _book_start(current_uid, "수집 시작")
    state = _await_account(serial, timeout=45)
    if state == "":
        _log("  계정 조회가 아직 없음 — 홈에서 한 번 더 확인")
        state = _await_account(serial, timeout=25)
    if state == "in":
        return True
    if state != "out":
        _log("  계정 조회가 오지 않음 — 상품을 열지 않고 수집 중지")
        return False
    _log("  계정 조회가 로그인 성공이 아님 — 상품을 열기 전에 다음 보관 세션으로 변경")
    seen: set[str] = set()
    if current_uid:
        seen.add(current_uid)
    while True:
        mark = _mitm_size()
        restored = switch_saved_session(serial, "시작 로그아웃")
        if not restored or restored in seen:
            break
        seen.add(restored)
        state = _account_state(mark)
        if state == "":
            state = _await_account(serial, timeout=12)
        if state == "in":
            return True
        if state == "":
            _log("  올린 세션의 계정 조회가 없음 — 다음 보관 세션")
        else:
            _log("  올린 세션도 로그인 아님 — 다음 보관 세션")
    _log("  로그인된 보관 세션 없음 — 상품을 열지 않고 수집 중지")
    return False


def _prime_collection_login(serial: str) -> bool:
    """수집 시작. 서버가 인정하는 보관 세션을 올리거나, 이미 그 상태면 갱신만 한다."""
    pool = list_sessions()
    if not pool:
        _log("  배당 세션 없음 — 상품을 열지 않음")
        return False
    sessions = _sessions_oldest_first(pool)
    uid = ""
    current = _account_userid_on_device(serial)
    if current:
        state = _await_account(serial, timeout=12)
        if state == "in":
            _log("  앱·서버 로그인 확인 — 현재 계정 유지 후 PC에 갱신")
            uid = current
        elif state == "out":
            _log("  기기 계정 번호만 있고 서버 로그인 아님 — 보관 세션에서 복원")
            if local_ok(current) and _restore_verified(serial, current):
                uid = current
                _log("  기기에 있는 계정의 PC 보관본으로 서버 로그인 확인")
        else:
            _log("  계정 조회 없음 — 보관 세션에서 복원")
    else:
        _log("  앱에 로그인 없음 — 보관 세션에서 복원 (오래된 순·오늘 미사용)")
    if not uid:
        restored = ""
        today_unused = [u for u in sessions if not _session_used_today(u)]
        candidates = today_unused or sessions
        if current and local_ok(current) and not _session_used_today(current):
            candidates = [current] + [c for c in candidates if c != current]
        elif current:
            candidates = [c for c in candidates if c != current]
        for candidate in candidates:
            if _restore_verified(serial, candidate):
                restored = candidate
                break
            _log("  복원·서버 확인 실패 — 다음 보관 세션")
        if not restored:
            _log("  서버가 인정하는 보관 세션이 없음 — 상품을 열지 않음")
            return False
        uid = restored
    if snapshot_session(serial):
        _log(f"  수집 시작 전 세션 PC 보관 userid={uid}")
    else:
        _log("  수집 시작 전 세션 보관 실패 — 기기 계정 유지하고 수집 계속")
    launch_shopee(serial)
    _book_start(uid, "수집 시작")
    return True


def restore_next_session(serial: str, reject: set[str] | frozenset[str]) -> str:
    for uid in list_sessions():
        if uid in reject:
            continue
        if _restore_verified(serial, uid):
            return uid
    return ""


def dismiss_product_error(serial: str) -> None:
    for _ in range(3):
        _sh(serial, "input keyevent KEYCODE_BACK")
        time.sleep(0.35)
    _sh(serial, f"am start -n {HOME_ACT}")
    time.sleep(1.0)


def wait_for_login(
    serial: str, timeout: float = 1800.0, *,
    device_uid_only: bool = False,
    reject_uids: frozenset[str] | set[str] | None = None,
) -> bool:
    _log("수동 로그인 대기 — 홈만 로그인 (상품 화면 금지)")
    reject = set(reject_uids or BLOCKED_USERIDS)
    dismiss_product_error(serial)
    deadline = time.time() + timeout
    stable = 0
    last_note = 0.0
    while time.time() < deadline:
        uid = _account_userid_on_device(serial)
        if uid and uid in reject:
            now = time.time()
            if now - last_note >= 20:
                _log(f"  막힌 계정 userid={uid} — 로그아웃하고 다른 계정으로 로그인하세요")
                last_note = now
            stable = 0
            time.sleep(3.0)
            continue
        mitm_ok = False if device_uid_only else _login_seen_in_mitm()
        if uid or mitm_ok:
            stable += 1
            if stable >= 2 and uid:
                _log(f"  로그인 세션 확인 userid={uid} — 수집 재개")
                return True
            if stable >= 2 and not uid:
                stable = 0
        else:
            stable = 0
        now = time.time()
        if now - last_note >= 30:
            left = int(deadline - now)
            _log(f"  로그인 대기 중… 남은 {left}s  (상품 화면을 열지 마세요)")
            last_note = now
        time.sleep(3.0)
    return False


def launch_shopee(serial: str) -> None:
    _sh(serial, f"am force-stop {SHOPEE_PKG}")
    time.sleep(0.8)
    _drop_stuck_zygote(serial)
    _sh(serial, f"am start -n {HOME_ACT}")
    _log("  Shopee 앱 기동 (홈)")


def reset_shopee_login(serial: str) -> None:
    _log("  Shopee 앱 데이터 삭제 — 이전 로그인 제거")
    _sh(serial, f"pm clear {SHOPEE_PKG}")
    time.sleep(1.2)
    launch_shopee(serial)


def prepare_fresh_login(serial: str) -> None:
    """앱 데이터를 지우고 첫 화면 안내를 넘긴 뒤 로그인 화면으로 둔다."""
    reset_shopee_login(serial)
    time.sleep(4.0)
    try:
        _sh(serial, "input tap 648 1584")
        time.sleep(2.5)
        _sh(serial, "input tap 458 182")
    except Exception:
        pass


def rotate_after_418(serial: str, port: int, mitm):
    """418이면 새 브라질 sticky 를 붙인 뒤, 그 IP 위에서 로그인하고 수집한다."""
    _log("  PDP 418/90309999 — 브라질 프록시와 로그인 세션을 즉시 교체")
    stop_mitm(mitm)
    try:
        MITM_LOG.write_text("", encoding="utf-8")
    except Exception:
        pass
    try:
        upstream = pick_br_upstream()
    except Exception as exc:
        _log(f"  새 프록시 실패: {exc}")
        return None, None
    new_mitm = start_mitm(port, upstream=upstream)
    setup_reverse_proxy(serial, port)
    reset_shopee_login(serial)
    _log("  로그인 구간 — 출구는 방금 붙인 브라질 IP, 상품 딥링크 없음")
    if not wait_for_login(serial, device_uid_only=True):
        _log("  로그인 대기 시간 초과 — 수집 중지")
        return None, None
    _log("  같은 브라질 IP로 수집 재개")
    return new_mitm, upstream


def _pull_enabled() -> bool:
    return str(os.environ.get("SHOPEE_PULL_SHARD") or "").isdigit()


def _claim_one(pending: list[tuple[int, int, str]]) -> bool:
    """Take the next free server-list item for this phone."""
    if not _pull_enabled():
        return False
    runtime = os.environ.get("SHOPEE_RUNTIME") or ""
    if runtime and runtime not in sys.path:
        sys.path.insert(0, runtime)
    try:
        import list_progress
        held = {(shop, item) for shop, item, _url in pending}
        url = list_progress.claim_next(int(os.environ["SHOPEE_PULL_SHARD"]), held)
    except Exception as exc:
        _log(f"  목록에서 가져오기 실패: {exc}")
        return False
    if not url:
        return False
    ids = _save.parse_ids(url)
    if not ids or ids in held or _save.already_saved(*ids):
        return False
    pending.append((ids[0], ids[1], url))
    _log(f"  목록에서 가져옴 i.{ids[0]}.{ids[1]}")
    return True


def capture_many(
    targets: list[tuple[int, int, str]],
    *,
    port: int = DEFAULT_PORT,
    keep_proxy: bool = False,
    wait_s: float = WAIT_S,
    copy_runtime_each: bool = False,
    upstream: str | None = None,
    skip_first_recover: bool = False,
    wait_login: bool = False,
    force_relogin: bool = False,
    rotate_region: str | None = None,
) -> dict:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    rec = ensure_device()
    serial = rec["serial"]
    pending = [(s, i, u) for s, i, u in targets if not _save.already_saved(s, i)]
    pulling = _pull_enabled()
    _log("=" * 68)
    _log("【VMOS 앱 전용 캡처】 웹 없음 · 세션 유지")
    _log(f"  기기 {serial}  Android {rec.get('androidVersion')}  {rec.get('padName')}")
    if pulling:
        _log("  대상 서버 목록에서 직접 가져옴")
    else:
        _log(f"  대상 {len(targets)}  미저장 {len(pending)}")
    _log("=" * 68)
    if not pending and not pulling:
        return {"ok": True, "saved": 0, "miss": 0, "serial": serial, "note": "이미 전부 저장"}

    try:
        probe = vmos_cloud.device_probe(serial)
        if SHOPEE_PKG not in (probe.get("shopee") or []):
            raise RuntimeError(f"Shopee 앱 없음: {probe}")
    except Exception as exc:
        _log(f"  패키지 확인 생략: {exc}")

    mitm = None
    saved = 0
    miss = 0
    consec = 0
    solved_opens = 0
    login_wall_hits = 0
    items: list[dict] = []
    t0 = time.time()
    last_one: dict = {}

    _uid_cache = {"val": "", "at": 0.0}

    def _publish(note: str) -> None:
        elapsed = max(0.01, time.time() - t0)
        # userid 조회는 vu+grep 로 느리다. 계정 전환 때만 바뀌므로 30s 캐시로 건당 adb 왕복을 줄인다.
        now = time.time()
        if not _uid_cache["val"] or now - float(_uid_cache["at"]) >= 30.0:
            _uid_cache["val"] = _account_userid_on_device(serial)
            _uid_cache["at"] = now
        uid = _uid_cache["val"]
        write_harvest_status({
            "running": True,
            "method": METHOD_LABEL,
            "note": note,
            "started_at": t0,
            "elapsed_s": round(elapsed, 1),
            "saved": saved,
            "miss": miss,
            "done": saved + miss,
            "pending_total": len(pending),
            "pending_left": max(0, len(pending) - (saved + miss)),
            "per_min": round(saved / (elapsed / 60.0), 2),
            "per_hour": round(saved / (elapsed / 3600.0), 1),
            "target_per_hour": TARGET_PER_HOUR,
            "last": last_one,
            "serial": serial,
            "userid": uid,
            "upstream": (upstream or "")[-120:],
            "egress": "폰 회선 직접" if not upstream else "상류 프록시",
            "log_file": os.environ.get("HARVEST_LOG") or "",
            "updated": time.time(),
        })

    try:
        install_system_ca(serial)
        mitm = start_mitm(port, upstream=upstream, reuse=skip_first_recover)
        setup_reverse_proxy(serial, port)
        global _PDP_LOG_OFFSET, _HOLD_FOR_CAPTCHA
        _PDP_LOG_OFFSET = _mitm_size()
        if wait_login or force_relogin:
            if force_relogin:
                prepare_fresh_login(serial)
            else:
                dismiss_product_error(serial)
                if not _sh(serial, f"pidof {SHOPEE_PKG}").strip():
                    launch_shopee(serial)
            _log("  로그인 구간 — 다른 계정으로 홈만 로그인")
            if not wait_for_login(serial, device_uid_only=True, reject_uids=BLOCKED_USERIDS):
                _log("  로그인 대기 시간 초과 — 수집 중지")
                return {"ok": False, "saved": 0, "miss": 0, "serial": serial, "note": "로그인 대기 시간 초과"}
        if not _prime_collection_login(serial):
            return {"ok": False, "saved": 0, "miss": 0, "serial": serial, "note": "로그인된 세션 없음"}
        _log("  현재 로그인을 유지한 채 상품 수집 시작")
        _publish("수집 진행 중")
        item_wait = wait_s
        bounce = False
        streak = 0
        miss_streak = 0     # 적응형 재기동용 연속 미저장 수
        n = 0
        idle_note = 0.0
        retried: set[tuple[int, int]] = set()
        rot_item = 0        # 현재 상품에서 시도한 IP 교체 횟수
        blocked_items = 0   # 여러 IP 에서도 418 로 스킵된 연속 상품 수
        browse_in = (
            random.randint(BROWSE_EVERY_MIN, max(BROWSE_EVERY_MIN, BROWSE_EVERY_MAX))
            if BROWSE_EVERY_MAX > 0 else 0
        )

        def _rotate_proxy() -> bool:
            """418 시 상류 프록시를 새 출구 IP 로 교체하고 mitm 을 재기동한다."""
            nonlocal mitm, upstream
            if not rotate_region:
                return False
            pp = _proxy_pool()
            try:
                if upstream:
                    pp.record(upstream, "blocked")
            except Exception:
                pass
            try:
                new_up = pick_rotating_upstream(rotate_region)
            except Exception as exc:
                _log(f"  프록시 회전 실패: {exc}")
                return False
            stop_mitm(mitm)
            try:
                MITM_LOG.write_text("", encoding="utf-8")
            except Exception:
                pass
            try:
                mitm = start_mitm(port, upstream=new_up)
                setup_reverse_proxy(serial, port)
            except Exception as exc:
                _log(f"  mitm 재기동 실패: {exc}")
                return False
            upstream = new_up
            global _PDP_LOG_OFFSET
            _PDP_LOG_OFFSET = _mitm_size()
            _log("  새 대만 IP 로 수집 재개")
            _publish("418 → 프록시 IP 교체 후 재개")
            return True

        account_saves = 0

        def _relogin(reason: str = "로그인 필요") -> bool:
            """다음 배당 계정으로 바꾼다. IP는 유지하고, 앱 안의 로그인만 바꾼다."""
            nonlocal account_saves
            global _PDP_LOG_OFFSET, _SOLVED_CAPTCHA, _HOLD_FOR_CAPTCHA
            _SOLVED_CAPTCHA = False
            prev = _account_userid_on_device(serial)
            reject = set(BLOCKED_USERIDS)
            if prev:
                reject.add(prev)
                _log(f"  계정 전환 ({reason}) — 다음 배당 세션. IP는 유지")
            else:
                _log(f"  계정 전환 ({reason}) — 로그인된 계정 없음")
            restored = switch_saved_session(serial, reason)
            if restored:
                account_saves = 0
                _HOLD_FOR_CAPTCHA = True
                _consume_traffic_log(serial)
                _PDP_LOG_OFFSET = _mitm_size()
                _log(f"  보관 세션으로 전환 userid={restored} — 캡차 창이 뜰 때까지 화면을 건드리지 않음")
                _publish("세션 전환 — 캡차 화면 유지")
                return True
            pool = list_sessions()
            left = [u for u in _sessions_oldest_first(pool) if not _session_used_today(u)]
            if left:
                _log("  전환 실패 — 오늘 남은 보관 세션이 있어 잠시 뒤 재시도")
                time.sleep(12)
                return True
            _log("  오늘 보관 세션 순환 완료 — 새 계정 로그인 대기")
            _publish("다음 세션 없음 — 새 계정 로그인 대기")
            prepare_fresh_login(serial)
            _consume_traffic_log(serial)
            _PDP_LOG_OFFSET = _mitm_size()
            if not wait_for_login(serial, device_uid_only=True, reject_uids=reject):
                _log("  새 로그인 대기 시간 초과 — 수집 중지")
                return False
            if snapshot_session(serial):
                _log("  새 로그인 세션 PC에 보관 — 수집 재개")
            else:
                _log("  새 로그인 세션 보관 실패 — 수집 재개")
            launch_shopee(serial)
            _consume_traffic_log(serial)
            _PDP_LOG_OFFSET = _mitm_size()
            _publish("새 로그인 확인 — 수집 재개")
            return True

        def _wait_418() -> str:
            """ok=이어서 수집, relogin=새 로그인 완료, skip=이 상품만 넘김, stop=수집 중지."""
            global _PDP_LOG_OFFSET, _HOLD_FOR_CAPTCHA
            if _rotate_proxy():
                return "ok"
            if silent_block() or not popup_captcha_expected():
                if not (serial and _captcha_session_visible(serial)):
                    _log("  418 이지만 팝업 캡차 없음 — 이 상품은 넘기고 같은 계정으로 계속")
                    _PDP_LOG_OFFSET = _mitm_size()
                    _publish("수집 진행 중")
                    return "skip"
            # 418 이면 앱을 재기동하지 않고, 넘길 팝업 캡차만 그대로 둔 채 기다린다.
            def _captcha_tick() -> None:
                if serial and _home_foreground(serial) and not _captcha_session_visible(serial):
                    if not popup_captcha_expected():
                        _publish("수집 진행 중")
                        return
                _publish("418 캡차 대기 — 화면에서 수동으로 넘기면 재개")

            while True:
                _publish("418 캡차 대기 — 화면에서 수동으로 넘기면 재개")
                result = wait_manual_captcha(on_tick=_captcha_tick, serial=serial)
                if result == "passed":
                    return "ok"
                if result == "login":
                    return "relogin" if _relogin("로그인 필요") else "stop"
                if result == "skip":
                    return "skip"
                if result == "relogin":
                    return "relogin" if _relogin("페이지 사용 불가") else "stop"
                if result in {"nocaptcha", "timeout"}:
                    if serial and (
                        _captcha_session_visible(serial) or _captcha_attempted(_PDP_LOG_OFFSET)
                    ):
                        _log("  캡차 미통과 — 창 유지, 통과할 때까지 계속 대기")
                        _HOLD_FOR_CAPTCHA = True
                        continue
                    if result == "nocaptcha":
                        return "relogin" if _relogin("화면에 캡차 없음") else "stop"
                    _log("  캡차 대기 구간 종료 — 같은 화면에서 다시 대기")
                    continue
                _log("  캡차 대기 종료 — 같은 계정으로 계속")
                return "ok"

        def _count_save(*, switch: bool, shop: int | None = None, item: int | None = None) -> None:
            nonlocal account_saves, bounce
            account_saves += 1
            _book_saved()
            if shop is not None and item is not None:
                _drop_from_worklist(shop, item)
            if not switch or ROTATE_EVERY <= 0 or account_saves < ROTATE_EVERY:
                return
            _log(f"  이 계정 저장 {account_saves}건 — 다음 세션으로 전환")
            if switch_saved_session(serial, f"{ROTATE_EVERY}건"):
                account_saves = 0
                global _HOLD_FOR_CAPTCHA
                _HOLD_FOR_CAPTCHA = True
                bounce = True
            else:
                account_saves = 0

        def _maybe_rest() -> None:
            done = saved + miss
            if REST_EVERY <= 0 or REST_S <= 0:
                return
            if done <= 0 or done % REST_EVERY:
                return
            if n >= len(pending):
                return
            _log(f"  {REST_EVERY}건 휴식 {REST_S:.0f}s")
            _publish(f"{REST_EVERY}건마다 {REST_S:.0f}s 휴식")
            launch_shopee(serial)
            time.sleep(REST_S)

        if _error_page_foreground(serial) and (
            popup_captcha_expected() or _captcha_session_visible(serial)
        ):
            _log("  세션 복원 후 캡차 — 앱을 재기동하지 않고 대기")
            while True:
                held = wait_manual_captcha(serial=serial)
                if held == "passed":
                    break
                if held == "login" and not _relogin("로그인 필요"):
                    return {"ok": False, "saved": 0, "miss": 0, "serial": serial, "note": "로그인 대기 시간 초과"}
                if held == "relogin" and not _relogin("페이지 사용 불가"):
                    return {"ok": False, "saved": 0, "miss": 0, "serial": serial, "note": "로그인 대기 시간 초과"}
                if held == "skip":
                    break
                if held in {"nocaptcha", "timeout"} and (
                    _captcha_session_visible(serial) or _captcha_attempted(_PDP_LOG_OFFSET)
                ):
                    _log("  캡차 미통과 — 창 유지, 통과할 때까지 계속 대기")
                    continue
                if held == "nocaptcha" and not _relogin("화면에 캡차 없음"):
                    return {"ok": False, "saved": 0, "miss": 0, "serial": serial, "note": "로그인 대기 시간 초과"}
                _log("  캡차 대기 종료 — 같은 계정으로 계속")
                break
        elif _screen_traffic_error(serial):
            _log("  시작 시점 페이지 사용 불가 — 다음 계정으로 전환")
            if not _relogin("페이지 사용 불가"):
                return {"ok": False, "saved": 0, "miss": 0, "serial": serial, "note": "로그인 대기 시간 초과"}

        while True:
            if n >= len(pending):
                if _claim_one(pending):
                    idle_note = 0.0
                    continue
                if not pulling:
                    break
                again = [
                    (shop, item, url)
                    for shop, item, url in pending
                    if (shop, item) not in retried and not _save.already_saved(shop, item)
                ]
                if again:
                    _log(f"  미저장 {len(again)}건 한 번 더")
                    for shop, item, url in again:
                        retried.add((shop, item))
                        pending.append((shop, item, url))
                    continue
                now = time.time()
                if now - idle_note >= 15:
                    _log("  목록에 가져올 항목 없음 — 갱신 대기")
                    _publish("목록 갱신 대기")
                    idle_note = now
                time.sleep(4)
                continue
            sid, iid, _u = pending[n]
            if _logged_out_since(_PDP_LOG_OFFSET) and not _HOLD_FOR_CAPTCHA and not _error_page_foreground(serial):
                if not _relogin("로그인 필요"):
                    break
                bounce = True
                streak = 0
                continue
            if _traffic_error_since(_PDP_LOG_OFFSET) and not _error_page_foreground(serial):
                _log("  페이지 사용 불가 화면 — 다음 계정으로 전환")
                if not _relogin("페이지 사용 불가"):
                    break
                bounce = True
                streak = 0
                continue
            if pdp_blocked():
                action = _wait_418()
                if action == "stop":
                    break
                if action == "relogin":
                    bounce = True
                    streak = 0
                    rot_item = 0
                    continue
                if action == "skip":
                    _log(f"  418 상품 스킵 i.{sid}.{iid}")
                    miss += 1
                    _mark_missed_in_worklist(sid, iid)
                    blocked_items += 1
                    n += 1
                    streak = 0
                    rot_item = 0
                    bounce = False
                    if blocked_items >= ACCOUNT_FLAG_BLOCKS:
                        _log(f"  연속 {blocked_items}개 상품 418 — 수집 중지")
                        _publish("상품 API 418 — 수집 중지")
                        break
                    continue
                # 캡차를 넘긴 뒤에는 앱을 재기동하지 않는다.
                bounce = False
                streak = 0
                continue
            try:
                if bounce and HUMAN_MODE:
                    how = "인앱리셋"
                    _log("  인앱 리셋 — 홈 훑고 웜 진입 (콜드 재기동 없음)")
                    _human_browse_reset(serial, port)
                    one = capture_one(
                        serial, sid, iid,
                        restart=False, wait_s=item_wait,
                        copy=False, reset_task=False,
                    )
                    bounce = False
                    streak = 0
                elif bounce:
                    how = "재기동"
                    _log(f"  {BOUNCE_EVERY}건 연속 저장 후 재기동")
                    one = capture_one(
                        serial, sid, iid,
                        restart=True, wait_s=item_wait,
                        copy=False, reset_task=False,
                    )
                    bounce = False
                    streak = 0
                else:
                    how = "VIEW"
                    if HUMAN_MODE:
                        # 이전 상품에서 사람처럼 빠져나온 뒤 다음 상품으로
                        _back_from_product(serial)
                    one = capture_one(
                        serial, sid, iid,
                        restart=False, wait_s=item_wait,
                        copy=copy_runtime_each and n == 0,
                        reset_task=False,
                    )
            except Exception as exc:
                _log(f"  캡처 예외 i.{sid}.{iid}: {exc}")
                try:
                    dismiss_product_error(serial)
                except Exception:
                    pass
                n += 1
                miss += 1
                _mark_missed_in_worklist(sid, iid)
                consec = 0
                bounce = False
                streak = 0
                _maybe_rest()
                continue
            one["how"] = how
            items.append(one)
            last_one = one
            if not one.get("ok") and not pdp_blocked():
                time.sleep(1.0)
            captcha_now = pdp_blocked() and (
                popup_captcha_expected() or _captcha_session_visible(serial)
            )
            # 느린 uiautomator 덤프는 치울 캡차 대기가 실제로 걸려 있을 때만 한다.
            # 평상시(_HOLD_FOR_CAPTCHA=False)에는 화면을 조회하지 않아 건당 adb 왕복을 줄인다.
            if not captcha_now and _HOLD_FOR_CAPTCHA and not _captcha_session_visible(serial):
                _HOLD_FOR_CAPTCHA = False
            if not one.get("ok") and one.get("logged_out") and not captcha_now:
                if not _relogin("로그인 필요"):
                    break
                bounce = True
                streak = 0
                _publish("세션 전환 — 캡차 화면 유지")
                continue
            if one.get("traffic_error") and not captcha_now:
                _log(f"  페이지 사용 불가 — i.{sid}.{iid} 는 새 로그인 후 다시 연다")
                if one.get("ok"):
                    saved += 1
                    _count_save(switch=False, shop=sid, item=iid)
                    consec = 0
                    n += 1
                    rot_item = 0
                    blocked_items = 0
                if not _relogin("페이지 사용 불가"):
                    break
                bounce = True
                streak = 0
                rot_item = 0
                _publish("세션 전환 — 캡차 화면 유지")
                continue
            if pdp_blocked():
                # 저장은 됐는데 뒤따르는 요청만 418 → 저장 인정하고 IP 교체 후 진행
                if one.get("ok") or _save.already_saved(sid, iid):
                    if not one.get("ok"):
                        one["ok"] = True
                        one.pop("error", None)
                        _log(f"  418 직전 저장 확인 i.{sid}.{iid}")
                    saved += 1
                    _count_save(switch=False, shop=sid, item=iid)
                    consec = 0
                    n += 1
                    rot_item = 0
                    blocked_items = 0
                    action = _wait_418()
                    if action == "stop":
                        break
                    bounce = False
                    streak = 0
                    _maybe_rest()
                    _publish("수집 진행 중")
                    continue
                # 저장 안 된 418 — 프록시가 있으면 새 IP 로 재시도. 캡차 대기는 앱을 재기동하지 않는다.
                action = _wait_418()
                if action == "stop":
                    break
                if action == "relogin":
                    bounce = True
                    streak = 0
                    rot_item = 0
                    continue
                if action == "skip":
                    _log(f"  화면에 캡차 없는 418 — 상품 스킵 i.{sid}.{iid}")
                    miss += 1
                    _mark_missed_in_worklist(sid, iid)
                    n += 1
                    rot_item = 0
                    bounce = False
                    streak = 0
                    _publish("수집 진행 중")
                    continue
                if action == "ok" and rotate_region and rot_item < MAX_ROTATE_PER_ITEM:
                    rot_item += 1
                    bounce = True
                    streak = 0
                    continue  # 같은 상품 재시도(n 유지)
                if action == "ok":
                    global _SOLVED_CAPTCHA
                    bounce = False
                    streak = 0
                    solved_opens += 1
                    if solved_opens >= 3 and pdp_blocked():
                        _log("  캡차를 넘긴 뒤 같은 상품이 계속 막힘 — 이 상품은 넘김")
                        solved_opens = 0
                        _SOLVED_CAPTCHA = False
                        n += 1
                        miss += 1
                        _mark_missed_in_worklist(sid, iid)
                        bounce = False
                        continue
                    continue  # 같은 상품을 재기동 없이 다시 연다
                # IP 를 여러 번 갈아도 같은 상품 418 → 스킵
                _log(f"  {MAX_ROTATE_PER_ITEM}회 IP 교체에도 418 — 상품 스킵 i.{sid}.{iid}")
                rot_item = 0
                blocked_items += 1
                miss += 1
                _mark_missed_in_worklist(sid, iid)
                consec = 0
                bounce = False
                streak = 0
                n += 1
                if blocked_items >= ACCOUNT_FLAG_BLOCKS:
                    _log(f"  연속 {blocked_items}개 상품이 모든 IP 에서 418 — 계정 차단 판정, 수집 중지")
                    _publish("계정 차단 판정 — 수집 중지")
                    break
                _maybe_rest()
                _publish("수집 진행 중")
                continue
            n += 1
            if one.get("ok"):
                saved += 1
                _count_save(switch=True, shop=sid, item=iid)
                consec = 0
                solved_opens = 0
                login_wall_hits = 0
                streak += 1
                miss_streak = 0
                rot_item = 0
                blocked_items = 0
                if ADAPTIVE_BOUNCE and not HUMAN_MODE:
                    # 건강한 연속 저장에서는 재기동하지 않는다. 신선도 유지용으로만 주기 재기동.
                    if FORCE_BOUNCE_EVERY > 0 and streak >= FORCE_BOUNCE_EVERY:
                        bounce = True
                        streak = 0
                        _log(f"  {FORCE_BOUNCE_EVERY}건 연속 저장 — 세션 신선도 유지 재기동")
                else:
                    _bounce_at = HUMAN_BOUNCE_EVERY if HUMAN_MODE else BOUNCE_EVERY
                    if streak >= _bounce_at:
                        bounce = True
                        streak = 0
                        if HUMAN_MODE:
                            _log(f"  {_bounce_at}건 연속 저장 — 다음은 인앱 리셋")
                        else:
                            _log(f"  {_bounce_at}건 연속 저장 — 다음 상품은 재기동")
                if browse_in:
                    browse_in -= 1
                    if browse_in <= 0:
                        _log("  리듬 — 홈을 잠시 연다")
                        try:
                            _sh(serial, f"am start -n {HOME_ACT}")
                            time.sleep(random.uniform(1.0, 2.2))
                        except Exception as exc:
                            _log(f"  홈 섞기 생략: {exc}")
                        bounce = True
                        lo = max(1, BROWSE_EVERY_MIN)
                        hi = max(lo, BROWSE_EVERY_MAX)
                        browse_in = random.randint(lo, hi)
                        _log(f"  리듬 — 다음은 재기동, 다음 홈은 {browse_in}건 후")
            else:
                miss += 1
                outcome = one.get("outcome") or "missed"
                _record_outcome_in_worklist(sid, iid, outcome)
                consec = 0
                streak = 0
                rot_item = 0
                if outcome == "gone":
                    # 상품 없음은 네트워크 미저장이 아니므로 복구 재기동 대상이 아니다.
                    miss_streak = 0
                    bounce = False
                    _log("  상품없음 — 임시 없음 목록에 보관하고 계속")
                else:
                    miss_streak += 1
                    if (
                        ADAPTIVE_BOUNCE and not HUMAN_MODE
                        and MISS_BOUNCE_STREAK > 0 and miss_streak >= MISS_BOUNCE_STREAK
                    ):
                        bounce = True
                        miss_streak = 0
                        _log(f"  연속 미저장 {MISS_BOUNCE_STREAK}건 — 복구 재기동")
                    else:
                        bounce = False
                        _log("  미저장 — 다음 상품 계속 (재기동 없음)")
            _maybe_rest()
            if n < len(pending):
                if HUMAN_MODE:
                    gap = random.uniform(HUMAN_GAP_MIN, HUMAN_GAP_MAX)
                    _log(f"  사람 간격 {gap:.1f}s 대기")
                    time.sleep(gap)
                else:
                    lo, hi = min(GAP_MIN, GAP_MAX), max(GAP_MIN, GAP_MAX)
                    time.sleep(random.uniform(lo, hi) if hi > lo else GAP_S)
            _publish("수집 진행 중")
        elapsed = max(0.01, time.time() - t0)
        _log(f"완료 저장 {saved}  미저장 {miss}  {elapsed:.1f}s  {saved / (elapsed / 60):.1f}건/분")
        _publish("수집 종료")
    except Exception as exc:
        _log(f"  수집 중단: {type(exc).__name__}: {exc}")
        raise
    finally:
        try:
            st_path = _status_path()
            st = json.loads(st_path.read_text(encoding="utf-8")) if st_path.is_file() else {}
            st["running"] = False
            st["serial"] = st.get("serial") or serial
            st["note"] = st.get("note") or "수집 종료"
            st["updated"] = time.time()
            write_harvest_status(st)
        except Exception:
            pass
        stop_mitm(mitm)
        if not keep_proxy:
            clear_proxy(serial, port)
            _log("  프록시/reverse 해제")
    out = {
        "ok": saved > 0,
        "saved": saved,
        "miss": miss,
        "serial": serial,
        "elapsed_s": round(time.time() - t0, 1),
        "items": items[-8:],
    }
    if items and len(items) == 1:
        out.update(items[0])
    return out


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="VMOS 앱 전용 get_pc 캡처(웹 없음)")
    ap.add_argument("--shop", type=int, default=DEFAULT_SHOP)
    ap.add_argument("--item", type=int, default=DEFAULT_ITEM)
    ap.add_argument("--url", default="", help="https://shopee.com.br/-i.SHOP.ITEM 또는 /product/SHOP/ITEM")
    ap.add_argument("--urls", default="", help="상품 URL txt/csv (세션 유지 연속 캡처)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--keep-proxy", action="store_true")
    ap.add_argument("--safe", action="store_true", help="대기 25초(느림)")
    ap.add_argument("--fresh-proxy", action="store_true", help="1024proxy 새 세션을 mitm 상류로")
    ap.add_argument("--no-proxy", action="store_true", help="1024proxy 상류 없이 직접 수집")
    ap.add_argument("--rotate-region", default="", help="주거용 회전 프록시 지역(예: TW). 시작부터 회전 IP 로 나가고 418 시 IP 교체")
    ap.add_argument("--wait-login", action="store_true", help="같은 브라질 프록시 위에서 수동 로그인 후 수집")
    ap.add_argument("--force-relogin", action="store_true", help="앱 데이터를 지워 새 로그인을 강제")
    ap.add_argument("--go", action="store_true", help="로그인 대기 없이 현재 세션으로 즉시 수집")
    ap.add_argument(
        "--method",
        default=os.environ.get("HARVEST_METHOD") or "기본방식",
        help="수집 방식 이름. 기본방식 은 4건 재기동·상류 프록시 없음·418 캡차 대기·페이지 사용 불가 화면이면 즉시 새 로그인",
    )
    args = ap.parse_args()
    method = apply_named_method(args.method)
    spec = NAMED_METHODS[method]
    wait_s = WAIT_S_SAFE if args.safe else float(spec["wait_s"])
    upstream = None
    skip_recover = bool(args.go)
    rotate_region = (args.rotate_region or "").strip().upper() or None
    saved_up = RUNTIME / "upstream.txt"
    if spec.get("no_upstream"):
        if rotate_region or args.fresh_proxy or not args.no_proxy:
            _log("  기본방식 — 상류 프록시·IP 회전 없이 mitm 만 사용")
        upstream = None
        rotate_region = None
        _log("  1024proxy 없이 직접 수집")
    elif rotate_region:
        upstream = pick_rotating_upstream(rotate_region)
        _log(f"  {rotate_region} 회전 주거용 프록시로 수집 — 418 시 IP 자동 교체")
    elif args.no_proxy:
        upstream = None
        _log("  1024proxy 없이 직접 수집")
    elif args.fresh_proxy or not saved_up.is_file():
        upstream = pick_br_upstream()
    else:
        upstream = saved_up.read_text(encoding="utf-8").strip()
        _log("  저장해 둔 브라질 프록시 세션 재사용")
    if args.urls:
        path = Path(args.urls)
        if not path.is_file():
            _log(f"목록 없음: {path}")
            return 2
        targets = load_targets(path)
        if args.limit:
            targets = targets[: args.limit]
        rec = capture_many(
            targets, port=args.port, keep_proxy=args.keep_proxy, wait_s=wait_s,
            upstream=upstream, skip_first_recover=skip_recover,
            wait_login=args.wait_login, force_relogin=args.force_relogin,
            rotate_region=rotate_region,
        )
    else:
        shop, item = args.shop, args.item
        if args.url:
            ids = _save.parse_ids(args.url)
            if not ids:
                _log(f"URL 에서 shop/item 을 못 읽음: {args.url}")
                return 2
            shop, item = ids
        rec = capture(shop, item, port=args.port, keep_proxy=args.keep_proxy, wait_s=wait_s)
    _log(json.dumps({k: v for k, v in rec.items() if k != "mitm_tail"}, ensure_ascii=False, indent=2))
    return 0 if rec.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
