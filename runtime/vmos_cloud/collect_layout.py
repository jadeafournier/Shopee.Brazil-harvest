# -*- coding: utf-8 -*-
"""수집 결과와 서버 목록의 위치, 업로드 방식.

다른 대화에서 자료 수집을 이어서 할 때도 이 파일을 읽으면 된다.
- Upload: 수집한 상품 JSON. mode 가 auto 이면 저장과 함께 서버로 보낸다.
- Download: 서버가 보낸 수집 목록. 수집은 이 목록으로 한다.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRAZIL = ROOT / "Shopee.Brazil"
UPLOAD_DIR = BRAZIL / "Upload"
DOWNLOAD_DIR = BRAZIL / "Download"
TASKS_TXT = DOWNLOAD_DIR / "tasks.txt"
TASKS_JSON = DOWNLOAD_DIR / "tasks.json"
CONFIG = BRAZIL / "collect.json"


def _default() -> dict:
    return {
        "member": "m02",
        "upload_dir": str(UPLOAD_DIR),
        "download_dir": str(DOWNLOAD_DIR),
        "mode": "auto",
        "upload_once": False,
        # list_mode: "mode2" = shared global pool + 연속 pull (기본·권장)
        #            "mode1" = per-phone allocation + balance_manager
        "list_mode": "mode2",
    }


def ensure() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    ensure()
    data = _default()
    if CONFIG.is_file():
        try:
            raw = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            data.update(raw)
    if data.get("mode") not in ("auto", "manual"):
        data["mode"] = "auto"
    if data.get("list_mode") not in ("mode1", "mode2"):
        data["list_mode"] = "mode2"
    data["upload_once"] = bool(data.get("upload_once"))
    data["upload_dir"] = str(UPLOAD_DIR)
    data["download_dir"] = str(DOWNLOAD_DIR)
    if not CONFIG.is_file():
        return save(data)
    return data


def save(data: dict) -> dict:
    ensure()
    payload = _default()
    payload.update(data)
    payload["upload_dir"] = str(UPLOAD_DIR)
    payload["download_dir"] = str(DOWNLOAD_DIR)
    if payload.get("mode") not in ("auto", "manual"):
        payload["mode"] = "auto"
    if payload.get("list_mode") not in ("mode1", "mode2"):
        payload["list_mode"] = "mode2"
    payload["upload_once"] = bool(payload.get("upload_once"))
    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CONFIG)
    return payload


def set_mode(mode: str) -> dict:
    data = load()
    data["mode"] = "manual" if mode == "manual" else "auto"
    data["upload_once"] = False
    return save(data)


def list_mode() -> str:
    return load().get("list_mode") or "mode2"


def set_list_mode(mode: str) -> dict:
    data = load()
    data["list_mode"] = "mode2" if mode == "mode2" else "mode1"
    return save(data)


def request_upload() -> dict:
    data = load()
    data["upload_once"] = True
    return save(data)


def clear_upload_once() -> dict:
    data = load()
    data["upload_once"] = False
    return save(data)
