# -*- coding: utf-8 -*-
"""VMOS 원클릭 새 기기 — 기기지문 교체 후 식별값 전후 비교."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from br_emulator import vmos_cloud  # noqa: E402
from br_emulator.vmos_app_capture import (  # noqa: E402
    DEFAULT_PORT,
    SHOPEE_PKG,
    _log,
    _sh,
    _vu,
    install_system_ca,
    launch_shopee,
    setup_reverse_proxy,
)

RUNTIME = ROOT / "runtime" / "vmos_cloud"
PHONES_PATH = RUNTIME / "phones.json"


def snap(serial: str) -> dict:
    keys = (
        "ro.build.fingerprint",
        "ro.product.model",
        "ro.product.brand",
        "ro.product.device",
        "ro.product.manufacturer",
        "persist.sys.timezone",
        "persist.sys.locale",
        "gsm.sim.operator.iso-country",
        "persist.sys.cloud.wifi.mac",
        "persist.sys.cloud.bt.mac",
        "persist.sys.cloud.imei",
        "persist.sys.cloud.serialno",
    )
    props = {}
    for k in keys:
        try:
            props[k] = _sh(serial, f"getprop {k}").strip()
        except Exception as exc:
            props[k] = f"err:{exc}"
    try:
        props["android_id"] = _sh(serial, "settings get secure android_id").strip()
    except Exception as exc:
        props["android_id"] = f"err:{exc}"
    try:
        props["shopee_prefs"] = _vu(
            serial, f"ls /data/data/{SHOPEE_PKG}/shared_prefs 2>/dev/null | head",
            timeout=10,
        )
    except Exception as exc:
        props["shopee_prefs"] = f"err:{exc}"
    return props


def try_api(path: str, body: dict) -> dict:
    _log(f"API {path} {json.dumps(body, ensure_ascii=False)[:220]}")
    return vmos_cloud.api(path, method="POST", body=body)


def load_phones() -> list[dict]:
    phones = json.loads(PHONES_PATH.read_text(encoding="utf-8"))
    phones.sort(key=lambda p: int(p["shard"]))
    return phones


def wait_pad_online(pad_code: str, timeout: float = 240.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        pads = vmos_cloud.list_pads()
        pad = vmos_cloud.pick_pad(pads, pad_code)
        last = pad
        st = int(pad.get("cvmStatus") or 0)
        _log(f"  pad status={st} name={pad.get('padName')}")
        if st == 100:
            return pad
        time.sleep(8.0)
    return last


def replace_one(phone: dict) -> dict:
    pad = phone["pad"]
    port = int(phone.get("port") or DEFAULT_PORT)
    _log(f"==== Change Region {phone.get('name')} {pad} BR ====")
    rec = vmos_cloud.ensure_connected(pad)
    serial = rec["serial"]
    before = snap(serial)
    before_path = RUNTIME / f"device_ids_before_{pad}.json"
    after_path = RUNTIME / f"device_ids_after_{pad}.json"
    before_path.write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
    _log("교체 전")
    for k in ("ro.build.fingerprint", "ro.product.model", "android_id"):
        _log(f"  {k}={before.get(k)}")

    body = {
        "padCodes": [pad],
        "countryCode": "BR",
        "wipeData": True,
        "replacementRealAdiFlag": True,
    }
    last_err = None
    result = None
    for attempt in range(8):
        try:
            result = try_api("/api/padApi/replacePad", body)
            _log(f"  응답: {json.dumps(result, ensure_ascii=False)[:400]}")
            break
        except Exception as exc:
            last_err = exc
            text = str(exc)
            _log(f"  시도 {attempt + 1}/8 실패: {text[:240]}")
            if "busy" in text.lower() or "try again" in text.lower():
                time.sleep(12.0)
                continue
            time.sleep(6.0)
    if result is None:
        _log(f"원클릭 API 실패 — 기기 안 식별값만 교체합니다: {last_err}")
        import secrets
        new_aid = secrets.token_hex(8)
        _log(f"  새 android_id={new_aid}")
        _vu(serial, f"settings put secure android_id {new_aid}")
        _vu(serial, "rm -f /data/system/users/0/settings_ssaid.xml")
        _vu(serial, f"pm clear {SHOPEE_PKG}")
        _vu(serial, "pm clear com.google.android.gms")
        after = snap(serial)
        after_path.write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(f"  교체 후 android_id={after.get('android_id')}")
        install_system_ca(serial)
        setup_reverse_proxy(serial, port)
        launch_shopee(serial)
        _log("로컬 식별값 교체 완료. 쇼피 홈에서 다시 로그인하세요.")
        return rec

    _log("인스턴스 재기동 대기")
    time.sleep(12.0)
    online = wait_pad_online(pad)
    _log(f"온라인 복귀 status={online.get('cvmStatus')}")

    rec = vmos_cloud.ensure_connected(pad)
    serial = rec["serial"]
    after = None
    for attempt in range(8):
        try:
            after = snap(serial)
            if after.get("android_id") and "err:" not in str(after.get("android_id")):
                break
        except Exception as exc:
            _log(f"  식별값 재시도 {attempt + 1}: {exc}")
        time.sleep(5.0)
    if after is None:
        after = {"error": "snap failed"}
    after_path.write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
    _log("교체 후")
    for k in ("ro.build.fingerprint", "ro.product.model", "android_id"):
        _log(f"  {k}={after.get(k)}")
    changed = before.get("android_id") != after.get("android_id") or (
        before.get("ro.build.fingerprint") != after.get("ro.build.fingerprint")
    )
    _log("식별값 변경됨" if changed else "식별값이 같아 보입니다")

    try:
        install_system_ca(serial)
        setup_reverse_proxy(serial, port)
        launch_shopee(serial)
        _log("mitm CA·프록시 재적용, 쇼피 홈 기동 — 새 기기이므로 다시 로그인해야 합니다")
    except Exception as exc:
        _log(f"후처리 실패(기기는 교체됨): {exc}")
    return rec


def _session_row(rec: dict, phone: dict) -> dict:
    return {
        "padCode": rec.get("padCode") or phone["pad"],
        "padName": phone.get("name") or rec.get("padName"),
        "androidVersion": rec.get("androidVersion"),
        "country": rec.get("country") or "Brazil",
        "serial": rec.get("serial"),
        "adb": rec.get("adb"),
        "expireTime": rec.get("expireTime"),
    }


def main() -> int:
    phones = load_phones()
    wanted = [a for a in sys.argv[1:] if a]
    if wanted:
        phones = [p for p in phones if p["pad"] in wanted or p.get("name") in wanted]
    if not phones:
        _log("Change Region 대상 폰이 없습니다.")
        return 1
    saved: list[dict] = []
    for phone in phones:
        rec = replace_one(phone)
        saved.append(_session_row(rec, phone))
        payload = dict(saved[-1])
        payload["devices"] = [row["serial"] for row in saved if row.get("serial")]
        payload["phones"] = saved
        vmos_cloud.save_session(payload)
    _log(f"Change Region 완료 {len(saved)}대")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
