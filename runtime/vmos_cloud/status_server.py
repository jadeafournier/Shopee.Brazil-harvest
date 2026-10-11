# -*- coding: utf-8 -*-
"""로컬 수집 상태 UI — http://127.0.0.1:8765

폰 3대의 로그를 각각 읽고, 위에 합계 저장량과 합산 속도를 보여 준다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path(__file__).resolve().parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
from collect_layout import UPLOAD_DIR

OUT = UPLOAD_DIR
ADB = ROOT / "emu_setup" / "platform-tools" / "adb.exe"
_ADB_CACHE: dict[str, tuple[float, bool]] = {}


def _phone_name_sort_key(name: str) -> str:
    return str(name or "")


def _sort_phone_rows(rows: list[dict]) -> list[dict]:
    try:
        import locale

        for cand in ("ko_KR.UTF-8", "Korean_Korea.65001", "Korean_Korea.UTF-8", ""):
            try:
                if cand:
                    locale.setlocale(locale.LC_COLLATE, cand)
                else:
                    locale.setlocale(locale.LC_COLLATE, "")
                break
            except locale.Error:
                continue
        rows.sort(key=lambda p: locale.strxfrm(_phone_name_sort_key(str(p.get("name") or ""))))
    except Exception:
        rows.sort(key=lambda p: _phone_name_sort_key(str(p.get("name") or "")))
    return rows


def _load_phones() -> list[dict]:
    path = RUNTIME / "phones.json"
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = [
            {
                "shard": int(p["shard"]),
                "name": p["name"],
                "serial": p.get("serial") or "",
                "port": int(p["port"]),
                "adb_port": str(p.get("adb_port") or ""),
            }
            for p in raw
        ]
        return _sort_phone_rows(rows)
    return _sort_phone_rows([
        {"shard": 0, "name": "Phone3", "serial": "", "port": 8088, "adb_port": "5071"},
        {"shard": 1, "name": "Phone2", "serial": "", "port": 8089, "adb_port": "5072"},
        {"shard": 2, "name": "Phone1", "serial": "", "port": 8090, "adb_port": "5073"},
    ])


def phones_live() -> list[dict]:
    return _load_phones()


def ui_phone_slot_count() -> int:
    import phone_fleet

    return phone_fleet.fleet_slot_count()

_PROC_CACHE: dict = {"at": 0.0, "rows": []}
_SNAPSHOT_CACHE: dict = {"at": 0.0, "payload": None}
_SNAPSHOT_BUILD_LOCK = threading.Lock()
_SNAPSHOT_STALE_S = 1.0
_PRUNE_AT = 0.0


def _invalidate_snapshot_cache() -> None:
    _SNAPSHOT_CACHE["at"] = 0.0
    _SNAPSHOT_CACHE["payload"] = None
_OK = re.compile(r"✅\s*i\.(\d+)\.(\d+)\s+([\d.]+)s")
_MISS = re.compile(r"(?:…|\.\.\.)?\s*i\.(\d+)\.(\d+)\s+([\d.]+)s\s+미저장")
_UID = re.compile(r"userid=(\d+)")
_TARGET = re.compile(r"대상\s+(\d+)")
_DEVICE = re.compile(r"기기\s+(\S+)\s+Android\s+\S+\s+(.+)")

HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Collection</title>
<style>
  :root { color-scheme: light; }
  body {
    font-family: "Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI", sans-serif;
    margin: 32px 36px 48px; color: #143044;
    background-color: #d7ebf7;
    background-image:
      radial-gradient(980px 440px at 96% -10%, rgba(255, 255, 255, 0.82), transparent 58%),
      radial-gradient(760px 400px at -6% -4%, rgba(198, 228, 246, 0.9), transparent 64%),
      linear-gradient(180deg, #f3f9fd 0%, #d7ebf7 46%, #c9e3f3 100%);
    background-attachment: fixed;
    letter-spacing: -0.012em;
  }
  .link-live {
    position: fixed;
    top: 14px;
    right: 36px;
    z-index: 40;
    width: 46px;
    height: 46px;
    margin: 0;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: transparent;
    pointer-events: none;
  }
  .link-live-glow {
    position: absolute;
    inset: 2px;
    border-radius: 50%;
    opacity: 0;
    box-shadow: 0 0 16px rgba(59, 130, 246, 0.45);
    animation: link-glow-idle 3s ease-in-out infinite;
  }
  .link-live.on .link-live-glow {
    opacity: 0.75;
  }
  .link-live-orbit {
    position: absolute;
    inset: 5px;
    border-radius: 50%;
    border: 1.5px dashed rgba(100, 116, 139, 0.35);
    opacity: 0.55;
  }
  .link-live.on .link-live-orbit {
    border-color: rgba(56, 189, 248, 0.55);
    opacity: 1;
    animation: link-orbit 2.8s linear infinite;
  }
  .link-live-ring {
    position: absolute;
    top: 50%;
    left: 50%;
    width: 12px;
    height: 12px;
    margin: -6px 0 0 -6px;
    border-radius: 50%;
    border: 2px solid transparent;
    opacity: 0;
  }
  .link-live.on .link-live-ring {
    border-color: rgba(56, 189, 248, 0.65);
    animation: link-ripple 2.1s ease-out infinite;
  }
  .link-live.on .link-live-ring.r2 { animation-delay: 0.65s; }
  .link-live-core {
    position: absolute;
    top: 50%;
    left: 50%;
    width: 11px;
    height: 11px;
    margin: -5.5px 0 0 -5.5px;
    border-radius: 50%;
    background: #475569;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.15);
    z-index: 2;
  }
  .link-live.on .link-live-core {
    background: linear-gradient(180deg, #7dd3fc 0%, #2563eb 100%);
    box-shadow:
      0 0 12px rgba(59, 130, 246, 0.85),
      0 0 4px rgba(191, 219, 254, 0.9);
    animation: link-core-live 1.5s ease-in-out infinite;
  }
  .link-live-stream {
    position: absolute;
    top: 50%;
    left: 50%;
    width: 28px;
    height: 28px;
    margin: -14px 0 0 -14px;
    border-radius: 50%;
    opacity: 0;
    background: conic-gradient(
      from 0deg,
      transparent 0deg,
      rgba(56, 189, 248, 0.55) 36deg,
      transparent 72deg
    );
  }
  .link-live.on .link-live-stream {
    opacity: 0.85;
    animation: link-stream 1.2s linear infinite;
  }
  @keyframes link-orbit {
    to { transform: rotate(360deg); }
  }
  @keyframes link-ripple {
    0% { transform: scale(0.65); opacity: 0.85; }
    100% { transform: scale(3.6); opacity: 0; }
  }
  @keyframes link-core-live {
    0%, 100% { transform: scale(0.92); filter: brightness(1); }
    50% { transform: scale(1.08); filter: brightness(1.12); }
  }
  @keyframes link-stream {
    to { transform: rotate(360deg); }
  }
  @keyframes link-glow-idle {
    0%, 100% { opacity: 0.35; }
    50% { opacity: 0.55; }
  }
  .link-live.off .link-live-core {
    animation: link-core-off 2.8s ease-in-out infinite;
  }
  @keyframes link-core-off {
    0%, 100% { opacity: 0.55; }
    50% { opacity: 0.85; }
  }
  h1 { font-size: 28px; font-weight: 600; letter-spacing: -0.035em; margin: 0 0 6px; color: #10283c; }
  h2 { font-size: 16px; font-weight: 600; letter-spacing: -0.02em; margin: 0; color: #10283c; }
  .sub { color: #4d6d84; margin: 0 0 8px; line-height: 1.5; font-size: 14px; }
  .method {
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), #f7fbfe);
    border: 1px solid rgba(255, 255, 255, 0.9); border-radius: 20px;
    padding: 14px 18px; margin-bottom: 18px; font-size: 13px; color: #2a4c62;
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.9) inset, 0 16px 36px rgba(20, 64, 98, 0.07);
  }
  .workload {
    background: linear-gradient(
      180deg,
      rgba(255, 255, 255, 0.52) 0%,
      rgba(244, 250, 254, 0.38) 100%
    );
    border: 1px solid rgba(255, 255, 255, 0.55);
    border-radius: 22px;
    padding: 16px 18px 14px;
    margin-bottom: 18px;
    backdrop-filter: blur(18px) saturate(1.2);
    -webkit-backdrop-filter: blur(18px) saturate(1.2);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.72) inset,
      0 0 0 1px rgba(255, 255, 255, 0.35) inset,
      0 20px 48px rgba(20, 64, 98, 0.1),
      0 8px 20px rgba(20, 64, 98, 0.06),
      0 2px 6px rgba(18, 52, 76, 0.05);
  }
  .workload .row { margin-bottom: 10px; grid-template-columns: repeat(6, minmax(0, 1fr)); }
  @media (max-width: 1500px) {
    .workload .row { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  }
  @media (max-width: 720px) {
    .workload .row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  #panel-collect .workload .row .card {
    position: relative;
    overflow: hidden;
    border-radius: 22px;
    --wl-fill: rgba(62, 192, 255, 0.38);
    --wl-border: rgba(47, 143, 191, 0.42);
    --wl-accent-color: #2f8fbf;
    background-color: rgba(241, 249, 254, 0.97);
    background-image: radial-gradient(
      ellipse 64% 27% at 50% 0%,
      var(--wl-fill) 1%,
      transparent 64%
    );
    border: 1px solid var(--wl-border);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.92) inset,
      0 0 0 1px rgba(255, 255, 255, 0.55) inset,
      0 12px 28px rgba(20, 64, 98, 0.06),
      0 2px 5px rgba(20, 64, 98, 0.05);
    backdrop-filter: blur(14px) saturate(1.15);
    -webkit-backdrop-filter: blur(14px) saturate(1.15);
  }
  #panel-collect .workload .row .card.wl-stat-done-miss {
    --wl-fill: rgba(62, 192, 255, 0.22);
    --wl-border: rgba(47, 143, 191, 0.36);
    background-color: rgba(246, 251, 254, 0.97);
  }
  .wl-stat-done-miss .v.ok { color: #0d7a34; }
  #panel-collect .workload .row .card.wl-stat-active {
    --wl-fill: rgba(56, 189, 248, 0.24);
    --wl-border: rgba(14, 116, 178, 0.38);
  }
  .wl-stat-active .wl-stat-split-head {
    margin-bottom: 0;
  }
  .wl-stat-active .wl-active-phones {
    position: relative;
    z-index: 1;
    display: grid;
    grid-template-columns: repeat(10, minmax(0, 1fr));
    gap: 5px;
    align-items: end;
    margin: 14px 0 0;
    padding: 0;
    min-height: 0;
    overflow: visible;
  }
  .wl-active-glyph {
    position: relative;
    width: 100%;
    min-width: 0;
    padding: 0;
    line-height: 0;
    animation: wl-ap-idle-slot 3.6s ease-in-out infinite;
    animation-delay: calc(var(--slot-i, 0) * 0.18s);
  }
  .wl-active-glyph::after {
    content: "";
    position: absolute;
    inset: 8% 12% auto 12%;
    height: 35%;
    border-radius: 40% 40% 55% 55%;
    pointer-events: none;
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.55) 0%, transparent 100%);
    opacity: 0.65;
  }
  .wl-active-glyph .wl-ap-premium {
    display: block;
    width: 100%;
    height: auto;
    filter: drop-shadow(0 2px 4px rgba(14, 116, 178, 0.14));
  }
  .wl-active-glyph .wl-ap-body {
    fill: rgba(255, 255, 255, 0.94);
    stroke: rgba(14, 116, 178, 0.42);
    stroke-width: 1.1;
  }
  .wl-active-glyph .wl-ap-glass {
    fill: rgba(255, 255, 255, 0.35);
    stroke: none;
  }
  .wl-active-glyph .wl-ap-island {
    fill: rgba(15, 23, 42, 0.82);
    stroke: none;
  }
  .wl-active-glyph .wl-ap-home {
    fill: rgba(100, 116, 139, 0.35);
    stroke: none;
  }
  .wl-active-glyph.tone-red .wl-ap-body {
    fill: url(#wl-ap-fill-red);
    stroke: rgba(220, 38, 38, 0.5);
  }
  .wl-active-glyph.tone-blue .wl-ap-body {
    fill: url(#wl-ap-fill-blue);
    stroke: rgba(14, 116, 178, 0.45);
  }
  .wl-active-glyph.tone-yellow .wl-ap-body {
    fill: url(#wl-ap-fill-yellow);
    stroke: rgba(217, 119, 6, 0.48);
  }
  .wl-active-glyph.tone-green .wl-ap-body {
    fill: url(#wl-ap-fill-green);
    stroke: rgba(21, 128, 61, 0.48);
  }
  .wl-active-glyph .wl-ap-life {
    position: absolute;
    right: 8%;
    top: 18%;
    width: 10px;
    height: 10px;
    pointer-events: none;
  }
  .wl-active-glyph .wl-ap-life .pick-life-signal {
    width: 10px;
    height: 10px;
  }
  .wl-active-glyph .pick-life-signal {
    position: relative;
    flex: 0 0 8px;
    width: 8px;
    height: 8px;
    display: block;
    overflow: visible;
  }
  .wl-active-glyph .pick-life-signal .pls-core,
  .wl-active-glyph .pick-life-signal .pls-halo,
  .wl-active-glyph .pick-life-signal .pls-ring,
  .wl-active-glyph .pick-life-signal .pls-orbit {
    position: absolute;
    left: 50%;
    top: 50%;
    pointer-events: none;
  }
  .wl-active-glyph .pick-life-signal .pls-core {
    width: 3px;
    height: 3px;
    margin: -1.5px 0 0 -1.5px;
    border-radius: 50%;
    z-index: 3;
  }
  .wl-active-glyph .pick-life-signal .pls-halo {
    width: 7px;
    height: 7px;
    margin: -3.5px 0 0 -3.5px;
    border-radius: 50%;
    opacity: 0;
    z-index: 1;
  }
  .wl-active-glyph .pick-life-signal .pls-ring {
    width: 5px;
    height: 5px;
    margin: -2.5px 0 0 -2.5px;
    border-radius: 50%;
    border: 1px solid transparent;
    opacity: 0;
    z-index: 2;
  }
  .wl-active-glyph .pick-life-signal .pls-orbit {
    width: 8px;
    height: 8px;
    margin: -4px 0 0 -4px;
    opacity: 0;
    z-index: 2;
    animation: pls-orbit-spin 2.8s linear infinite;
  }
  .wl-active-glyph .pick-life-signal.is-dormant .pls-core {
    background: radial-gradient(circle at 35% 30%, #e2e8f0 0%, #64748b 55%, #475569 100%);
    opacity: 0.78;
  }
  .wl-active-glyph .pick-life-signal.is-ops .pls-core {
    background: radial-gradient(circle at 35% 28%, #fff 0%, #7dd3fc 40%, #0284c7 100%);
    box-shadow: 0 0 4px rgba(56, 189, 248, 0.7);
    animation: pls-ops-breathe 2.2s ease-in-out infinite;
  }
  .wl-active-glyph .pick-life-signal.is-live .pls-core {
    background: radial-gradient(circle at 32% 28%, #fff 0%, #6ee7b7 38%, #059669 100%);
    box-shadow: 0 0 5px rgba(16, 185, 129, 0.85);
    animation: pls-live-core 1.85s ease-in-out infinite;
  }
  .wl-active-glyph .pick-life-signal.is-live .pls-halo {
    background: radial-gradient(circle, rgba(52, 211, 153, 0.5) 0%, transparent 70%);
    opacity: 0.7;
    animation: pls-halo-breathe 1.85s ease-in-out infinite;
  }
  .wl-active-glyph .pick-life-signal.is-live .pls-ring {
    border-color: rgba(110, 231, 183, 0.65);
    animation: pls-ring-bloom 2.1s ease-out infinite;
  }
  .wl-active-glyph .pick-life-signal.is-live .pls-orbit { opacity: 1; }
  .wl-active-glyph .pick-life-signal.is-wait .pls-core {
    background: radial-gradient(circle at 35% 28%, #fff 0%, #fde68a 42%, #d97706 100%);
    animation: pls-wait-pulse 1.45s ease-in-out infinite;
  }
  .wl-active-glyph .pick-life-signal.is-fault .pls-core {
    background: radial-gradient(circle at 35% 28%, #fecaca 0%, #f87171 45%, #b91c1c 100%);
    animation: pls-fault-flicker 1.8s ease-in-out infinite;
  }
  .wl-active-glyph .pick-life-signal.is-captcha .pls-core {
    background: radial-gradient(circle at 35% 28%, #fff 0%, #fcd34d 40%, #ea580c 100%);
    animation: pls-wait-pulse 0.95s ease-in-out infinite;
  }
  .wl-active-glyph .pick-life-signal.is-wait .pls-ring {
    border-color: rgba(251, 191, 36, 0.6);
    animation: pls-ring-bloom 2.2s ease-out infinite;
  }
  .wl-active-glyph .pick-life-signal.is-ops .pls-halo {
    background: radial-gradient(circle, rgba(125, 211, 252, 0.5) 0%, transparent 70%);
    opacity: 0.55;
    animation: pls-halo-breathe 2.2s ease-in-out infinite;
  }
  .wl-active-glyph.sig-live {
    animation: wl-ap-glyph-live 1.55s ease-in-out infinite;
    animation-delay: calc(var(--slot-i, 0) * 0.12s);
  }
  .wl-active-glyph.sig-live .wl-ap-premium {
    animation: wl-ap-screen-live 1.55s ease-in-out infinite;
    filter: drop-shadow(0 2px 5px rgba(52, 211, 153, 0.35));
  }
  .wl-active-glyph.sig-ops {
    animation: wl-ap-glyph-ops 2s ease-in-out infinite;
  }
  .wl-active-glyph.sig-ops .wl-ap-premium {
    filter: drop-shadow(0 2px 5px rgba(56, 189, 248, 0.32));
  }
  .wl-active-glyph.sig-wait .wl-ap-island,
  .wl-active-glyph.sig-captcha .wl-ap-island {
    animation: wl-ap-notch-wait 1.2s ease-in-out infinite;
  }
  .wl-active-glyph.sig-fault .wl-ap-premium {
    animation: wl-ap-screen-fault 1.1s ease-in-out infinite;
  }
  .wl-active-glyph.is-slot-empty {
    opacity: 0.38;
    filter: saturate(0.75);
  }
  @keyframes wl-ap-idle-slot {
    0%, 100% { transform: translateY(0); opacity: 0.88; }
    50% { transform: translateY(-0.5px); opacity: 1; }
  }
  @keyframes wl-ap-glyph-live {
    0%, 100% { filter: brightness(1); }
    50% { filter: brightness(1.12) drop-shadow(0 0 4px rgba(52, 211, 153, 0.45)); }
  }
  @keyframes wl-ap-glyph-ops {
    0%, 100% { filter: brightness(1); }
    50% { filter: brightness(1.1) drop-shadow(0 0 4px rgba(56, 189, 248, 0.4)); }
  }
  @keyframes wl-ap-notch-live {
    0%, 100% { opacity: 0.75; transform: scaleX(1); }
    50% { opacity: 1; transform: scaleX(1.06); }
  }
  @keyframes wl-ap-notch-wait {
    0%, 100% { opacity: 0.65; }
    50% { opacity: 1; }
  }
  @keyframes wl-ap-screen-live {
    0%, 100% { box-shadow: inset 0 0 0 rgba(52, 211, 153, 0); }
    50% { box-shadow: inset 0 0 6px rgba(52, 211, 153, 0.35); }
  }
  @keyframes wl-ap-screen-ops {
    0%, 100% { box-shadow: inset 0 0 0 rgba(56, 189, 248, 0); }
    50% { box-shadow: inset 0 0 5px rgba(56, 189, 248, 0.32); }
  }
  @keyframes wl-ap-screen-fault {
    0%, 100% { opacity: 1; }
    45% { opacity: 0.55; }
    55% { opacity: 0.85; }
  }
  .wl-balance-badge .wl-mobil-wrap {
    width: 72px;
    height: 52px;
  }
  .wl-balance-beacon {
    position: relative;
    width: 72px;
    height: 52px;
    display: block;
    overflow: visible;
  }
  .wl-balance-beacon .wl-bal-beam {
    position: absolute; left: 50%; top: 22px;
    width: 44px; height: 3px; margin-left: -22px;
    border-radius: 999px;
    background: linear-gradient(90deg, rgba(34, 197, 94, 0.35) 0%, rgba(16, 185, 129, 0.85) 50%, rgba(34, 197, 94, 0.35) 100%);
    transform-origin: 50% 50%;
    transition: transform 0.45s ease, background 0.35s ease;
    z-index: 2;
  }
  .wl-balance-beacon .wl-bal-pan {
    position: absolute; bottom: 6px;
    width: 14px; height: 22px;
    border-radius: 6px 6px 8px 8px;
    background: linear-gradient(180deg, rgba(255,255,255,0.95) 0%, rgba(226, 232, 240, 0.9) 100%);
    border: 1px solid rgba(148, 163, 184, 0.45);
    box-shadow: 0 4px 10px rgba(15, 23, 42, 0.08);
    overflow: hidden;
    z-index: 3;
  }
  .wl-balance-beacon .wl-bal-pan.left { left: 10px; }
  .wl-balance-beacon .wl-bal-pan.right { right: 10px; }
  .wl-balance-beacon .wl-bal-fill {
    position: absolute; left: 0; right: 0; bottom: 0;
    height: 55%;
    background: linear-gradient(180deg, #6ee7b7 0%, #059669 100%);
    opacity: 0.85;
    transition: height 0.5s ease, background 0.35s ease;
  }
  .wl-balance-beacon .wl-bal-link {
    position: absolute; left: 50%; top: 18px;
    width: 10px; height: 10px; margin-left: -5px;
    border-radius: 50%;
    background: radial-gradient(circle at 35% 30%, #fff 0%, #34d399 50%, #047857 100%);
    box-shadow: 0 0 10px rgba(52, 211, 153, 0.75);
    opacity: 0;
    z-index: 4;
  }
  .wl-balance-beacon .wl-bal-ring {
    position: absolute; left: 50%; top: 14px;
    width: 18px; height: 18px; margin-left: -9px;
    border-radius: 50%;
    border: 1.5px solid rgba(52, 211, 153, 0.55);
    opacity: 0;
    z-index: 1;
  }
  .wl-balance-beacon .wl-bal-q {
    position: absolute; left: 0; right: 0; bottom: -1px;
    text-align: center;
    font-size: 8px;
    font-weight: 650;
    letter-spacing: 0.04em;
    color: #64748b;
    font-variant-numeric: tabular-nums;
  }
  .wl-balance-beacon.is-ok .wl-bal-beam {
    transform: rotate(0deg);
    animation: wl-bal-beam-steady 2.8s ease-in-out infinite;
  }
  .wl-balance-beacon.is-ok .wl-bal-pan.left .wl-bal-fill,
  .wl-balance-beacon.is-ok .wl-bal-pan.right .wl-bal-fill {
    height: 58%;
  }
  .wl-balance-beacon.is-ok .wl-bal-link {
    opacity: 1;
    animation: wl-bal-link-glow 2.4s ease-in-out infinite;
  }
  .wl-balance-beacon.is-ok .wl-bal-ring {
    opacity: 0.7;
    animation: wl-bal-ring-ok 2.6s ease-out infinite;
  }
  .wl-balance-beacon.is-bad .wl-bal-beam {
    transform: rotate(-9deg);
    background: linear-gradient(90deg, rgba(248, 113, 113, 0.45) 0%, rgba(239, 68, 68, 0.9) 50%, rgba(248, 113, 113, 0.45) 100%);
    animation: wl-bal-beam-tilt 1.1s ease-in-out infinite;
  }
  .wl-balance-beacon.is-bad .wl-bal-pan.left .wl-bal-fill {
    height: 72%;
    background: linear-gradient(180deg, #fca5a5 0%, #dc2626 100%);
  }
  .wl-balance-beacon.is-bad .wl-bal-pan.right .wl-bal-fill {
    height: 38%;
    background: linear-gradient(180deg, #fed7aa 0%, #ea580c 100%);
  }
  .wl-balance-beacon.is-bad .wl-bal-link {
    opacity: 1;
    background: radial-gradient(circle at 35% 30%, #fff 0%, #f87171 50%, #b91c1c 100%);
    box-shadow: 0 0 12px rgba(239, 68, 68, 0.85);
    animation: wl-bal-link-shake 0.85s ease-in-out infinite;
  }
  .wl-balance-beacon.is-bad .wl-bal-ring {
    opacity: 0.85;
    border-color: rgba(239, 68, 68, 0.65);
    animation: wl-bal-ring-bad 1.05s ease-out infinite;
  }
  @keyframes wl-bal-beam-steady {
    0%, 100% { filter: brightness(1); }
    50% { filter: brightness(1.12); }
  }
  @keyframes wl-bal-beam-tilt {
    0%, 100% { transform: rotate(-9deg); }
    50% { transform: rotate(-12deg); }
  }
  @keyframes wl-bal-link-glow {
    0%, 100% { transform: scale(1); box-shadow: 0 0 10px rgba(52, 211, 153, 0.65); }
    50% { transform: scale(1.1); box-shadow: 0 0 16px rgba(52, 211, 153, 0.95); }
  }
  @keyframes wl-bal-link-shake {
    0%, 100% { transform: translateX(0); }
    25% { transform: translateX(-1.5px); }
    75% { transform: translateX(1.5px); }
  }
  @keyframes wl-bal-ring-ok {
    0% { transform: scale(0.85); opacity: 0.5; }
    70% { transform: scale(1.45); opacity: 0; }
    100% { transform: scale(1.45); opacity: 0; }
  }
  @keyframes wl-bal-ring-bad {
    0% { transform: scale(0.7); opacity: 0.75; }
    100% { transform: scale(2); opacity: 0; }
  }
  .wl-upload-badge .wl-mobil-wrap {
    width: 78px;
    flex-direction: column;
    gap: 0;
    height: auto;
    min-height: 54px;
  }
  .wl-upload-beacon {
    position: relative;
    width: 78px;
    height: 54px;
    display: block;
    overflow: visible;
  }
  .wl-upload-beacon .wl-upload-aura {
    position: absolute; inset: 8px 4px 4px;
    border-radius: 50%;
    background: radial-gradient(circle at 50% 80%, rgba(56, 189, 248, 0.22) 0%, transparent 68%);
    opacity: 0.5;
    pointer-events: none;
  }
  .wl-upload-beacon .wl-upload-arc {
    position: absolute; left: 0; top: 0;
    width: 78px; height: 54px;
    overflow: visible; pointer-events: none;
  }
  .wl-upload-beacon .wl-upload-arc-path {
    fill: none;
    stroke: url(#wl-upload-grad);
    stroke-width: 2.2;
    stroke-linecap: round;
    opacity: 0.55;
  }
  .wl-upload-beacon.is-bridge .wl-upload-arc-path {
    opacity: 0.92;
    animation: wl-upload-arc-shimmer 2.5s ease-in-out infinite;
  }
  .wl-upload-beacon .wl-upload-source {
    position: absolute; left: 6px; bottom: 10px;
    width: 12px; height: 12px; border-radius: 50%;
    background: radial-gradient(circle at 32% 28%, #fff 0%, #bae6fd 42%, #0284c7 100%);
    box-shadow: 0 0 10px rgba(56, 189, 248, 0.65);
    z-index: 3;
  }
  .wl-upload-beacon .wl-upload-source::after {
    content: "";
    position: absolute; inset: -4px;
    border-radius: 50%;
    border: 1px solid rgba(125, 211, 252, 0.45);
    opacity: 0.7;
  }
  .wl-upload-beacon .wl-upload-target {
    position: absolute; right: 4px; bottom: 8px;
    width: 16px; height: 16px; z-index: 2;
  }
  .wl-upload-beacon .wl-upload-target .t-core {
    position: absolute; left: 50%; top: 50%;
    width: 8px; height: 8px; margin: -4px 0 0 -4px;
    border-radius: 50%;
    background: radial-gradient(circle at 35% 30%, #fff 0%, #c4b5fd 45%, #7c3aed 100%);
    box-shadow: 0 0 10px rgba(167, 139, 250, 0.75);
  }
  .wl-upload-beacon .wl-upload-target .t-ring {
    position: absolute; inset: 0;
    border-radius: 50%;
    border: 1.5px solid rgba(167, 139, 250, 0.5);
    opacity: 0.65;
  }
  .wl-upload-beacon .wl-upload-orb {
    position: absolute; left: 10px; bottom: 13px;
    width: 8px; height: 8px; border-radius: 50%;
    background: radial-gradient(circle at 35% 30%, #fff 0%, #93c5fd 40%, #4f46e5 100%);
    box-shadow: 0 0 10px rgba(99, 102, 241, 0.9);
    opacity: 0; z-index: 5;
  }
  .wl-upload-beacon .wl-upload-spark {
    position: absolute; width: 3px; height: 3px; border-radius: 50%;
    background: #fff; opacity: 0; z-index: 4;
    box-shadow: 0 0 8px rgba(255, 255, 255, 0.95);
  }
  .wl-upload-beacon .wl-upload-spark.s1 { left: 32px; bottom: 28px; }
  .wl-upload-beacon .wl-upload-spark.s2 { left: 48px; bottom: 22px; }
  .wl-upload-beacon.is-bridge .wl-upload-aura {
    animation: wl-upload-aura-breathe 2.8s ease-in-out infinite;
  }
  .wl-upload-beacon.is-idle { opacity: 0.52; filter: saturate(0.6); }
  .wl-upload-beacon.is-burst .wl-upload-orb {
    animation: wl-upload-orb-arc 1.2s cubic-bezier(0.25, 0.9, 0.3, 1) forwards;
  }
  .wl-upload-beacon.is-burst .wl-upload-target .t-ring {
    animation: wl-upload-ring-pop 1.2s ease-out forwards;
  }
  .wl-upload-beacon.is-burst .wl-upload-target .t-core {
    animation: wl-upload-core-flash 1.2s ease-out forwards;
  }
  .wl-upload-beacon.is-burst .wl-upload-spark.s1 { animation: wl-upload-spark 1.05s ease-out 0.06s forwards; }
  .wl-upload-beacon.is-burst .wl-upload-spark.s2 { animation: wl-upload-spark 1.05s ease-out 0.2s forwards; }
  .wl-upload-beacon.is-burst .wl-upload-source {
    animation: wl-upload-source-pulse 1.2s ease-out forwards;
  }
  .wl-upload-beacon.is-burst .wl-upload-aura {
    animation: wl-upload-glow-burst 1.2s ease-out forwards;
  }
  .wl-upload-sent-sub {
    margin-top: -2px;
    font-size: 9px;
    font-weight: 650;
    letter-spacing: 0.03em;
    color: #64748b;
    font-variant-numeric: tabular-nums;
    text-align: center;
    width: 100%;
    cursor: default;
  }
  @keyframes wl-upload-aura-breathe {
    0%, 100% { opacity: 0.42; transform: scale(0.96); }
    50% { opacity: 0.78; transform: scale(1.04); }
  }
  @keyframes wl-upload-arc-shimmer {
    0%, 100% { stroke-opacity: 0.65; filter: brightness(1); }
    50% { stroke-opacity: 1; filter: brightness(1.2); }
  }
  @keyframes wl-upload-orb-arc {
    0% { opacity: 0; transform: translate(0, 0) scale(0.55); }
    10% { opacity: 1; transform: translate(0, 0) scale(1); }
    100% { opacity: 0; transform: translate(46px, -18px) scale(0.35); }
  }
  @keyframes wl-upload-ring-pop {
    0% { transform: scale(0.75); opacity: 0.4; }
    35% { transform: scale(1.2); opacity: 1; box-shadow: 0 0 16px 5px rgba(167, 139, 250, 0.7); }
    100% { transform: scale(2.3); opacity: 0; box-shadow: none; }
  }
  @keyframes wl-upload-core-flash {
    0%, 100% { filter: brightness(1); transform: scale(1); }
    30% { filter: brightness(1.5); transform: scale(1.15); }
  }
  @keyframes wl-upload-spark {
    0% { opacity: 0; transform: scale(0.35); }
    30% { opacity: 1; transform: scale(1.15); }
    100% { opacity: 0; transform: scale(0.2); }
  }
  @keyframes wl-upload-source-pulse {
    0%, 100% { box-shadow: 0 0 10px rgba(56, 189, 248, 0.65); }
    25% { box-shadow: 0 0 18px rgba(56, 189, 248, 1); }
  }
  @keyframes wl-upload-glow-burst {
    0% { opacity: 0.45; }
    25% { opacity: 0.95; }
    100% { opacity: 0.45; }
  }
  .wl-stat-split-head {
    display: grid;
    grid-template-columns: 1fr auto;
    grid-template-rows: min-content min-content;
    column-gap: 10px;
    row-gap: 8px;
    margin-bottom: 2px;
    align-items: start;
  }
  .wl-stat-active .wl-stat-split-head {
    grid-template-rows: min-content min-content auto;
  }
  .wl-stat-split-main,
  .wl-stat-split-head > .wl-pct-badge {
    display: contents;
  }
  /* Primary stat numbers — same slot as To collect (#wl-pending) */
  .workload .row .card .wl-stat-split-head .wl-stat-split-main > .k {
    grid-column: 1;
    grid-row: 1;
    margin: 0;
    padding: 0;
    line-height: 1.2;
    min-height: 1.2em;
    align-self: start;
  }
  .workload .row .card .wl-stat-split-head .wl-pct-badge > .k {
    grid-column: 2;
    grid-row: 1;
    justify-self: end;
    align-self: start;
    margin: 0;
    font-size: 10px;
    color: #6d8da3;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    line-height: 1.2;
    min-height: 1.2em;
  }
  .workload .row .card .wl-stat-split-head .wl-stat-split-main > .v {
    grid-column: 1;
    grid-row: 2;
    margin: 0;
    padding: 0;
    align-self: start;
    justify-self: start;
    text-align: left;
    font-size: 30px;
    font-weight: 600;
    line-height: 1;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.045em;
    color: #10283c;
  }
  .workload .row .card .wl-stat-split-head .wl-pct-badge > :not(.k) {
    grid-column: 2;
    grid-row: 2;
    justify-self: end;
    align-self: start;
  }
  .wl-mobil-wrap {
    position: relative;
    height: 52px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .wl-mobil-badge .wl-mobil-wrap { width: 64px; }
  .wl-elapsed-badge .wl-mobil-wrap { width: 118px; }
  .wl-stat-side-val {
    font-size: 30px;
    font-weight: 600;
    margin-top: 0;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.045em;
    line-height: 1;
    text-align: center;
  }
  .wl-mobil-val { color: #0369a1; }
  .wl-mobil-val.ok { color: #0284c7; }
  .wl-mobil-val.idle { color: #94a3b8; }
  .wl-miss-sub {
    position: relative;
    z-index: 1;
    margin-top: 0;
    font-size: 13px;
    font-weight: 650;
    letter-spacing: 0.02em;
    color: #6d8da3;
    font-variant-numeric: tabular-nums;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .wl-miss-clear-btn {
    appearance: none;
    flex: 0 0 auto;
    width: 22px;
    height: 22px;
    margin: 0;
    padding: 0;
    border: 1px solid rgba(163, 32, 32, 0.28);
    border-radius: 7px;
    cursor: pointer;
    color: #8a2430;
    background: linear-gradient(180deg, #fff 0%, #fdeeee 100%);
    box-shadow: 0 1px 2px rgba(138, 36, 48, 0.08);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: filter 0.12s ease, border-color 0.12s ease;
  }
  .wl-miss-clear-btn:hover:not(:disabled) {
    filter: brightness(0.98);
    border-color: rgba(163, 32, 32, 0.5);
  }
  .wl-miss-clear-btn:disabled {
    opacity: 0.35;
    cursor: default;
  }
  .wl-miss-clear-btn svg {
    display: block;
    width: 12px;
    height: 12px;
    pointer-events: none;
  }
  .wl-miss-recollect-btn {
    border-color: rgba(29, 111, 168, 0.32);
    color: #1d6fa8;
    background: linear-gradient(180deg, #fff 0%, #eef6fc 100%);
  }
  .wl-miss-recollect-btn:hover:not(:disabled) {
    border-color: rgba(29, 111, 168, 0.55);
  }
  .wl-to-collect-head {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: 100%;
  }
  .wl-to-collect-reset-btn {
    appearance: none;
    flex: 0 0 auto;
    width: 22px;
    height: 22px;
    margin: 0;
    padding: 0;
    border: 1px solid rgba(90, 69, 136, 0.28);
    border-radius: 7px;
    cursor: pointer;
    color: #5a4588;
    background: linear-gradient(180deg, #fff 0%, #f3eef9 100%);
    box-shadow: 0 1px 2px rgba(90, 69, 136, 0.08);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: filter 0.12s ease, border-color 0.12s ease;
  }
  .wl-to-collect-reset-btn:hover:not(:disabled) {
    filter: brightness(0.98);
    border-color: rgba(90, 69, 136, 0.48);
  }
  .wl-to-collect-reset-btn:disabled {
    opacity: 0.35;
    cursor: default;
  }
  .wl-to-collect-reset-btn svg {
    display: block;
    width: 12px;
    height: 12px;
    pointer-events: none;
  }
  .wl-miss-sub .bad {
    font-size: inherit;
    font-weight: inherit;
    color: #a32020;
  }
  .wl-pending-sub {
    position: relative;
    z-index: 1;
    margin-top: 0;
    font-size: 13px;
    font-weight: 650;
    letter-spacing: 0.02em;
    color: #6d8da3;
    font-variant-numeric: tabular-nums;
  }
  .wl-pending-sub .wl-pending-val {
    font-size: inherit;
    font-weight: inherit;
    color: #5a4588;
  }
  .wl-pending-sub #wl-total,
  .wl-miss-sub .bad,
  .wl-miss-sub #wl-missed,
  .wl-speed-sub .wl-foot-val {
    margin-left: 0.72em;
  }
  #wl-total,
  #wl-missed,
  #wl-rate-hour .wl-foot-val,
  #wl-rate-hour {
    line-height: 1.25;
  }
  #wl-total,
  #wl-missed {
    font-size: 1.62em;
    font-weight: 700;
  }
  #panel-collect .workload .row .card.wl-stat-total {
    --wl-fill: rgba(62, 192, 255, 0.26);
  }
  #panel-collect .workload .row .card.wl-stat-watch {
    --wl-fill: rgba(62, 192, 255, 0.2);
    --wl-border: rgba(47, 143, 191, 0.38);
    --wl-list-new: #ef4444;
    --wl-list-new-soft: #fca5a5;
    --wl-list-new-ring: rgba(239, 68, 68, 0.7);
    --wl-list-new-glow: rgba(239, 68, 68, 0.95);
    --wl-list-new-halo: rgba(254, 226, 226, 0.9);
    --wl-list-same: #9b87e8;
    --wl-list-same-soft: #c4b5fd;
    --wl-list-same-ring: rgba(155, 135, 232, 0.72);
    --wl-list-same-glow: rgba(155, 135, 232, 0.92);
    --wl-list-same-halo: rgba(237, 233, 254, 0.85);
  }
  .wl-stat-watch .k { margin-bottom: 0; }
  .wl-stat-done-foot {
    position: relative;
    z-index: 1;
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 6px;
    margin-top: 4px;
    padding-left: 0;
  }
  .wl-stat-done-foot .wl-miss-sub { margin: 0; flex: 1 1 auto; min-width: 0; }
  #panel-collect .workload .row .card.wl-stat-total,
  #panel-collect .workload .row .card.wl-stat-done-miss,
  #panel-collect .workload .row .card.wl-stat-speed,
  #panel-collect .workload .row .card.wl-stat-lavender,
  #panel-collect .workload .row .card.wl-stat-active,
  #panel-collect .workload .row .card.wl-stat-watch {
    display: grid;
    grid-template-columns: 1fr auto;
    grid-template-rows: auto 1fr auto;
    column-gap: 10px;
    min-height: 0;
  }
  #panel-collect .workload .row .card.wl-stat-total .wl-stat-split-head,
  #panel-collect .workload .row .card.wl-stat-done-miss .wl-stat-split-head,
  #panel-collect .workload .row .card.wl-stat-speed .wl-stat-split-head,
  #panel-collect .workload .row .card.wl-stat-lavender .wl-stat-split-head,
  #panel-collect .workload .row .card.wl-stat-active .wl-stat-split-head,
  #panel-collect .workload .row .card.wl-stat-watch .wl-stat-split-head {
    display: contents;
  }
  #panel-collect .workload .row .card.wl-stat-total .wl-stat-split-main > .v,
  #panel-collect .workload .row .card.wl-stat-done-miss .wl-stat-split-main > .v,
  #panel-collect .workload .row .card.wl-stat-speed .wl-stat-split-main > .v,
  #panel-collect .workload .row .card.wl-stat-lavender .wl-stat-split-main > .v,
  #panel-collect .workload .row .card.wl-stat-active .wl-stat-split-main > .v {
    grid-row: 2;
    align-self: center;
  }
  #panel-collect .workload .row .card.wl-stat-watch .wl-stat-split-head .wl-pct-badge > :not(.k) {
    grid-row: 2;
    align-self: center;
    font-size: 30px;
    font-weight: 700;
    color: #0f172a;
  }
  #panel-collect .workload .row .card.wl-stat-watch > .k {
    grid-column: 1;
    grid-row: 1;
    align-self: start;
  }
  #panel-collect .workload .row .card.wl-stat-watch > .wl-watch-count {
    grid-column: 2;
    grid-row: 1;
    justify-self: end;
    align-self: start;
    font-size: 20px;
    font-weight: 800;
    line-height: 1;
    color: #0f172a;
  }
  #panel-collect .workload .row .card.wl-stat-watch > .wl-watch-visual {
    grid-column: 1 / -1;
    grid-row: 2;
    min-height: 72px;
    max-height: 88px;
    overflow: hidden;
  }
  #panel-collect .workload .row .card.wl-stat-speed .wl-stat-split-head .wl-pct-badge > :not(.k),
  #panel-collect .workload .row .card.wl-stat-lavender .wl-stat-split-head .wl-pct-badge > :not(.k) {
    grid-row: 2;
    align-self: center;
  }
  #panel-collect .workload .row .card.wl-stat-total > .wl-pending-sub {
    grid-column: 1;
    grid-row: 3;
    align-self: end;
    margin-top: 4px;
    padding-left: 0;
  }
  #panel-collect .workload .row .card.wl-stat-done-miss > .wl-stat-done-foot {
    grid-column: 1 / -1;
    grid-row: 3;
    align-self: end;
    margin-top: 4px;
  }
  #panel-collect .workload .row .card.wl-stat-speed > .wl-speed-sub {
    grid-column: 1 / -1;
    grid-row: 3;
    align-self: end;
    margin-top: 4px;
    padding-left: 0;
  }
  #panel-collect .workload .row .card.wl-stat-total .wl-stat-split-head .wl-pct-badge > :not(.k) {
    grid-column: 2;
    grid-row: 2 / 4;
    align-self: center;
    justify-self: end;
    margin-top: 14px;
  }
  #panel-collect .workload .row .card.wl-stat-lavender > .wl-phone-assign-line {
    grid-column: 1 / -1;
    grid-row: 3;
    align-self: end;
    margin-top: 4px;
  }
  #panel-collect .workload .row .card.wl-stat-active .wl-active-phones {
    grid-column: 1 / -1;
    grid-row: 3;
    align-self: end;
  }
  .wl-watch-visual {
    position: relative;
    z-index: 1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-height: 72px;
    margin-top: 4px;
  }
  .wl-radar {
    position: relative;
    width: 68px;
    height: 68px;
    flex: 0 0 auto;
    border-radius: 50%;
    background: radial-gradient(circle at 50% 50%, rgba(62, 192, 255, 0.12) 0%, rgba(18, 72, 112, 0.04) 68%, transparent 70%);
    border: 1px solid rgba(47, 143, 191, 0.22);
    box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.5) inset;
    overflow: hidden;
  }
  .wl-radar::before,
  .wl-radar::after {
    content: "";
    position: absolute;
    inset: 50%;
    border: 1px solid rgba(47, 143, 191, 0.14);
    border-radius: 50%;
    transform: translate(-50%, -50%);
  }
  .wl-radar::before { width: 46%; height: 46%; }
  .wl-radar::after { width: 78%; height: 78%; }
  .wl-radar-sweep {
    position: absolute;
    inset: -50%;
    background: conic-gradient(from 0deg, transparent 0deg, rgba(62, 192, 255, 0.55) 28deg, transparent 56deg);
    animation: wl-radar-spin 2.8s linear infinite;
    transform-origin: 50% 50%;
  }
  @keyframes wl-radar-spin {
    to { transform: rotate(360deg); }
  }
  .wl-radar-blip {
    position: absolute;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #3ec0ff;
    box-shadow: 0 0 8px rgba(62, 192, 255, 0.85);
    opacity: 0.35;
    animation: wl-radar-blip 2.8s ease-in-out infinite;
  }
  .wl-radar-blip.b1 { top: 22%; left: 58%; animation-delay: 0.4s; }
  .wl-radar-blip.b2 { top: 62%; left: 34%; animation-delay: 1.1s; }
  @keyframes wl-radar-blip {
    0%, 100% { opacity: 0.2; transform: scale(0.85); }
    45%, 55% { opacity: 1; transform: scale(1.15); }
  }
  .wl-radar.is-hit .wl-radar-blip {
    opacity: 0;
    animation: none;
  }
  .wl-radar-hit {
    position: absolute;
    top: 40%;
    left: 54%;
    width: 10px;
    height: 10px;
    transform: translate(-50%, -50%);
    opacity: 0;
    pointer-events: none;
    z-index: 2;
  }
  .wl-radar.is-hit .wl-radar-hit { opacity: 1; }
  .wl-radar-hit-core {
    position: absolute;
    inset: 0;
    border-radius: 50%;
    animation: wl-hit-spark 0.85s ease-in-out infinite;
  }
  .wl-radar.is-hit.is-new .wl-radar-hit-core {
    background: var(--wl-list-new);
    box-shadow: 0 0 12px var(--wl-list-new-glow), 0 0 4px var(--wl-list-new-halo);
  }
  .wl-radar.is-hit.is-same .wl-radar-hit-core {
    background: var(--wl-list-same);
    box-shadow: 0 0 12px var(--wl-list-same-glow), 0 0 4px var(--wl-list-same-halo);
  }
  .wl-radar-hit-ring {
    position: absolute;
    top: 50%;
    left: 50%;
    width: 10px;
    height: 10px;
    margin: -5px 0 0 -5px;
    border-radius: 50%;
    border: 2px solid transparent;
    opacity: 0;
    transform: scale(0.5);
  }
  .wl-radar.is-hit.is-new .wl-radar-hit-ring {
    border-color: var(--wl-list-new-ring);
    animation: wl-radar-ripple 1.75s ease-out infinite;
  }
  .wl-radar.is-hit.is-same .wl-radar-hit-ring {
    border-color: var(--wl-list-same-ring);
    animation: wl-radar-ripple 2s ease-out infinite;
  }
  .wl-radar-hit-ring.r2 { animation-delay: 0.42s; }
  .wl-radar-hit-ring.r3 { animation-delay: 0.84s; }
  @keyframes wl-radar-ripple {
    0% { transform: scale(0.55); opacity: 0.9; }
    100% { transform: scale(4.5); opacity: 0; }
  }
  @keyframes wl-hit-spark {
    0%, 100% { transform: scale(0.8); opacity: 0.7; }
    50% { transform: scale(1.35); opacity: 1; }
  }
  .wl-ingest {
    position: relative;
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    gap: 4px;
    width: 52px;
    height: 68px;
    padding: 0 0 2px;
    transition: opacity 0.35s ease;
  }
  .wl-ingest.is-idle { opacity: 0.72; }
  .wl-ingest.is-active { opacity: 1; }
  .wl-ingest-icon {
    width: 22px;
    height: 22px;
    color: #6d8da3;
    flex: 0 0 auto;
  }
  .wl-ingest.is-active.is-new .wl-ingest-icon { color: var(--wl-list-new); }
  .wl-ingest.is-active.is-same .wl-ingest-icon { color: var(--wl-list-same); }
  .wl-ingest.is-idle .wl-ingest-icon {
    animation: wl-ingest-idle-pulse 2.4s ease-in-out infinite;
  }
  @keyframes wl-ingest-idle-pulse {
    0%, 100% { opacity: 0.55; transform: translateY(0); }
    50% { opacity: 1; transform: translateY(2px); }
  }
  .wl-ingest-stream {
    display: flex;
    align-items: flex-end;
    justify-content: center;
    gap: 4px;
    flex: 1 1 auto;
    width: 100%;
    min-height: 0;
  }
  .wl-ingest-col {
    display: flex;
    flex-direction: column;
    gap: 3px;
    width: 8px;
    height: 100%;
    justify-content: flex-start;
  }
  .wl-ingest-col i {
    display: block;
    width: 100%;
    height: 8px;
    border-radius: 2px;
    background: linear-gradient(180deg, #94a3b8 0%, #64748b 100%);
    opacity: 0;
    transform: translateY(-10px);
  }
  .wl-ingest.is-idle .wl-ingest-col i {
    opacity: 0.35;
    animation: wl-ingest-drop-slow 1.6s ease-in infinite;
  }
  .wl-ingest.is-idle .wl-ingest-col.delay i { animation-delay: 0.35s; }
  .wl-ingest.is-idle .wl-ingest-col.delay2 { display: none; }
  .wl-ingest.is-active .wl-ingest-col i {
    animation: wl-ingest-drop 0.75s ease-in infinite;
  }
  .wl-ingest.is-active.is-new .wl-ingest-col i {
    background: linear-gradient(180deg, var(--wl-list-new-soft) 0%, var(--wl-list-new) 100%);
    box-shadow: 0 0 6px var(--wl-list-new-glow);
  }
  .wl-ingest.is-active.is-same .wl-ingest-col i {
    background: linear-gradient(180deg, var(--wl-list-same-soft) 0%, var(--wl-list-same) 100%);
    box-shadow: 0 0 5px var(--wl-list-same-glow);
  }
  .wl-ingest.is-active.is-new .wl-ingest-col i { animation-duration: 0.65s; }
  .wl-ingest.is-active.is-same .wl-ingest-col i { animation-duration: 0.95s; }
  .wl-ingest-col.delay i { animation-delay: 0.12s; }
  .wl-ingest-col.delay2 i { animation-delay: 0.28s; }
  .wl-ingest-col i:nth-child(2) { animation-delay: 0.22s; }
  .wl-ingest-col i:nth-child(3) { animation-delay: 0.44s; }
  @keyframes wl-ingest-drop {
    0% { opacity: 0; transform: translateY(-12px); }
    30% { opacity: 1; }
    100% { opacity: 0; transform: translateY(40px); }
  }
  @keyframes wl-ingest-drop-slow {
    0% { opacity: 0.15; transform: translateY(-8px); }
    45% { opacity: 0.5; }
    100% { opacity: 0; transform: translateY(28px); }
  }
  .wl-ingest-mark {
    position: absolute;
    right: 6px;
    bottom: 4px;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.25s ease;
  }
  .wl-ingest.is-active.is-new .wl-ingest-mark {
    opacity: 1;
    background: var(--wl-list-new);
    box-shadow: 0 0 8px var(--wl-list-new-glow);
  }
  .wl-ingest.is-active.is-same .wl-ingest-mark {
    opacity: 1;
    background: transparent;
    border: 2px solid var(--wl-list-same);
    box-shadow: none;
    width: 8px;
    height: 8px;
  }
  .wl-ingest-slot { position: relative; display: flex; flex-direction: column; align-items: center; height: 100%; }
  .wl-pct-badge { flex: 0 0 auto; }
  @property --wl-pct {
    syntax: "<number>";
    inherits: false;
    initial-value: 0;
  }
  .wl-pct-ring-wrap {
    position: relative;
    width: 52px;
    height: 52px;
    filter: drop-shadow(0 2px 10px rgba(37, 99, 235, 0.18));
  }
  .wl-pct-ring-wrap::before {
    content: "";
    position: absolute;
    inset: -5px;
    border-radius: 50%;
    pointer-events: none;
    z-index: 0;
    box-shadow:
      0 0 14px rgba(56, 189, 248, 0.42),
      0 0 26px rgba(37, 99, 235, 0.22);
    opacity: 0.55;
    animation: wl-pct-halo 3.2s ease-in-out infinite;
  }
  .wl-pct-ring-wrap.is-rising::before {
    opacity: 0.9;
    animation: wl-pct-halo-rise 0.75s ease-in-out infinite alternate;
    box-shadow:
      0 0 18px rgba(56, 189, 248, 0.62),
      0 0 32px rgba(59, 130, 246, 0.38);
  }
  .wl-pct-ring-wrap.is-complete::before {
    box-shadow:
      0 0 16px rgba(96, 165, 250, 0.58),
      0 0 30px rgba(37, 99, 235, 0.32);
  }
  @keyframes wl-pct-halo {
    0%, 100% { opacity: 0.45; transform: scale(0.97); }
    50% { opacity: 0.75; transform: scale(1.03); }
  }
  @keyframes wl-pct-halo-rise {
    0% { opacity: 0.65; transform: scale(0.98); }
    100% { opacity: 1; transform: scale(1.06); }
  }
  .wl-pct-ring {
    position: absolute;
    inset: 0;
    z-index: 1;
    border-radius: 50%;
    --wl-pct: 0;
    background: conic-gradient(
      from -90deg,
      #7dd3fc 0deg,
      #2563eb calc(var(--wl-pct) * 3.6deg),
      rgba(18, 72, 112, 0.09) calc(var(--wl-pct) * 3.6deg),
      rgba(18, 72, 112, 0.09) 360deg
    );
    box-shadow:
      0 0 0 1px rgba(125, 211, 252, 0.45),
      0 0 12px rgba(59, 130, 246, 0.35);
    transition: --wl-pct 0.75s cubic-bezier(0.22, 1, 0.36, 1);
  }
  .wl-pct-ring::after {
    content: "";
    position: absolute;
    inset: 4px;
    border-radius: 50%;
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(241, 249, 254, 0.96) 100%);
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.95) inset;
  }
  .wl-pct-ring-glow {
    position: absolute;
    inset: -2px;
    border-radius: 50%;
    opacity: 0;
    pointer-events: none;
    background: conic-gradient(
      from -90deg,
      transparent 0deg,
      rgba(62, 192, 255, 0.55) 40deg,
      transparent 80deg
    );
  }
  .wl-pct-ring-wrap.is-idle .wl-pct-ring-glow {
    opacity: 0.45;
    animation: wl-pct-orbit 4.5s linear infinite;
  }
  .wl-pct-ring-wrap.is-rising .wl-pct-ring-glow {
    opacity: 0.85;
    animation: wl-pct-orbit 1.1s linear infinite;
  }
  .wl-pct-ring-wrap.is-rising .wl-pct-ring {
    animation: wl-pct-ring-surge 0.55s ease-out infinite alternate;
  }
  .wl-pct-ring-wrap.is-idle .wl-pct-ring {
    animation: wl-pct-ring-breathe 3.2s ease-in-out infinite;
  }
  .wl-pct-ring-wrap.is-complete .wl-pct-ring {
    background: conic-gradient(
      from -90deg,
      #93c5fd 0deg,
      #1d4ed8 calc(var(--wl-pct) * 3.6deg),
      rgba(18, 72, 112, 0.09) calc(var(--wl-pct) * 3.6deg),
      rgba(18, 72, 112, 0.09) 360deg
    );
    box-shadow:
      0 0 0 1px rgba(147, 197, 253, 0.55),
      0 0 14px rgba(37, 99, 235, 0.45);
    animation: wl-pct-ring-complete 2.8s ease-in-out infinite;
  }
  @keyframes wl-pct-orbit {
    to { transform: rotate(360deg); }
  }
  @keyframes wl-pct-ring-breathe {
    0%, 100% { filter: brightness(1) saturate(1); }
    50% { filter: brightness(1.06) saturate(1.08); }
  }
  @keyframes wl-pct-ring-surge {
    0% { filter: brightness(1.05) drop-shadow(0 0 0 rgba(59, 130, 246, 0)); }
    100% { filter: brightness(1.18) drop-shadow(0 0 14px rgba(59, 130, 246, 0.55)); }
  }
  @keyframes wl-pct-ring-complete {
    0%, 100% { filter: brightness(1.02); }
    50% { filter: brightness(1.1); }
  }
  .wl-pct-val {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    font-weight: 650;
    letter-spacing: -0.03em;
    font-variant-numeric: tabular-nums;
    color: #1a5a7a;
    z-index: 2;
    transition: color 0.35s ease, transform 0.35s ease;
  }
  .wl-pct-ring-wrap.is-idle .wl-pct-val {
    animation: wl-pct-text-idle 3.2s ease-in-out infinite;
  }
  .wl-pct-ring-wrap.is-rising .wl-pct-val {
    color: #1d4ed8;
    text-shadow: 0 0 10px rgba(59, 130, 246, 0.35);
    animation: wl-pct-text-rise 0.65s ease-out infinite alternate;
  }
  .wl-pct-ring-wrap.is-complete .wl-pct-val {
    color: #1e40af;
    text-shadow: 0 0 8px rgba(96, 165, 250, 0.4);
    animation: wl-pct-text-idle 2.8s ease-in-out infinite;
  }
  @keyframes wl-pct-text-idle {
    0%, 100% { transform: scale(1); opacity: 0.88; }
    50% { transform: scale(1.03); opacity: 1; }
  }
  @keyframes wl-pct-text-rise {
    0% { transform: scale(1); }
    100% { transform: scale(1.1); }
  }
  #panel-collect .workload .row .card.wl-stat-speed {
    --wl-fill: rgba(62, 192, 255, 0.3);
    --wl-border: rgba(47, 143, 191, 0.4);
    --wl-accent-color: #3ec0ff;
  }
  .wl-elapsed-val { color: #6d28d9; }
  .wl-elapsed-val.live { color: #7c3aed; }
  .wl-elapsed-val.idle { color: #94a3b8; }
  .wl-stat-speed .wl-speed-sub {
    position: relative;
    z-index: 1;
    margin-top: 0;
    font-size: 13px;
    font-weight: 650;
    letter-spacing: 0.02em;
    color: #6d8da3;
    font-variant-numeric: tabular-nums;
  }
  .wl-speed-sub .wl-foot-val {
    font-size: 1.62em;
    font-weight: 700;
    color: #10283c;
    font-variant-numeric: tabular-nums;
  }
  #panel-collect .workload .row .card.wl-stat-lavender {
    --wl-fill: rgba(155, 135, 232, 0.36);
    --wl-border: rgba(122, 106, 196, 0.44);
    --wl-accent-color: #7a6ac4;
    background-color: rgba(248, 245, 252, 0.97);
  }
  /* 상단은 별도 띠 없이 — 좌·우·하와 같은 --wl-border, 두께만 2px */
  #panel-collect .workload .row .card.wl-top-accent,
  .wl-pick.wl-top-accent {
    border-top-width: 2px;
    border-top-color: var(--wl-border);
  }
  .workload .row .card .k,
  .workload .row .card .v { position: relative; z-index: 1; }
  .wl-stat-unassigned-row {
    position: relative;
    z-index: 1;
  }
  .workload-picks-wrap {
    margin: 12px 0 8px;
  }
  /* POOL LEFT(lavender) 카드 mode2 전용 — 왼쪽 Temp missed / 오른쪽 Temp gone
     / 아래 Perm gone + 정산 배지. 다른 카드와 같은 split-head 레이아웃 사용. */
  /* 오른쪽 제목(배지) 아래 Temp gone 값: mode1 에선 숨김, mode2 에서만 표시 */
  .wl-cyc-gone-val { display: none; }
  body.lm2 .wl-stat-lavender .wl-cyc-gone-val {
    display: block;
    color: #7c3aed;
  }
  /* 왼쪽 큰 값(Temp missed): mode2 에서 주황 */
  body.lm2 .wl-stat-lavender .wl-unassigned-val { color: #d97706; }
  /* 맨 아래 Perm gone + 정산 배지 줄 */
  .wl-lav-foot { display: none; }
  body.lm2 #panel-collect .workload .row .card.wl-stat-lavender > .wl-lav-foot {
    display: flex;
    align-items: center;
    gap: 8px;
    grid-column: 1 / -1;
    grid-row: 3;
    align-self: end;
    margin-top: 4px;
    padding-top: 8px;
    border-top: 1px solid rgba(47, 143, 191, 0.18);
  }
  .wl-lav-perm {
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    font-size: 15px;
  }
  .wl-lav-perm-k {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    color: #6d8da3;
  }
  .wl-lav-perm-v { color: #be123c; font-weight: 800; }
  .wl-cycle-acct {
    margin-left: auto;
    align-self: center;
    font-size: 9.5px;
    font-weight: 800;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 4px 9px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .wl-cycle-acct.ok { color: #047857; background: rgba(16, 185, 129, 0.14); }
  .wl-cycle-acct.bad { color: #b91c1c; background: rgba(239, 68, 68, 0.14); }
  /* 목록관리2(mode2): 전역 풀 방식 — 폰별 할당·재분배 UI 숨김 */
  body.lm2 #wl-picks { display: none; }
  body.lm2 #wl-auto-wrap { display: none; }
  body.lm2 #wl-phone-assign { display: none; }
  /* mode2: 배지 제목 자리는 'Temp gone' 라벨로 쓰이므로 정상 표시 */
  body.lm2 .wl-stat-lavender .wl-pct-badge.wl-auto-badge > .k { opacity: 1; visibility: visible; }
  /* mode2: Missed 라인은 '이번 목록 업로드 수' 표시용 — 수정 버튼 숨김 */
  body.lm2 #wl-missed-clear,
  body.lm2 #wl-missed-recollect { display: none; }
  .wl-picks-head {
    margin: 0 0 8px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #6d8da3;
  }
  .workload-picks-phones {
    display: grid;
    grid-template-columns: repeat(10, minmax(0, 1fr));
    gap: 8px;
    align-items: stretch;
  }
  .workload-picks-phones .wl-pick {
    padding: 9px 8px 8px;
    border-radius: 15px;
  }
  .workload-picks-phones .wl-pick::after,
  .workload-picks-phones .wl-pick-glow {
    display: none;
  }
  .workload-picks-phones .wl-pick-top {
    display: flex;
    align-items: center;
    gap: 7px;
    margin-bottom: 7px;
    min-width: 0;
  }
  .workload-picks-phones .wl-pick-head-body {
    flex: 1 1 auto;
    min-width: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
    align-items: center;
    column-gap: 6px;
  }
  .workload-picks-phones .wl-index {
    width: 20px;
    height: 20px;
    font-size: 9px;
    border-width: 1px;
    box-shadow: none;
    flex: 0 0 auto;
  }
  .workload-picks-phones .wl-pick-phone {
    grid-column: 1;
    justify-self: start;
    min-width: 0;
    max-width: 100%;
    font-size: 10px;
    padding-right: 2px;
  }
  .workload-picks-phones .wl-pick-head-body .wl-count {
    grid-column: 2;
    justify-self: center;
    font-size: 15px;
    font-weight: 650;
    line-height: 1;
    margin: 0;
    padding: 0 2px;
    text-shadow: none;
    font-variant-numeric: tabular-nums;
  }
  .workload-picks-phones .wl-pick-head-body .wl-count.is-zero {
    color: #94a3b8;
  }
  .workload-picks-phones .wl-pick.has-queue .wl-pick-head-body .wl-count {
    color: #0c4a6e;
  }
  .workload-picks-phones .wl-actions {
    gap: 6px;
    margin-top: 0;
    padding-top: 1px;
  }
  .workload-picks-phones .wl-qty-input {
    padding: 6px 5px;
    font-size: 12px;
    border-radius: 8px;
  }
  .workload-picks-phones .wl-icon-btn {
    width: 28px;
    height: 28px;
    border-radius: 9px;
  }
  .workload-picks-phones .wl-icon-btn svg {
    width: 15px;
    height: 15px;
  }
  .workload-picks-phones .wl-pick.is-slot-empty {
    opacity: 0.72;
  }
  .workload-picks-phones .wl-pick.is-slot-empty .wl-pick-phone {
    color: #94a3b8;
  }
  .wl-unassigned-val { color: #5a4588; }
  .wl-stat-lavender .wl-stat-split-head {
    margin-bottom: 0;
  }
  .wl-stat-lavender .wl-phone-assign-line {
    margin-top: 0;
    padding-top: 2px;
  }
  .wl-phone-assign-line {
    position: relative;
    z-index: 1;
    margin-top: 4px;
    width: 100%;
    text-align: center;
    overflow: hidden;
    -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 5%, #000 95%, transparent 100%);
    mask-image: linear-gradient(90deg, transparent 0, #000 5%, #000 95%, transparent 100%);
  }
  .wl-phone-assign-line .wl-phone-assign-track {
    display: inline-block;
    white-space: nowrap;
    font-size: 11px;
    font-weight: 650;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.06em;
    color: #5a4588;
    line-height: 1.35;
    padding: 0 2px;
  }
  .wl-phone-assign-line.is-scroll {
    text-align: left;
  }
  .wl-phone-assign-line.is-scroll .wl-phone-assign-track {
    animation: wl-phone-assign-marquee var(--wl-marquee-dur, 28s) linear infinite;
    will-change: transform;
  }
  @keyframes wl-phone-assign-marquee {
    0% { transform: translateX(0); }
    100% { transform: translateX(-50%); }
  }
  .wl-stat-lavender .v.wl-pending-val { color: #5a4588; font-variant-numeric: tabular-nums; }
  .wl-stat-lavender .wl-auto-badge .k {
    visibility: hidden;
  }
  .wl-auto-icon-wrap {
    position: relative;
    width: 52px;
    height: 52px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .wl-auto-icon-wrap::before {
    content: "";
    position: absolute;
    inset: 6px;
    border-radius: 50%;
    pointer-events: none;
    opacity: 0;
    background: radial-gradient(circle at 50% 42%, rgba(255, 255, 255, 0.55) 0%, rgba(196, 181, 253, 0.18) 42%, transparent 72%);
    transition: opacity 0.28s ease;
  }
  .wl-auto-icon-wrap:hover::before {
    opacity: 1;
  }
  .wl-auto-icon-wrap.is-busy::before {
    opacity: 0.85;
    animation: wl-auto-soft-pulse 1.1s ease-in-out infinite alternate;
  }
  @keyframes wl-auto-soft-pulse {
    0% { transform: scale(0.96); opacity: 0.65; }
    100% { transform: scale(1.02); opacity: 0.9; }
  }
  .wl-auto-icon {
    position: relative;
    z-index: 1;
    flex: 0 0 auto;
    appearance: none;
    border: none;
    cursor: pointer;
    width: 46px;
    height: 46px;
    border-radius: 50%;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: #f8f5ff;
    background:
      radial-gradient(ellipse 80% 55% at 50% 18%, rgba(255, 255, 255, 0.42) 0%, transparent 55%),
      linear-gradient(165deg, #ddd6fe 0%, #a78bfa 28%, #7c3aed 62%, #5b21b6 100%);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.55) inset,
      0 -1px 0 rgba(91, 33, 182, 0.35) inset,
      0 0 0 1px rgba(255, 255, 255, 0.28),
      0 4px 14px rgba(109, 40, 217, 0.22);
    transition: transform 0.2s ease, box-shadow 0.2s ease, filter 0.2s ease;
  }
  .wl-auto-icon svg {
    display: block;
    width: 21px;
    height: 21px;
    opacity: 0.96;
    filter: drop-shadow(0 1px 1px rgba(46, 16, 101, 0.2));
  }
  .wl-auto-icon-wrap.is-busy .wl-auto-icon {
    cursor: wait;
    filter: brightness(1.06);
    animation: wl-auto-btn-busy 0.85s ease-in-out infinite alternate;
  }
  .wl-auto-icon-wrap.is-busy .wl-auto-icon svg {
    animation: wl-auto-icon-spin 0.95s ease-in-out infinite;
  }
  @keyframes wl-auto-btn-busy {
    0% { box-shadow: 0 1px 0 rgba(255, 255, 255, 0.5) inset, 0 0 0 1px rgba(255, 255, 255, 0.32), 0 4px 16px rgba(124, 58, 237, 0.28); }
    100% { box-shadow: 0 1px 0 rgba(255, 255, 255, 0.58) inset, 0 0 0 1px rgba(233, 213, 255, 0.45), 0 6px 20px rgba(124, 58, 237, 0.38); }
  }
  @keyframes wl-auto-icon-spin {
    0% { transform: rotate(-6deg); }
    50% { transform: rotate(6deg); }
    100% { transform: rotate(-6deg); }
  }
  .wl-auto-icon:hover:not(:disabled) {
    transform: translateY(-1px) scale(1.02);
    filter: brightness(1.04);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.6) inset,
      0 0 0 1px rgba(255, 255, 255, 0.38),
      0 8px 22px rgba(109, 40, 217, 0.28);
  }
  .wl-auto-icon:active:not(:disabled) {
    transform: translateY(0) scale(0.98);
    filter: brightness(0.97);
  }
  .wl-auto-icon:disabled {
    opacity: 0.42;
    cursor: default;
    filter: grayscale(0.15);
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.35) inset, 0 0 0 1px rgba(255, 255, 255, 0.15);
  }
  .wl-pick {
    position: relative; display: flex; flex-direction: column; min-height: 0;
    border-radius: 22px;
    --wl-fill: rgba(62, 192, 255, 0.34);
    --wl-border: rgba(47, 143, 191, 0.4);
    --wl-accent-color: #2f8fbf;
    --wl-queue-fill: 0%;
    border: 1px solid var(--wl-border);
    background-color: rgba(241, 249, 254, 0.97);
    background-image:
      radial-gradient(ellipse 90% 40% at 50% -10%, rgba(255, 255, 255, 0.75) 0%, transparent 55%),
      radial-gradient(ellipse 64% 27% at 50% 0%, var(--wl-fill) 1%, transparent 64%);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.92) inset,
      0 0 0 1px rgba(255, 255, 255, 0.5) inset,
      0 16px 36px rgba(20, 64, 98, 0.09),
      0 2px 8px rgba(20, 64, 98, 0.06);
    backdrop-filter: blur(16px) saturate(1.3);
    -webkit-backdrop-filter: blur(16px) saturate(1.3);
    padding: 12px 11px 10px;
    color: #143044;
    overflow: hidden;
    transition: box-shadow 0.25s ease, transform 0.25s ease;
  }
  .wl-pick::after {
    content: "";
    position: absolute; inset: 0;
    border-radius: inherit;
    pointer-events: none;
    background: linear-gradient(115deg, transparent 40%, rgba(255, 255, 255, 0.45) 50%, transparent 60%);
    opacity: 0;
    transform: translateX(-30%);
    transition: opacity 0.3s ease;
  }
  .wl-pick:hover::after {
    opacity: 0.35;
    animation: wl-pick-shimmer 1.8s ease-in-out infinite;
  }
  .wl-pick .wl-pick-glow {
    position: absolute;
    right: -20px; bottom: -24px;
    width: 80px; height: 80px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(62, 192, 255, 0.22) 0%, transparent 70%);
    pointer-events: none;
    opacity: 0.5;
  }
  .wl-pick.has-queue .wl-pick-glow {
    opacity: 0.85;
    animation: wl-pick-glow-breathe 2.6s ease-in-out infinite;
  }
  .wl-pick:hover {
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.95) inset,
      0 0 0 1px rgba(255, 255, 255, 0.6) inset,
      0 20px 40px rgba(20, 64, 98, 0.11),
      0 0 0 1px rgba(62, 192, 255, 0.12);
  }
  @keyframes wl-pick-shimmer {
    0% { transform: translateX(-35%); }
    100% { transform: translateX(35%); }
  }
  @keyframes wl-pick-glow-breathe {
    0%, 100% { transform: scale(0.95); opacity: 0.65; }
    50% { transform: scale(1.05); opacity: 0.95; }
  }
  .wl-pick.unassigned {
    --wl-fill: rgba(155, 135, 232, 0.36);
    --wl-border: rgba(122, 106, 196, 0.44);
    background-color: rgba(248, 245, 252, 0.97);
    --wl-accent-color: #7a6ac4;
  }
  .wl-pick.wl-top-accent > * { position: relative; z-index: 1; }
  .wl-pick-top {
    display: flex; align-items: center; gap: 8px;
    margin: 0 0 8px;
    min-width: 0;
  }
  .wl-index {
    flex: 0 0 auto;
    width: 34px; height: 34px;
    border-radius: 50%;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 13px; font-weight: 650;
    font-variant-numeric: tabular-nums;
    color: #0f4c6e;
    background: radial-gradient(circle at 35% 28%, #fff 0%, #e0f2fe 48%, #bae6fd 100%);
    border: 1.5px solid var(--wl-border);
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.95) inset, 0 4px 12px rgba(47, 143, 191, 0.14);
  }
  .wl-pick-phone {
    flex: 1 1 auto; min-width: 0;
    font-size: 12px; font-weight: 650;
    letter-spacing: -0.02em;
    color: #10283c;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .wl-metric {
    flex: 1 1 auto; min-width: 0;
    padding: 8px 11px 9px;
    margin: 0 0 8px;
    border-radius: 16px;
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, rgba(244, 250, 253, 0.78) 100%);
    border: 1px solid rgba(126, 176, 214, 0.34);
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.95) inset, 0 8px 18px rgba(20, 64, 98, 0.06);
  }
  .wl-metric .k { display: block; margin: 0 0 3px; font-size: 8px; letter-spacing: 0.14em; }
  .wl-count-row { display: flex; flex-direction: column; gap: 6px; }
  .wl-pick .wl-count {
    font-size: 30px; font-weight: 600; font-variant-numeric: tabular-nums;
    letter-spacing: -0.045em; line-height: 1;
    color: #0c4a6e; margin: 0;
    text-shadow: 0 1px 0 rgba(255, 255, 255, 0.85);
  }
  .wl-pick .wl-count.is-zero { color: #94a3b8; }
  .wl-queue-meter {
    height: 5px; border-radius: 999px;
    background: rgba(148, 163, 184, 0.2);
    overflow: hidden;
    box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.08);
  }
  .wl-queue-meter .wl-queue-fill {
    display: block; height: 100%; width: var(--wl-queue-fill, 0%);
    border-radius: inherit;
    background: linear-gradient(90deg, #38bdf8 0%, #2563eb 55%, #6366f1 100%);
    box-shadow: 0 0 10px rgba(56, 189, 248, 0.45);
    transition: width 0.45s ease;
  }
  .wl-pick.has-queue .wl-queue-meter .wl-queue-fill {
    animation: wl-queue-pulse 2.4s ease-in-out infinite;
  }
  @keyframes wl-queue-pulse {
    0%, 100% { filter: brightness(1); }
    50% { filter: brightness(1.15); }
  }
  .wl-pick.unassigned .wl-count { color: #5a4588; }
  .wl-actions {
    margin-top: auto;
    display: flex; flex-direction: row; align-items: center; gap: 6px;
  }
  .wl-qty-input {
    flex: 1 1 auto; min-width: 0; margin: 0; padding: 8px 8px;
    border-radius: 10px; border: 1px solid rgba(18, 72, 112, 0.14);
    background: #fff; color: #10283c;
    font: inherit; font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums;
    text-align: center;
    box-shadow: 0 1px 2px rgba(20, 64, 98, 0.06);
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
  }
  .wl-qty-input:focus {
    outline: none;
    border-color: rgba(47, 143, 191, 0.65);
    box-shadow: 0 0 0 3px rgba(62, 192, 255, 0.22);
  }
  .wl-qty-input::-webkit-outer-spin-button,
  .wl-qty-input::-webkit-inner-spin-button { opacity: 0.65; }
  .wl-icon-btns { flex: 0 0 auto; display: flex; gap: 4px; }
  .wl-icon-btn {
    appearance: none; border: 0; cursor: pointer;
    width: 36px; height: 36px; border-radius: 11px; padding: 0;
    display: inline-flex; align-items: center; justify-content: center;
    color: #f4f9fc;
    background: linear-gradient(180deg, #1a4560 0%, #12344c 100%);
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.12) inset, 0 4px 10px rgba(18, 52, 76, 0.16);
    transition: transform 0.12s ease, box-shadow 0.12s ease, filter 0.12s ease;
  }
  .wl-icon-btn:hover:not(:disabled) {
    filter: brightness(1.06);
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.14) inset, 0 6px 14px rgba(18, 52, 76, 0.2);
  }
  .wl-icon-btn:active:not(:disabled) { transform: translateY(1px); }
  .wl-icon-btn.secondary {
    background: linear-gradient(180deg, #fff 0%, #f2f6f9 100%);
    color: #23475f;
    border: 1px solid rgba(18, 72, 112, 0.18);
    box-shadow: 0 1px 2px rgba(20, 64, 98, 0.06);
  }
  .wl-icon-btn.secondary:hover:not(:disabled) {
    filter: none;
    border-color: rgba(47, 143, 191, 0.45);
    background: linear-gradient(180deg, #fff 0%, #eef6fc 100%);
  }
  .wl-icon-btn:disabled { opacity: 0.45; cursor: default; transform: none; filter: none; }
  .wl-icon-btn svg { display: block; pointer-events: none; }
  #panel-collect .collect-actions { margin: 8px 0 14px; }
  .collect-ref {
    position: relative;
    overflow: hidden;
    margin: 28px 0 0;
    padding: 18px 20px 16px;
    border-radius: 22px;
    border: 1px solid rgba(47, 143, 191, 0.28);
    border-top-width: 2px;
    border-top-color: rgba(47, 143, 191, 0.38);
    background-color: rgba(251, 253, 255, 0.94);
    background-image:
      radial-gradient(ellipse 72% 42% at 50% 0%, rgba(62, 192, 255, 0.14) 0%, transparent 62%),
      linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(244, 250, 254, 0.92) 100%);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.95) inset,
      0 14px 32px rgba(20, 64, 98, 0.06),
      0 2px 6px rgba(20, 64, 98, 0.04);
    backdrop-filter: blur(12px) saturate(1.12);
    -webkit-backdrop-filter: blur(12px) saturate(1.12);
  }
  .collect-ref-head {
    margin: 0 0 12px;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(18, 72, 112, 0.08);
  }
  .collect-ref-head h2 {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: #10283c;
    margin: 0;
  }
  .collect-ref-body {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .collect-ref-item { margin: 0; }
  .collect-ref-title {
    display: block;
    margin: 0 0 4px;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: -0.015em;
    color: #143044;
  }
  .collect-ref-v {
    margin: 0;
    font-size: 14px;
    line-height: 1.55;
    color: #4d6d84;
    letter-spacing: -0.01em;
    font-weight: 400;
  }
  .collect-ref-v.path {
    font-family: inherit;
    word-break: break-all;
  }
  .row {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
    gap: 16px; margin-bottom: 20px;
  }
  #panel-collect .row {
    grid-template-columns: repeat(7, minmax(0, 1fr));
    width: 100%;
  }
  #panel-collect .row .card {
    background: rgba(255, 255, 255, 0.34);
    border: 1px solid rgba(255, 255, 255, 0.78);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.92) inset,
      0 10px 24px rgba(20, 64, 98, 0.08),
      0 28px 54px rgba(20, 64, 98, 0.14);
    backdrop-filter: blur(18px) saturate(1.4);
    -webkit-backdrop-filter: blur(18px) saturate(1.4);
  }
  .harvest-msg { min-height: 1.2em; margin: -8px 0 14px; color: #8a2430; font-size: 13px; font-weight: 600; }
  .card {
    position: relative;
    background: linear-gradient(180deg, #ffffff 0%, #f6fbfe 100%);
    border: 1px solid rgba(255, 255, 255, 0.92); border-radius: 22px;
    padding: 18px 20px 16px; min-width: 120px; flex: 1;
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.95) inset, 0 18px 40px rgba(20, 64, 98, 0.08), 0 2px 6px rgba(20, 64, 98, 0.04);
  }
  .row > .card:not(.wl-stat-lavender):not(.wl-top-accent)::before, .panel::before, .phone::before {
    content: ""; position: absolute; left: 16px; right: 16px; top: 0; height: 1px;
    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.98), transparent);
  }
  .k {
    font-size: 10px; color: #6d8da3; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase;
  }
  .v {
    font-size: 30px; font-weight: 600; margin-top: 8px; color: #10283c;
    font-variant-numeric: tabular-nums; letter-spacing: -0.045em;
  }
  .ok { color: #0d7a34; }
  .bad { color: #a32020; }
  .warn { color: #8a5a00; }
  #collect-phones {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 18px;
    margin: 4px 0 18px;
    align-items: stretch;
  }
  /* 수집 폰 카드: 상단 LED 색과 같은 계열로 셸·화면·패널 통일 */
  #collect-phones .pick.collect-phone {
    padding: 9px 8px 11px;
    border-radius: 26px;
    border: 1px solid transparent;
    color: #143044;
  }
  #collect-phones .pick.collect-phone.tone-red {
    border-color: rgba(248, 113, 113, 0.38);
    background: linear-gradient(165deg, #fffcfc 0%, #fff7f7 58%, #feecec 100%);
    box-shadow: inset 0 0 0 1px #fff, 0 8px 18px rgba(185, 28, 28, 0.06);
  }
  #collect-phones .pick.collect-phone.tone-blue {
    border-color: rgba(56, 189, 248, 0.38);
    background: linear-gradient(165deg, #fcfeff 0%, #f6fbff 58%, #edf7fe 100%);
    box-shadow: inset 0 0 0 1px #fff, 0 8px 18px rgba(14, 116, 178, 0.06);
  }
  #collect-phones .pick.collect-phone.tone-yellow {
    border-color: rgba(251, 191, 36, 0.4);
    background: linear-gradient(165deg, #fffef8 0%, #fffbeb 58%, #fef9e7 100%);
    box-shadow: inset 0 0 0 1px #fff, 0 8px 18px rgba(161, 98, 7, 0.06);
  }
  #collect-phones .pick.collect-phone.tone-green {
    border-color: rgba(74, 222, 128, 0.38);
    background: linear-gradient(165deg, #fbfffc 0%, #f4fdf7 58%, #ecfdf3 100%);
    box-shadow: inset 0 0 0 1px #fff, 0 8px 18px rgba(21, 128, 61, 0.06);
  }
  #collect-phones .pick.collect-phone.tone-red.on { box-shadow: inset 0 0 0 1px #fff, 0 0 0 2px rgba(239, 68, 68, 0.55), 0 8px 20px rgba(185, 28, 28, 0.16); }
  #collect-phones .pick.collect-phone.tone-blue.on { box-shadow: inset 0 0 0 1px #fff, 0 0 0 2px rgba(56, 189, 248, 0.55), 0 8px 20px rgba(14, 116, 178, 0.14); }
  #collect-phones .pick.collect-phone.tone-yellow.on { box-shadow: inset 0 0 0 1px #fff, 0 0 0 2px rgba(251, 191, 36, 0.55), 0 8px 20px rgba(161, 98, 7, 0.14); }
  #collect-phones .pick.collect-phone.tone-green.on { box-shadow: inset 0 0 0 1px #fff, 0 0 0 2px rgba(34, 197, 94, 0.55), 0 8px 20px rgba(21, 128, 61, 0.14); }
  #collect-phones .pick-earpiece {
    display: flex; align-items: center; justify-content: center;
    width: 100%; margin: 0 0 8px; padding: 0;
  }
  #collect-phones .pick-earpiece-line {
    display: flex; align-items: center; justify-content: space-between;
    gap: 10px; width: 100%; max-width: 196px; height: 18px; line-height: 0;
    margin: 0 auto;
  }
  #collect-phones .pick-ear-left {
    display: inline-flex; align-items: center; gap: 5px; flex: 0 0 auto; min-width: 28px;
  }
  #collect-phones .pick-login-wait {
    display: inline-flex; align-items: center; justify-content: center;
    width: 14px; height: 14px; flex: 0 0 14px; color: #64748b;
    opacity: 0.38; transition: opacity 0.25s ease, color 0.25s ease, filter 0.25s ease;
  }
  #collect-phones .pick-login-wait svg {
    width: 14px; height: 14px; display: block; pointer-events: none;
  }
  #collect-phones .pick-login-wait .login-avatar-fill {
    fill: currentColor; stroke: none;
  }
  #collect-phones .pick-login-wait.is-live {
    color: #2563eb;
    opacity: 1;
    filter: drop-shadow(0 0 4px rgba(37, 99, 235, 0.55));
    animation: login-wait-blink 1.15s ease-in-out infinite;
  }
  @keyframes login-wait-blink {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.28; transform: scale(0.92); }
  }
  #collect-phones .pick-cap-beacon {
    display: inline-flex; flex-direction: column; align-items: center; justify-content: flex-end;
    flex: 0 0 10px; height: 14px; opacity: 0.42; transition: opacity 0.25s ease;
  }
  #collect-phones .pick-cap-beacon-dot {
    width: 5px; height: 5px; border-radius: 50%; flex: 0 0 auto;
    background: #94a3b8;
    box-shadow: none;
  }
  #collect-phones .pick-cap-beacon-stem {
    width: 3px; height: 8px; margin-top: 1px; border-radius: 2px; flex: 0 0 auto;
    background: linear-gradient(180deg, #94a3b8 0%, #64748b 100%);
  }
  #collect-phones .pick-cap-beacon.is-live {
    opacity: 1;
  }
  #collect-phones .pick-cap-beacon.is-live .pick-cap-beacon-dot {
    background: #fbbf24;
    box-shadow: 0 0 7px 2px rgba(251, 191, 36, 0.95);
    animation: cap-beacon-dot 1.05s ease-in-out infinite;
  }
  #collect-phones .pick-cap-beacon.is-live .pick-cap-beacon-stem {
    background: linear-gradient(180deg, #fde68a 0%, #d97706 100%);
    box-shadow: 0 0 6px rgba(217, 119, 6, 0.35);
  }
  @keyframes cap-beacon-dot {
    0%, 100% { transform: scale(1); opacity: 1; }
    50% { transform: scale(1.12); opacity: 0.82; }
  }
  #collect-phones .pick-life-signal {
    position: relative; flex: 0 0 18px; width: 18px; height: 18px;
    display: block; overflow: visible;
  }
  #collect-phones .pick-life-signal .pls-core,
  #collect-phones .pick-life-signal .pls-halo,
  #collect-phones .pick-life-signal .pls-ring,
  #collect-phones .pick-life-signal .pls-orbit {
    position: absolute; left: 50%; top: 50%; pointer-events: none;
  }
  #collect-phones .pick-life-signal .pls-core {
    width: 7px; height: 7px; margin: -3.5px 0 0 -3.5px; border-radius: 50%;
    background: radial-gradient(circle at 35% 30%, #fff 0%, #64748b 42%, #334155 100%);
    box-shadow: inset 0 -1px 2px rgba(15, 23, 42, 0.35);
    z-index: 3;
  }
  #collect-phones .pick-life-signal .pls-halo {
    width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 50%;
    opacity: 0; z-index: 1;
  }
  #collect-phones .pick-life-signal .pls-ring {
    width: 8px; height: 8px; margin: -4px 0 0 -4px; border-radius: 50%;
    border: 1px solid transparent; opacity: 0; z-index: 2;
  }
  #collect-phones .pick-life-signal .pls-ring.r2 { animation-delay: 0.65s; }
  #collect-phones .pick-life-signal .pls-orbit {
    width: 16px; height: 16px; margin: -8px 0 0 -8px; opacity: 0; z-index: 2;
    animation: pls-orbit-spin 2.8s linear infinite;
  }
  #collect-phones .pick-life-signal .pls-orbit i {
    position: absolute; top: 0; left: 50%; width: 3px; height: 3px; margin-left: -1.5px;
    border-radius: 50%; background: #fff; box-shadow: 0 0 6px rgba(255, 255, 255, 0.95);
  }
  #collect-phones .pick-life-signal.is-dormant .pls-core {
    background: radial-gradient(circle at 35% 30%, #e2e8f0 0%, #64748b 55%, #475569 100%);
    opacity: 0.72;
  }
  #collect-phones .pick-life-signal.is-ops .pls-core {
    background: radial-gradient(circle at 35% 28%, #fff 0%, #7dd3fc 40%, #0284c7 100%);
    box-shadow: 0 0 8px rgba(56, 189, 248, 0.75);
    animation: pls-ops-breathe 2.2s ease-in-out infinite;
  }
  #collect-phones .pick-life-signal.is-ops .pls-halo {
    background: radial-gradient(circle, rgba(125, 211, 252, 0.55) 0%, transparent 70%);
    opacity: 0.55;
    animation: pls-halo-breathe 2.2s ease-in-out infinite;
  }
  #collect-phones .pick-life-signal.is-live .pls-core {
    background: radial-gradient(circle at 32% 28%, #fff 0%, #6ee7b7 38%, #059669 72%, #047857 100%);
    box-shadow: 0 0 10px rgba(16, 185, 129, 0.9), inset 0 1px 1px rgba(255, 255, 255, 0.65);
    animation: pls-live-core 1.85s ease-in-out infinite;
  }
  #collect-phones .pick-life-signal.is-live .pls-halo {
    background: radial-gradient(circle, rgba(52, 211, 153, 0.65) 0%, rgba(16, 185, 129, 0.15) 55%, transparent 72%);
    opacity: 0.85;
    animation: pls-halo-breathe 1.85s ease-in-out infinite;
  }
  #collect-phones .pick-life-signal.is-live .pls-ring {
    border-color: rgba(110, 231, 183, 0.75);
    animation: pls-ring-bloom 2.1s ease-out infinite;
  }
  #collect-phones .pick-life-signal.is-live .pls-orbit { opacity: 1; }
  #collect-phones .pick-life-signal.is-live .pls-orbit i {
    background: #ecfdf5;
    box-shadow: 0 0 8px rgba(110, 231, 183, 1);
  }
  #collect-phones .pick-life-signal.is-wait .pls-core {
    background: radial-gradient(circle at 35% 28%, #fff 0%, #fde68a 42%, #d97706 100%);
    box-shadow: 0 0 9px rgba(251, 191, 36, 0.85);
    animation: pls-wait-pulse 1.45s ease-in-out infinite;
  }
  #collect-phones .pick-life-signal.is-wait .pls-ring {
    border-color: rgba(251, 191, 36, 0.65);
    animation: pls-ring-bloom 2.4s ease-out infinite;
  }
  #collect-phones .pick-life-signal.is-fault .pls-core {
    background: radial-gradient(circle at 35% 28%, #fecaca 0%, #f87171 45%, #b91c1c 100%);
    box-shadow: 0 0 8px rgba(239, 68, 68, 0.75);
    animation: pls-fault-flicker 1.1s ease-in-out infinite;
  }
  #collect-phones .pick-life-signal.is-captcha .pls-core {
    background: radial-gradient(circle at 35% 28%, #fff 0%, #fcd34d 40%, #d97706 100%);
    box-shadow: 0 0 11px rgba(245, 158, 11, 0.95);
    animation: pls-wait-pulse 0.95s ease-in-out infinite;
  }
  #collect-phones .pick-life-signal.is-captcha .pls-ring {
    border-color: rgba(251, 191, 36, 0.85);
    animation: pls-ring-bloom 1.05s ease-out infinite;
  }
  #collect-phones .pick-life-signal.is-captcha .pls-halo {
    background: radial-gradient(circle, rgba(251, 191, 36, 0.55) 0%, transparent 68%);
    opacity: 0.9;
    animation: pls-halo-breathe 1.05s ease-in-out infinite;
  }
  @keyframes pls-live-core {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.08); }
  }
  @keyframes pls-halo-breathe {
    0%, 100% { transform: scale(0.92); opacity: 0.55; }
    50% { transform: scale(1.08); opacity: 0.95; }
  }
  @keyframes pls-ring-bloom {
    0% { transform: scale(0.55); opacity: 0.85; }
    100% { transform: scale(2.35); opacity: 0; }
  }
  @keyframes pls-orbit-spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
  }
  @keyframes pls-ops-breathe {
    0%, 100% { transform: scale(1); opacity: 0.92; }
    50% { transform: scale(1.06); opacity: 1; }
  }
  @keyframes pls-wait-pulse {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.1); }
  }
  @keyframes pls-fault-flicker {
    0%, 100% { opacity: 1; }
    45% { opacity: 0.55; }
    55% { opacity: 0.85; }
  }
  #collect-phones .pick-earpiece-line .pick-notch {
    width: 52px; height: 4px; border-radius: 999px;
    background: #9ec4dc; margin: 0; flex: 0 0 auto;
    display: block;
  }
  #collect-phones .pick.collect-phone.captcha-wait {
    box-shadow: inset 0 0 0 1px #fff, 0 0 0 1px rgba(245, 158, 11, 0.45), 0 10px 22px rgba(180, 83, 9, 0.12);
  }
  #collect-phones .pick.collect-phone.login-wait {
    box-shadow: inset 0 0 0 1px #fff, 0 0 0 1px rgba(37, 99, 235, 0.35), 0 10px 22px rgba(29, 78, 216, 0.1);
  }
  #collect-phones .pick.collect-phone.captcha-wait.login-wait {
    box-shadow: inset 0 0 0 1px #fff, 0 0 0 1px rgba(245, 158, 11, 0.4), 0 10px 22px rgba(120, 53, 15, 0.12);
  }
  #collect-phones .pick.collect-phone.is-slot-empty {
    cursor: default;
    opacity: 0.72;
    border-style: dashed;
    border-color: rgba(100, 116, 139, 0.35);
    background: linear-gradient(165deg, #fafcff 0%, #f1f5f9 100%);
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.9);
  }
  #collect-phones .pick.collect-phone.is-slot-empty .pick-name { color: #64748b; }
  #collect-phones .pick.collect-phone.is-slot-empty .collect-body .mini { opacity: 0.55; }
  #collect-phones .pick { width: auto; min-width: 0; min-height: 340px; height: auto; align-self: stretch; }
  #collect-phones .pick.collect-phone .pick-screen.collect-body {
    overflow: visible; gap: 4px; padding: 9px 9px 7px;
    display: flex; flex-direction: column; min-height: 0;
    border-radius: 20px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.45);
  }
  #collect-phones .pick.collect-phone.tone-red .pick-screen.collect-body {
    background: linear-gradient(165deg, #ffffff 0%, #fff8f8 100%);
    border: 1px solid rgba(248, 113, 113, 0.32);
  }
  #collect-phones .pick.collect-phone.tone-blue .pick-screen.collect-body {
    background: linear-gradient(165deg, #ffffff 0%, #f8fcff 100%);
    border: 1px solid rgba(56, 189, 248, 0.32);
  }
  #collect-phones .pick.collect-phone.tone-yellow .pick-screen.collect-body {
    background: linear-gradient(165deg, #ffffff 0%, #fffdf5 100%);
    border: 1px solid rgba(251, 191, 36, 0.34);
  }
  #collect-phones .pick.collect-phone.tone-green .pick-screen.collect-body {
    background: linear-gradient(165deg, #ffffff 0%, #f7fdf9 100%);
    border: 1px solid rgba(74, 222, 128, 0.32);
  }
  #collect-phones .pick.collect-phone.tone-red .pick-home { background: rgba(248, 113, 113, 0.55); }
  #collect-phones .pick.collect-phone.tone-blue .pick-home { background: rgba(56, 189, 248, 0.55); }
  #collect-phones .pick.collect-phone.tone-yellow .pick-home { background: rgba(251, 191, 36, 0.55); }
  #collect-phones .pick.collect-phone.tone-green .pick-home { background: rgba(74, 222, 128, 0.55); }
  #collect-phones .pick.collect-phone.tone-red .pick-earpiece-line .pick-notch { background: rgba(248, 113, 113, 0.45); }
  #collect-phones .pick.collect-phone.tone-blue .pick-earpiece-line .pick-notch { background: rgba(125, 211, 252, 0.55); }
  #collect-phones .pick.collect-phone.tone-yellow .pick-earpiece-line .pick-notch { background: rgba(250, 204, 21, 0.5); }
  #collect-phones .pick.collect-phone.tone-green .pick-earpiece-line .pick-notch { background: rgba(74, 222, 128, 0.48); }
  #collect-phones .pick-title-row {
    display: flex; align-items: center; gap: 6px; min-width: 0;
  }
  #collect-phones .pick-selected-mark {
    display: none;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    flex-shrink: 0;
  }
  #collect-phones .pick.collect-phone.on .pick-selected-mark {
    display: inline-flex;
  }
  #collect-phones .pick-selected-mark svg {
    width: 18px;
    height: 18px;
    display: block;
  }
  #collect-phones .adb-icon {
    display: inline-flex; align-items: center; justify-content: center;
    width: 18px; height: 18px; flex-shrink: 0;
  }
  #collect-phones .adb-icon svg { width: 18px; height: 18px; display: block; }
  #collect-phones .pick-title-main {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  #collect-phones .pick-screen .pick-name {
    font-size: 17px; margin: 0; display: inline;
    color: #10283c;
  }
  #collect-phones .pick-account {
    display: inline;
    font-size: 10px;
    font-weight: 650;
    letter-spacing: 0.02em;
    color: #4d6d84;
    font-variant-numeric: tabular-nums;
  }
  #collect-phones .pick-session-count {
    flex: 0 0 auto;
    margin-left: auto;
    font-size: 12px;
    font-weight: 650;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.02em;
    color: #4d6d84;
    white-space: nowrap;
  }
  #collect-phones .collect-body .meta { margin: 0 0 6px; font-size: 10px; line-height: 1.4; color: #4d6d84; }
  #collect-phones .collect-body .mini {
    display: grid;
    grid-template-columns: minmax(0, 3fr) minmax(0, 2fr) minmax(0, 2fr) minmax(0, 2fr);
    gap: 4px; margin: 0 0 4px; align-items: stretch;
  }
  #collect-phones .collect-body .mini .card {
    min-width: 0; padding: 6px 7px; border-radius: 12px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.55);
  }
  #collect-phones .collect-body .mini .card.status-col .v { font-size: 13px; line-height: 1.2; word-break: keep-all; }
  #collect-phones .tone-red .collect-body .mini .card { background: linear-gradient(180deg, #fff 0%, #fff5f5 100%); border: 1px solid rgba(248, 113, 113, 0.22); }
  #collect-phones .tone-blue .collect-body .mini .card { background: linear-gradient(180deg, #fff 0%, #f5faff 100%); border: 1px solid rgba(125, 211, 252, 0.28); }
  #collect-phones .tone-yellow .collect-body .mini .card { background: linear-gradient(180deg, #fff 0%, #fffbeb 100%); border: 1px solid rgba(250, 204, 21, 0.28); }
  #collect-phones .tone-green .collect-body .mini .card { background: linear-gradient(180deg, #fff 0%, #f0fdf4 100%); border: 1px solid rgba(74, 222, 128, 0.26); }
  #collect-phones .collect-body .mini .k { font-size: 8px; letter-spacing: 0.06em; color: #4d6d84; }
  #collect-phones .collect-body .mini .v { font-size: 15px; margin-top: 2px; }
  #collect-phones .collect-body .note { margin: 4px 0 2px; font-size: 10px; line-height: 1.35; color: #23475f; }
  #collect-phones .collect-body pre.collect-tail {
    flex: 1 1 auto; min-height: 132px; max-height: 196px; overflow: auto; margin: 4px 0 0; padding: 7px 8px;
    font-size: 9px; line-height: 1.4; border-radius: 10px; white-space: pre-wrap;
  }
  #collect-phones .collect-body pre.collect-tail.empty {
    display: none; margin: 0; padding: 0; border: 0; min-height: 0; max-height: 0; flex: 0 0 auto;
  }
  #collect-phones .tone-red .collect-body pre.collect-tail { background: rgba(254, 226, 226, 0.35); border: 1px solid rgba(252, 165, 165, 0.35); color: #7f1d1d; }
  #collect-phones .tone-blue .collect-body pre.collect-tail { background: rgba(224, 242, 254, 0.4); border: 1px solid rgba(186, 230, 253, 0.4); color: #0c4a6e; }
  #collect-phones .tone-yellow .collect-body pre.collect-tail { background: rgba(254, 249, 195, 0.4); border: 1px solid rgba(253, 224, 71, 0.4); color: #713f12; }
  #collect-phones .tone-green .collect-body pre.collect-tail { background: rgba(220, 252, 231, 0.4); border: 1px solid rgba(134, 239, 172, 0.4); color: #14532d; }
  .list-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .list-new {
    flex: 0 0 auto; width: 14px; height: 14px; border-radius: 50%;
    background: #3ec0ff;
    box-shadow: 0 0 10px 3px rgba(62, 192, 255, 0.85);
    animation: ledpulse 1.4s ease-out infinite;
  }
  .list-files { padding: 6px 12px 14px; max-height: 460px; overflow: auto; }
  .list-row {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 8px; border-bottom: 1px solid #eef4f8; cursor: pointer;
  }
  .list-row:last-child { border-bottom: 0; }
  .list-row.fresh { background: #c8efff; border-radius: 12px; }
  .list-row input { accent-color: #12344c; }
  .list-name { flex: 1 1 auto; min-width: 0; font-size: 13px; color: #12344c; word-break: break-all; }
  .list-count {
    flex: 0 0 auto; margin-left: 12px; font-size: 13px; font-weight: 650;
    color: #12344c; font-variant-numeric: tabular-nums;
  }
  .list-empty { color: #7f9aab; padding: 16px 8px; font-size: 13px; }
  .phone-head {
    display: flex; align-items: center; justify-content: space-between;
    gap: 10px; margin: 0 0 6px; padding-right: 18px;
  }
  .harvest-btn {
    flex: 0 0 auto; appearance: none; border: 0;
    background: #12344c; color: #f4f9fc; border-radius: 999px;
    padding: 8px 14px; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer;
    box-shadow: 0 8px 18px rgba(18, 52, 76, 0.16);
  }
  .harvest-btn.stop { background: #8a2430; }
  .harvest-btn:disabled { background: #9bb4c6; cursor: default; box-shadow: none; }
  .harvest-led { display: none; }
  .phone.live .harvest-led {
    display: block;
    position: absolute; top: 16px; right: 16px; z-index: 2;
    width: 6px; height: 6px; border-radius: 50%;
    background: #3ec0ff;
    box-shadow: 0 0 8px 2px rgba(62, 192, 255, 0.9);
  }
  .phone.live .harvest-led::after {
    content: "";
    position: absolute; left: 50%; top: 50%;
    width: 6px; height: 6px; margin: -3px 0 0 -3px;
    border-radius: 50%;
    background: rgba(62, 192, 255, 0.55);
    animation: ledpulse 1.4s ease-out infinite;
    pointer-events: none;
  }
  .pick-row { display: flex; gap: 18px; flex-wrap: wrap; margin: 4px 0 18px; align-items: flex-start; }
  .pick {
    position: relative; width: 186px; min-height: 300px; text-align: left; cursor: pointer;
    border-radius: 34px; border: 2px solid rgba(126, 176, 214, 0.9);
    background: linear-gradient(180deg, #eef6fc 0%, #d4e8f6 100%);
    box-shadow: inset 0 0 0 3px #f7fbfe, 0 16px 32px rgba(20, 64, 98, 0.12);
    padding: 12px 11px 14px; color: #143044;
    display: flex; flex-direction: column; font: inherit;
  }
  .pick.on {
    border-color: #1d6fa8;
    box-shadow: inset 0 0 0 3px #f7fbfe, 0 0 0 2px rgba(62, 192, 255, 0.55), 0 16px 32px rgba(20, 64, 98, 0.14);
  }
  .pick.collecting { border-color: rgba(29, 122, 168, 0.72); }
  .pick.waiting { border-color: rgba(180, 130, 0, 0.55); }
  .pick.region { border-color: #1d6fa8; }
  .pick.locked, .pick:disabled { cursor: default; opacity: 1; }
  .pick-notch {
    width: 58px; height: 7px; border-radius: 999px; background: #9ec4dc; margin: 2px auto 10px;
  }
  .pick-led {
    position: absolute; top: 16px; right: 30px; z-index: 2;
    width: 6px; height: 6px; border-radius: 50%;
    background: #ff2d2d;
    box-shadow: 0 0 8px 2px rgba(255, 45, 45, 0.9);
  }
  .pick-led::after {
    content: "";
    position: absolute; left: 50%; top: 50%;
    width: 6px; height: 6px; margin: -3px 0 0 -3px;
    border-radius: 50%;
    background: rgba(255, 45, 45, 0.55);
    animation: ledpulse 1.4s ease-out infinite;
    pointer-events: none;
  }
  .pick-led.blue {
    background: #3ec0ff;
    box-shadow: 0 0 8px 2px rgba(62, 192, 255, 0.9);
  }
  .pick-led.blue::after { content: none; }
  .pick-led.green {
    background: #2fdb62;
    box-shadow: 0 0 8px 2px rgba(47, 219, 98, 0.9);
  }
  .pick-led.green::after {
    content: "";
    background: rgba(47, 219, 98, 0.55);
    animation: ledpulse 1.4s ease-out infinite;
  }
  .pick-led.yellow {
    background: #ffd23a;
    box-shadow: 0 0 8px 2px rgba(255, 210, 58, 0.95);
  }
  .pick-led.yellow::after { content: none; animation: none; }
  .pick-led.idle {
    background: #1a4d6e;
    box-shadow: none;
  }
  .pick-led.idle::after { content: none; animation: none; }
  @keyframes ledpulse {
    0% { transform: scale(1); opacity: 0.55; }
    100% { transform: scale(3.2); opacity: 0; }
  }
  .pick-screen {
    flex: 1; border-radius: 20px;
    background: linear-gradient(180deg, #ffffff 0%, #f4f9fc 100%);
    border: 1px solid rgba(126, 176, 214, 0.4);
    padding: 12px 12px 10px; display: flex; flex-direction: column; gap: 6px;
  }
  .pick-home {
    width: 46px; height: 4px; border-radius: 999px;
    background: rgba(29, 111, 168, 0.28); margin: 10px auto 0;
  }
  .pick-name {
    font-family: "Palatino Linotype", Palatino, "Times New Roman", serif;
    font-size: 22px; font-weight: 500; letter-spacing: -0.02em; line-height: 1.1; color: #10283c;
  }
  .pick-st {
    align-self: flex-start; margin-top: 2px; padding: 3px 8px; border-radius: 999px;
    border: 1px solid rgba(29, 111, 168, 0.22);
    background: rgba(62, 192, 255, 0.14); font-size: 11px; font-weight: 600; color: #1d5f8a;
  }
  .pick.collecting .pick-st { color: #0d7a34; border-color: rgba(13, 122, 52, 0.28); background: rgba(13, 122, 52, 0.08); }
  .pick-n {
    margin-top: auto; font-size: 28px; font-weight: 500; letter-spacing: -0.04em;
    color: #10283c; font-variant-numeric: tabular-nums;
    font-family: "Palatino Linotype", Palatino, "Times New Roman", serif;
  }
  .pick-bar {
    margin-top: auto; height: 5px; border-radius: 999px; overflow: hidden;
    background: rgba(29, 111, 168, 0.12);
  }
  .pick-bar > span {
    display: block; height: 100%; border-radius: inherit;
    background: linear-gradient(90deg, #8fd4f4, #1d6fa8);
  }
  .pick-pct {
    font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
    color: #1d5f8a; font-variant-numeric: tabular-nums;
  }
  #panel-sessions .sess-stats {
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px; width: 100%; margin: 8px 0 20px;
  }
  #panel-sessions .sess-stats .card {
    background: rgba(255, 255, 255, 0.38);
    border: 1px solid rgba(255, 255, 255, 0.72);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.85) inset,
      0 18px 40px rgba(20, 64, 98, 0.1);
    backdrop-filter: blur(18px) saturate(1.35);
    -webkit-backdrop-filter: blur(18px) saturate(1.35);
  }
  @media (max-width: 720px) {
    #panel-sessions .sess-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  .collect-actions {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 0 0 16px;
    flex-wrap: wrap;
    padding: 14px 16px;
    border-radius: 20px;
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.9) 0%, rgba(244, 250, 254, 0.94) 100%);
    border: 1px solid rgba(47, 143, 191, 0.24);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.95) inset,
      0 12px 28px rgba(20, 64, 98, 0.06);
  }
  .collect-actions-tail {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    margin-left: auto;
    flex-shrink: 0;
  }
  .collect-actions .listmode-seg {
    flex-shrink: 0;
  }
  .collect-actions .check {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin-left: 0;
    padding: 8px 12px;
    border-radius: 12px;
    color: #23475f;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: -0.01em;
    cursor: pointer;
    background: rgba(255, 255, 255, 0.55);
    border: 1px solid rgba(18, 72, 112, 0.1);
  }
  .collect-actions .check input {
    width: 15px;
    height: 15px;
    accent-color: #2f8fbf;
    cursor: pointer;
  }
  .collect-btn {
    appearance: none;
    border: 1px solid transparent;
    border-radius: 14px;
    padding: 10px 18px;
    font: inherit;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: -0.02em;
    line-height: 1.2;
    cursor: pointer;
    white-space: nowrap;
    transition: transform 0.14s ease, box-shadow 0.14s ease, filter 0.14s ease, border-color 0.14s ease;
  }
  .collect-btn:hover:not(:disabled) {
    filter: brightness(1.04);
    transform: translateY(-1px);
  }
  .collect-btn:active:not(:disabled) {
    transform: translateY(0);
    filter: brightness(0.98);
  }
  .collect-btn:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px rgba(62, 192, 255, 0.35);
  }
  .collect-btn-primary {
    color: #f4fbff;
    background: linear-gradient(180deg, #1e5678 0%, #12344c 54%, #0e2838 100%);
    border-color: rgba(62, 192, 255, 0.28);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.16) inset,
      0 8px 22px rgba(18, 52, 76, 0.2);
  }
  .collect-btn-primary:hover:not(:disabled) {
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.18) inset,
      0 10px 26px rgba(18, 52, 76, 0.24);
  }
  .collect-btn-danger {
    color: #fff9f9;
    background: linear-gradient(180deg, #c43a4a 0%, #8a2430 52%, #6e1c25 100%);
    border-color: rgba(255, 180, 180, 0.22);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.14) inset,
      0 8px 22px rgba(138, 36, 48, 0.22);
  }
  .collect-btn-danger:hover:not(:disabled) {
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.16) inset,
      0 10px 26px rgba(138, 36, 48, 0.28);
  }
  .collect-btn-soft {
    color: #1a4560;
    background: linear-gradient(180deg, #ffffff 0%, #edf5fb 100%);
    border-color: rgba(47, 143, 191, 0.34);
    box-shadow:
      0 1px 0 rgba(255, 255, 255, 0.98) inset,
      0 4px 14px rgba(20, 64, 98, 0.08);
  }
  .collect-btn-soft:hover:not(:disabled) {
    border-color: rgba(47, 143, 191, 0.48);
    background: linear-gradient(180deg, #ffffff 0%, #e6f2fa 100%);
  }
  .collect-btn:disabled {
    cursor: default;
    transform: none;
    filter: none;
    color: #8aa4b6;
    background: linear-gradient(180deg, #eef3f7 0%, #dfe9f1 100%);
    border-color: rgba(18, 72, 112, 0.1);
    box-shadow: none;
  }
  #collect-phones .collect-body .pick-bar { margin: 6px 0 2px; }
  #collect-phones .collect-body .pick-pct { margin: 0 0 4px; text-align: center; }
  .phone {
    position: relative; flex: 1 1 auto; min-width: 0; cursor: pointer;
    background: linear-gradient(180deg, #ffffff 0%, #f7fbfe 100%);
    border: 1px solid rgba(255, 255, 255, 0.92); border-radius: 24px; padding: 18px 18px 16px;
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.95) inset, 0 18px 40px rgba(20, 64, 98, 0.08);
  }
  .phone.picked {
    border-color: #12344c;
    box-shadow: 0 0 0 2px #12344c, 0 18px 40px rgba(20, 64, 98, 0.12);
  }
  .phone h2 { font-size: 17px; margin: 0; }
  .phone pre { max-height: 220px; overflow: auto; margin: 8px 0 0; }
  .meta { color: #4d6d84; font-size: 12px; margin: 0 0 10px; line-height: 1.45; }
  .mini { display: flex; gap: 8px; flex-wrap: wrap; }
  .mini .card {
    min-width: 72px; padding: 10px 12px;
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid rgba(198, 220, 234, 0.8); border-radius: 16px;
    box-shadow: none;
  }
  .mini .k { letter-spacing: 0.04em; }
  .mini .v { font-size: 20px; }
  .note { margin: 12px 0 6px; font-size: 13px; color: #23475f; }
  .panel {
    position: relative;
    background: linear-gradient(180deg, #ffffff 0%, #f8fcfe 100%);
    border: 1px solid rgba(255, 255, 255, 0.9); border-radius: 24px;
    overflow: hidden; margin: 0 0 20px;
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.95) inset, 0 22px 48px rgba(20, 64, 98, 0.08);
  }
  .panel-head { padding: 18px 22px 14px; border-bottom: 1px solid rgba(206, 224, 236, 0.85); }
  .panel-scroll { overflow-x: auto; }
  table.sheet { width: 100%; border-collapse: separate; border-spacing: 0; background: #fff; margin: 0; }
  table.sheet th {
    background: #f6fafd; color: #6a879b; border: 0; border-bottom: 1px solid #e5eef5;
    font-size: 10px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
    padding: 14px 16px; text-align: left;
  }
  table.sheet td {
    border: 0; border-bottom: 1px solid #eef4f8; padding: 14px 16px;
    text-align: left; font-size: 13px; color: #1c3d54; background: #fff;
    vertical-align: top;
  }
  table.sheet tbody tr:nth-child(even) td { background: #f8fbfd; }
  table.sheet tbody tr:last-child td { border-bottom: 0; }
  table.sheet td .k { letter-spacing: 0; text-transform: none; font-weight: 500; font-size: 12px; }
  pre {
    background: #f4f9fc; border: 1px solid #e1eef6; border-radius: 16px;
    padding: 12px; overflow: auto; max-height: 160px; font-size: 12px; margin: 10px 0 0;
    color: #24475e; line-height: 1.5;
  }
  nav.tabs { display: flex; align-items: flex-end; gap: 4px; margin: 0 0 16px; padding: 0 36px 0 0; border-bottom: 1px solid rgba(18, 72, 112, 0.16); }
  nav.tabs button {
    appearance: none; margin: 0 0 -1px; position: relative;
    border: 1px solid transparent; border-bottom: 0; border-radius: 14px 14px 0 0;
    background: transparent; color: #4d6d84; padding: 8px 18px 7px;
    font: inherit; font-size: 14px; font-weight: 600; line-height: 1.2; cursor: pointer;
  }
  nav.tabs button:focus { outline: none; }
  nav.tabs button:focus-visible { outline: 2px solid #7eb6d6; outline-offset: 2px; }
  nav.tabs button.on {
    background: #e4f3fb; color: #10283c; font-weight: 600;
    border-color: rgba(18, 72, 112, 0.16); padding: 10px 20px 9px; z-index: 1;
    box-shadow: 0 -8px 18px rgba(22, 74, 112, 0.05);
  }
  .path { font-size: 13px; word-break: break-all; }
  .seg { display: inline-flex; border: 1px solid #c5dced; border-radius: 999px; overflow: hidden; background: #fff; box-shadow: 0 6px 16px rgba(22, 74, 112, 0.06); }
  .seg button {
    appearance: none; border: 0; background: transparent; color: #4d6d84;
    padding: 7px 16px; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer;
  }
  .seg button.on { background: #12344c; color: #f4f9fc; }
  button:focus { outline: none; }
  button:focus-visible { outline: 2px solid #7eb6d6; outline-offset: 2px; }
  #sever-upload-now {
    appearance: none; border: 1px solid #c5dced; background: #fff; color: #16344c;
    border-radius: 999px; padding: 7px 16px; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer;
    box-shadow: 0 6px 16px rgba(22, 74, 112, 0.06);
  }
  #sever-upload-now:disabled { color: #8aa4b6; cursor: default; box-shadow: none; }
  .sess-bar { display: flex; align-items: center; gap: 12px; margin: 0 0 6px; }
  .sess-bar h1 { margin: 0; }
  #sess-refresh, #sever-refresh {
    appearance: none; border: 1px solid #c5dced; background: #fff; color: #16344c;
    border-radius: 999px; padding: 7px 16px; font: inherit; font-size: 13px; font-weight: 600;
    cursor: pointer; box-shadow: 0 6px 16px rgba(22, 74, 112, 0.06);
  }
  #sess-refresh:hover, #sever-refresh:hover { border-color: #8ebcda; }
  #sess-refresh:disabled, #sever-refresh:disabled { color: #8aa4b6; cursor: default; }
  .region { margin: 4px 0 18px; }
  .region-head p { margin: 4px 0 0; color: #4d6d84; font-size: 13px; }
  .region-scroll { overflow-x: auto; }
  table.rounds { margin: 0; min-width: 760px; border-collapse: separate; border-spacing: 0; }
  table.rounds th {
    background: #f6fafd; color: #6a879b; border: 0; border-bottom: 1px solid #e5eef5;
    font-size: 10px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
    padding: 14px 16px;
  }
  table.rounds td { vertical-align: top; border: 0; border-bottom: 1px solid #eef4f8; padding: 14px 16px; background: #fff; }
  table.rounds tbody tr:nth-child(even) td { background: #f8fbfd; }
  table.rounds tbody tr:last-child td { border-bottom: 0; }
  .sess-col {
    overflow: visible;
  }
  .sess-item {
    display: grid; grid-template-columns: 10px minmax(0, 1.3fr) minmax(0, 1.1fr) auto;
    gap: 8px; align-items: center; padding: 8px 2px;
    border-bottom: 1px solid #eef4f8;
  }
  .sess-item.labels {
    color: #6a879b; font-size: 10px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
  }
  .sess-id { font-weight: 600; font-variant-numeric: tabular-nums; color: #12344c; }
  .sess-state { display: block; margin-top: 2px; font-weight: 500; font-size: 11px; letter-spacing: 0; text-transform: none; color: #4d6d84; }
  .sess-when { color: #4d6d84; font-size: 12px; font-variant-numeric: tabular-nums; }
  .sess-n { font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; color: #12344c; }
  .sess-led {
    position: relative; width: 6px; height: 6px; border-radius: 50%; background: transparent;
  }
  .sess-led.blue {
    background: #3ec0ff;
    box-shadow: 0 0 8px 2px rgba(62, 192, 255, 0.9);
  }
  .sess-led.live {
    background: #3ec0ff;
    box-shadow: 0 0 8px 2px rgba(62, 192, 255, 0.9);
  }
  .sess-led.live::after {
    content: "";
    position: absolute; left: 50%; top: 50%;
    width: 6px; height: 6px; margin: -3px 0 0 -3px;
    border-radius: 50%;
    background: rgba(62, 192, 255, 0.55);
    animation: ledpulse 1.4s ease-out infinite;
    pointer-events: none;
  }
  .sess-led.yellow {
    background: #ffd23a;
    box-shadow: 0 0 8px 2px rgba(255, 210, 58, 0.95);
  }
  #panel-sessions .region-scroll {
    display: block; overflow-x: auto; padding-bottom: 10px; container-type: inline-size;
    scrollbar-width: thin;
    scrollbar-color: #8ec4e0 rgba(255, 255, 255, 0.35);
  }
  #panel-sessions .region-scroll::-webkit-scrollbar {
    width: 10px; height: 10px;
  }
  #panel-sessions .region-scroll::-webkit-scrollbar-track {
    background: rgba(255, 255, 255, 0.42);
    border-radius: 999px;
    box-shadow: inset 0 0 0 1px rgba(158, 196, 220, 0.45);
  }
  #panel-sessions .region-scroll::-webkit-scrollbar-thumb {
    background: linear-gradient(180deg, #e7f5fb, #7eb8d6);
    border-radius: 999px;
    border: 2px solid rgba(255, 255, 255, 0.9);
    box-shadow: 0 1px 4px rgba(20, 64, 98, 0.16);
  }
  #sess-manage {
    display: grid; grid-auto-flow: column; align-items: start;
    grid-auto-columns: calc((100cqi - 124px) / 5);
    gap: 22px; width: max-content;
    padding-left: 18px;
  }
  #sess-manage .manage-col:nth-child(5) { margin-right: 18px; }
  #panel-sessions .manage-col {
    min-width: 0;
    border: 1px solid rgba(126, 176, 214, 0.62);
    border-top: 4px solid #3ec0ff;
    border-radius: 18px;
    background: rgba(255, 255, 255, 0.82);
    box-shadow: 0 12px 28px rgba(20, 64, 98, 0.08);
    padding: 14px 14px 10px;
  }
  #panel-sessions .manage-col:nth-child(5n+2) { border-top-color: #1d6fa8; background: rgba(244, 250, 253, 0.94); }
  #panel-sessions .manage-col:nth-child(5n+3) { border-top-color: #7eb0d6; }
  #panel-sessions .manage-col:nth-child(5n+4) { border-top-color: #2f8fbf; background: rgba(244, 250, 253, 0.94); }
  #panel-sessions .manage-col:nth-child(5n+5) { border-top-color: #9ec4dc; }
  #panel-sessions .manage-col h3 {
    margin: 0 0 8px; font-size: 16px; font-weight: 600; color: #10283c; letter-spacing: -0.02em;
  }
  #panel-sessions .manage-col.unset {
    color: #7f9aab; background: rgba(255, 255, 255, 0.42); border-style: dashed; border-top-style: solid;
  }
  #panel-sessions .manage-col.unset h3 { color: #7f9aab; }
  .phone-h { margin-right: 8px; }
  .phone-n {
    display: inline-flex; align-items: center; justify-content: center;
    min-width: 1.8em; padding: 2px 8px; border-radius: 999px;
    background: rgba(62, 192, 255, 0.18); color: #1d5f8a;
    font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums;
  }
  .sess-empty { color: #7f9aab; font-size: 13px; padding: 12px 2px; }
  table.rounds td.time {
    white-space: nowrap; font-variant-numeric: tabular-nums; font-weight: 600; color: #12344c;
  }
  table.rounds td.total {
    text-align: center; font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums;
    color: #12344c; width: 88px;
  }
  .pcell-top { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; }
  .n {
    display: inline-block; min-width: 1.4em; text-align: center;
    background: #e7f6ec; color: #0b6b2d; font-weight: 700; border-radius: 999px;
    padding: 1px 8px; font-size: 13px; font-variant-numeric: tabular-nums;
  }
  .ids { max-height: 88px; overflow: auto; font-size: 12px; color: #24506a; line-height: 1.5; font-variant-numeric: tabular-nums; }
  td.pcell.empty { color: #7f9aab; font-size: 13px; }
  .fresh { color: #0b6b2d; }
  .busy { color: #8a5a00; }
  details.uses { font-size: 12px; }
  details.uses summary { cursor: pointer; }
  @media (max-width: 1100px) {
    #panel-collect .row { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    #collect-phones { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  }
  @media (max-width: 980px) {
    #collect-phones { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  @media (max-width: 640px) {
    #collect-phones { grid-template-columns: 1fr; }
  }
  @media (max-width: 640px) { #panel-collect .row { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
</head>
<body>
<div id="link-live" class="link-live off" role="status" aria-label="Offline" title="Offline">
  <span class="link-live-glow" aria-hidden="true"></span>
  <span class="link-live-orbit" aria-hidden="true"></span>
  <span class="link-live-stream" aria-hidden="true"></span>
  <span class="link-live-ring r1" aria-hidden="true"></span>
  <span class="link-live-ring r2" aria-hidden="true"></span>
  <span class="link-live-core" aria-hidden="true"></span>
</div>
<nav class="tabs" role="tablist">
  <button type="button" role="tab" class="on" id="tab-collect" aria-selected="true" onclick="showTab('collect')">Collection</button>
  <button type="button" role="tab" id="tab-sessions" aria-selected="false" onclick="showTab('sessions')">Sessions</button>
  <button type="button" role="tab" id="tab-sever" aria-selected="false" onclick="showTab('sever')">Server</button>
</nav>
<div id="panel-collect">
<p class="harvest-msg" id="harvest-msg"></p>
<section class="workload" aria-label="Server list workload" data-ui="list-pool-v2">
  <div class="row">
    <div class="card wl-top-accent wl-stat-total">
      <div class="wl-stat-split-head wl-stat-total-head">
        <div class="wl-stat-split-main wl-stat-total-main">
          <div class="k wl-to-collect-head">
            <span>To collect</span>
            <button type="button" class="wl-to-collect-reset-btn" id="wl-to-collect-reset" title="Reset all collection state" aria-label="Reset all collection state">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 15.5-6.36"/><path d="M18 3v6h-6"/><path d="M21 12a9 9 0 0 1-15.5 6.36"/><path d="M6 21v-6H0"/></svg>
            </button>
          </div>
          <div class="v wl-pending-val" id="wl-pending">-</div>
        </div>
        <div class="wl-pct-badge" aria-label="Progress">
          <span class="k">Progress</span>
          <div class="wl-pct-ring-wrap is-idle" id="wl-pct-wrap">
            <div class="wl-pct-ring" id="wl-pct-ring">
              <div class="wl-pct-ring-glow" aria-hidden="true"></div>
            </div>
            <span class="wl-pct-val" id="wl-pct">-</span>
          </div>
        </div>
      </div>
      <div class="wl-pending-sub">List total <span class="wl-pending-val" id="wl-total">-</span></div>
    </div>
    <div class="card wl-top-accent wl-stat-done-miss">
      <div class="wl-stat-split-head wl-stat-done-head">
        <div class="wl-stat-split-main wl-stat-done-main">
          <div class="k">Done</div>
          <div class="v ok" id="wl-done">0</div>
        </div>
        <div class="wl-pct-badge wl-upload-badge" aria-label="Upload to server">
          <span class="k">To server</span>
            <div class="wl-mobil-wrap">
            <div class="wl-upload-beacon is-idle" id="wl-upload-beacon" role="img" aria-label="Waiting for upload bridge">
              <svg width="0" height="0" aria-hidden="true" focusable="false">
                <defs>
                  <linearGradient id="wl-upload-grad" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stop-color="#38bdf8"/>
                    <stop offset="55%" stop-color="#6366f1"/>
                    <stop offset="100%" stop-color="#a78bfa"/>
                  </linearGradient>
                </defs>
              </svg>
              <span class="wl-upload-aura" aria-hidden="true"></span>
              <svg class="wl-upload-arc" viewBox="0 0 78 54" aria-hidden="true">
                <path class="wl-upload-arc-path" d="M14 38 Q39 10 64 38"/>
              </svg>
              <span class="wl-upload-source" aria-hidden="true"></span>
              <span class="wl-upload-target" aria-hidden="true"><span class="t-core"></span><span class="t-ring"></span></span>
              <span class="wl-upload-orb" aria-hidden="true"></span>
              <span class="wl-upload-spark s1" aria-hidden="true"></span>
              <span class="wl-upload-spark s2" aria-hidden="true"></span>
            </div>
            <span class="wl-upload-sent-sub" id="wl-upload-sent" title="Unique product files uploaded to the member hub (registry)">0 uploaded</span>
          </div>
        </div>
      </div>
      <div class="wl-stat-done-foot">
        <div class="wl-miss-sub">
          <span><span id="wl-missed-label">Missed</span> <span class="bad" id="wl-missed">0</span></span>
          <button type="button" class="wl-miss-clear-btn" id="wl-missed-clear" title="Clear missed unsaved items" aria-label="Clear missed unsaved items" disabled>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
          </button>
          <button type="button" class="wl-miss-clear-btn wl-miss-recollect-btn" id="wl-missed-recollect" title="Re-collect missed into Unassigned" aria-label="Re-collect missed into Unassigned" disabled>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
          </button>
        </div>
      </div>
    </div>
    <div class="card wl-stat-lavender wl-top-accent">
      <div class="wl-stat-split-head wl-stat-unassigned-row">
        <div class="wl-stat-split-main wl-stat-unassigned-body">
          <div class="k" id="wl-unassigned-label">Unassigned</div>
          <div class="v wl-unassigned-val" id="wl-unassigned-top">-</div>
        </div>
        <div class="wl-pct-badge wl-auto-badge">
          <span class="k" id="wl-lav-badge-k" aria-hidden="true">Auto</span>
          <div class="wl-auto-icon-wrap" id="wl-auto-wrap">
            <button type="button" class="wl-auto-icon" id="wl-auto-distribute" title="Auto distribute" aria-label="Auto distribute">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M12 4v3.6"/>
                <path d="M9.4 7.6h5.2"/>
                <path d="M6.3 18.8c1.6-5.8 3.1-8.2 5.7-8.2"/>
                <path d="M12 10.6V18.8"/>
                <path d="M17.7 18.8c-1.6-5.8-3.1-8.2-5.7-8.2"/>
                <circle cx="6.3" cy="18.8" r="1.45" fill="currentColor" stroke="none" opacity="0.92"/>
                <circle cx="12" cy="18.8" r="1.45" fill="currentColor" stroke="none" opacity="0.92"/>
                <circle cx="17.7" cy="18.8" r="1.45" fill="currentColor" stroke="none" opacity="0.92"/>
              </svg>
            </button>
          </div>
          <div class="v wl-cyc-gone-val" id="wl-cyc-gone">0</div>
        </div>
      </div>
      <div class="wl-phone-assign-line" id="wl-phone-assign" aria-label="Per-phone queue left (display only)" title="Per-phone assigned queue"></div>
      <div class="wl-lav-foot" id="wl-lav-foot" aria-label="Collection cycle (mode2)">
        <span class="wl-lav-perm" title="영구 상품없음 — 이 목록상 / 전체 (재수집 안 함)"><span class="wl-lav-perm-k">Perm gone</span> <span class="wl-lav-perm-v" id="wl-cyc-perm">0/0</span></span>
        <span class="wl-cycle-acct ok" id="wl-cyc-acct" title="정산: 영구(목록상)+임시미저장+임시없음+작업+저장 = 서버 건수">OK</span>
      </div>
    </div>
    <div class="card wl-top-accent wl-stat-speed">
      <div class="wl-stat-split-head wl-stat-speed-head">
        <div class="wl-stat-split-main wl-stat-speed-main">
          <div class="k">Per min</div>
          <div class="v" id="wl-rate-min">-</div>
        </div>
        <div class="wl-pct-badge wl-elapsed-badge" aria-label="Elapsed">
          <span class="k">Elapsed</span>
          <div class="wl-mobil-wrap">
            <span class="wl-stat-side-val wl-elapsed-val idle" id="wl-elapsed">-</span>
          </div>
        </div>
      </div>
      <div class="wl-speed-sub" id="wl-rate-hour">Hourly -</div>
    </div>
    <div class="card wl-top-accent wl-stat-active">
      <div class="wl-stat-split-head wl-stat-active-head">
        <div class="wl-stat-split-main">
          <div class="k">Active</div>
          <div class="v wl-mobil-val idle" id="wl-mobil">-</div>
        </div>
        <div class="wl-active-phones" id="wl-active-phones" aria-label="Phone activity"></div>
      </div>
    </div>
    <div class="card wl-top-accent wl-stat-watch">
      <div class="k">List watch</div>
      <div class="wl-watch-count" id="wl-watch-total" aria-label="Server list total">-</div>
      <div class="wl-watch-visual" aria-hidden="false">
        <div class="wl-radar" id="wl-radar" aria-hidden="true">
          <div class="wl-radar-sweep"></div>
          <span class="wl-radar-blip b1"></span>
          <span class="wl-radar-blip b2"></span>
          <span class="wl-radar-hit" id="wl-radar-hit" aria-hidden="true">
            <span class="wl-radar-hit-core"></span>
            <span class="wl-radar-hit-ring r1"></span>
            <span class="wl-radar-hit-ring r2"></span>
            <span class="wl-radar-hit-ring r3"></span>
          </span>
        </div>
        <div class="wl-ingest-slot">
          <div class="wl-ingest is-idle" id="wl-ingest" aria-label="List ingest">
            <svg class="wl-ingest-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 4v10"/>
              <path d="M8.5 10.5 12 14l3.5-3.5"/>
              <path d="M5 18h14"/>
            </svg>
            <div class="wl-ingest-stream" aria-hidden="true">
              <div class="wl-ingest-col"><i></i><i></i><i></i></div>
              <div class="wl-ingest-col delay"><i></i><i></i><i></i></div>
              <div class="wl-ingest-col delay2"><i></i><i></i><i></i></div>
            </div>
            <span class="wl-ingest-mark" aria-hidden="true"></span>
          </div>
        </div>
      </div>
    </div>
  </div>
  <svg width="0" height="0" aria-hidden="true" focusable="false" style="position:absolute">
    <defs>
      <linearGradient id="wl-ap-fill-blue" x1="0%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stop-color="#fcfeff"/><stop offset="55%" stop-color="#eef8ff"/><stop offset="100%" stop-color="#dbeafe"/>
      </linearGradient>
      <linearGradient id="wl-ap-fill-green" x1="0%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stop-color="#fbfffc"/><stop offset="55%" stop-color="#ecfdf3"/><stop offset="100%" stop-color="#d1fae5"/>
      </linearGradient>
      <linearGradient id="wl-ap-fill-yellow" x1="0%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stop-color="#fffef8"/><stop offset="55%" stop-color="#fffbeb"/><stop offset="100%" stop-color="#fef3c7"/>
      </linearGradient>
      <linearGradient id="wl-ap-fill-red" x1="0%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stop-color="#fffcfc"/><stop offset="55%" stop-color="#fef2f2"/><stop offset="100%" stop-color="#fee2e2"/>
      </linearGradient>
    </defs>
  </svg>
  <div class="workload-picks-wrap" id="wl-picks">
    <div class="wl-picks-head">Per-phone queue</div>
    <div class="workload-picks-phones" id="wl-picks-phones"></div>
  </div>
</section>
<div class="collect-actions">
  <button type="button" class="collect-btn collect-btn-primary" id="collect-start" onclick="runCollectAction('start')" disabled>Start collection</button>
  <button type="button" class="collect-btn collect-btn-danger" id="collect-stop" onclick="runCollectAction('stop')" disabled>Stop collection</button>
  <button type="button" class="collect-btn collect-btn-soft" id="collect-region" onclick="runCollectRegion()" disabled>Change region</button>
  <button type="button" class="collect-btn collect-btn-soft" id="collect-session" onclick="runCollectSession()" disabled>Collect sessions</button>
  <button type="button" class="collect-btn collect-btn-danger" id="collect-session-stop" onclick="runCollectSessionStop()" disabled>Stop sessions</button>
  <button type="button" class="collect-btn collect-btn-soft" id="collect-adb-reconnect" onclick="runCollectAdbReconnect()" disabled>ADB reconnect</button>
  <button type="button" class="collect-btn collect-btn-soft" id="collect-reinstall-app" onclick="runCollectReinstallApp()" disabled>Reinstall app</button>
  <div class="collect-actions-tail">
    <div class="seg listmode-seg" role="group" aria-label="List mode" title="목록관리 방식 전환">
      <button type="button" id="listmode-1" onclick="setListMode('mode1')" title="폰별 할당 + 잔량관리자">목록관리1</button>
      <button type="button" id="listmode-2" class="on" onclick="setListMode('mode2')" title="전역 풀 · 연속 pull (기본)">목록관리2</button>
    </div>
    <label class="check"><input type="checkbox" id="collect-all" onchange="toggleCollectAll(this.checked)"> Select all</label>
  </div>
</div>
<div class="pick-row" id="collect-phones" aria-label="Phones"></div>
<section class="collect-ref" aria-label="Reference">
  <div class="collect-ref-head">
    <h2>Reference</h2>
  </div>
  <div class="collect-ref-body">
    <article class="collect-ref-item">
      <h3 class="collect-ref-title">Server upload</h3>
      <p class="collect-ref-v" id="upload-hint">-</p>
    </article>
    <article class="collect-ref-item">
      <h3 class="collect-ref-title">UI refresh</h3>
      <p class="collect-ref-v" id="sub">Watches the harvest on three phones. Refreshes every 2 seconds.</p>
    </article>
    <article class="collect-ref-item">
      <h3 class="collect-ref-title">Storage path</h3>
      <p class="collect-ref-v path" id="collect-path">-</p>
    </article>
    <article class="collect-ref-item">
      <h3 class="collect-ref-title">Collection method</h3>
      <p class="collect-ref-v" id="method">-</p>
    </article>
      <p class="collect-ref-v" id="sub">Watches the harvest across the phone fleet. Refreshes every 2 seconds.</p>
</section>
</div>
<div id="panel-sessions" hidden>
<div class="sess-bar">
  <h1>Sessions</h1>
  <button type="button" id="sess-refresh" onclick="refreshSessions()">Refresh</button>
  <span class="sub" id="sess-refreshed"></span>
</div>
<p class="sub">Session totals and per-phone account status. Harvest actions are on the Collection tab.</p>
<p class="sub path" id="sess-path">-</p>
<div class="row sess-stats">
  <div class="card"><div class="k">Total</div><div class="v" id="sess-total">0</div></div>
  <div class="card"><div class="k">Available</div><div class="v ok" id="sess-free">0</div></div>
  <div class="card"><div class="k">In use</div><div class="v warn" id="sess-busy">0</div></div>
  <div class="card"><div class="k">Fresh</div><div class="v ok" id="sess-fresh">0</div></div>
</div>
<section class="panel region">
  <div class="panel-head region-head">
    <h2>Per phone</h2>
    <p>Scroll sideways for more phones. Count is assigned sessions. Blue LED: Fresh, Active, or handover. Pulsing blue: Collecting. Yellow: stopped before 300 saves — see the status line under the account.</p>
  </div>
  <div class="panel-scroll region-scroll">
    <div id="sess-manage"></div>
  </div>
</section>
</div>
<div id="panel-sever" hidden>
<div class="sess-bar">
  <h1>Server</h1>
  <div class="seg" role="group" aria-label="Upload mode">
    <button type="button" id="mode-auto" onclick="setSeverMode('auto')">Auto</button>
    <button type="button" id="mode-manual" onclick="setSeverMode('manual')">Manual</button>
  </div>
  <button type="button" id="sever-upload-now" onclick="uploadNow()" disabled>Upload now</button>
  <button type="button" id="sever-refresh" onclick="refreshSever()">Refresh</button>
  <span class="sub" id="sever-refreshed"></span>
</div>
<p class="sub">Auto sends each new product file as it is saved. Manual keeps the file in Upload until Upload now.</p>
<p class="sub path" id="sever-path">Upload  -</p>
<p class="sub path" id="sever-down">Download  -</p>
<p class="sub" id="sever-note">-</p>
<div class="row">
  <div class="card"><div class="k">Vault</div><div class="v" id="sever-vault">-</div></div>
  <div class="card"><div class="k">Inbox</div><div class="v" id="sever-inbox">-</div></div>
  <div class="card"><div class="k">List</div><div class="v" id="sever-tasks">-</div></div>
  <div class="card"><div class="k">Assigned</div><div class="v" id="sever-assigned">-</div></div>
  <div class="card"><div class="k">Processed</div><div class="v ok" id="sever-list-done">0</div></div>
  <div class="card"><div class="k">List left</div><div class="v warn" id="sever-list-pending">0</div></div>
  <div class="card"><div class="k">Uploaded</div><div class="v ok" id="sever-sent">0</div></div>
  <div class="card"><div class="k">Local queue</div><div class="v warn" id="sever-pending">0</div></div>
</div>
<section class="panel">
  <div class="panel-head"><h2>Connection</h2></div>
  <div class="panel-scroll">
    <table class="sheet">
      <tbody id="sever-conn"></tbody>
    </table>
  </div>
</section>
<section class="panel">
  <div class="panel-head"><h2>Incoming list</h2></div>
  <div class="panel-scroll">
    <table class="sheet">
      <thead><tr><th>Shop</th><th>Item</th><th>Expires</th></tr></thead>
      <tbody id="sever-samples"></tbody>
    </table>
  </div>
</section>
<section class="panel">
  <div class="panel-head"><h2>Live upload</h2></div>
  <div class="panel-scroll">
    <table class="sheet">
      <thead><tr><th>Time</th><th>File</th><th>Result</th></tr></thead>
      <tbody id="sever-recent"></tbody>
    </table>
  </div>
</section>
</div>
<script>
function esc(s){
  return String(s == null ? "" : s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function fmtElapsed(sec){
  if (sec == null || sec < 0) return "-";
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return String(h).padStart(2,"0") + ":" + String(m).padStart(2,"0") + ":" + String(s).padStart(2,"0");
}
function phoneActivityOpsBusy(p){
  const m = p.activity_mode || "";
  return m === "region" || m === "session" || m === "adb" || m === "reinstall";
}
function phoneTailHtml(p){
  const lines = (p.tail || []).slice();
  if (!lines.length) return '<pre class="collect-tail empty"></pre>';
  return '<pre class="collect-tail">' + lines.map(esc).join("\\n") + "</pre>";
}
function collectCardStatus(p){
  if (p.activity_mode === "region") return "Changing region";
  if (p.activity_mode === "reinstall") return "Reinstalling";
  if (p.activity_mode === "adb") return "ADB reconnect";
  if (p.activity_mode === "session") return "Session harvest";
  if (!p.running) return "Ready";
  if (p.state === "대기") return "Waiting";
  return "Collecting";
}
function phoneCaptchaActive(p){
  if (p.captcha_waiting) return true;
  if (p.state === "캡차 대기") return true;
  const note = String(p.note || "");
  if (note.indexOf("캡차 대기") >= 0 || note.indexOf("PDP 418") >= 0) return true;
  const tail = p.tail || [];
  for (let i = 0; i < tail.length; i++) {
    const s = String(tail[i]);
    if (s.indexOf("캡차 대기") >= 0 || s.indexOf("PDP 418") >= 0 || s.indexOf("/verify/traffic") >= 0) return true;
  }
  return false;
}
function phoneLoginWaiting(p){
  if (p.login_waiting) return true;
  if (p.activity_mode === "session") return true;
  const note = String(p.note || "");
  if (note.indexOf("로그인 대기") >= 0 || note.indexOf("로그인 필요") >= 0) return true;
  const tail = p.tail || [];
  for (let i = 0; i < tail.length; i++) {
    const s = String(tail[i]);
    if (s.indexOf("로그인 대기") >= 0 || s.indexOf("로그인 필요") >= 0) return true;
  }
  return false;
}
function pickLoginWaitIcon(live){
  const cls = live ? " is-live" : "";
  const aria = live ? ' role="img" aria-label="Waiting for login"' : ' role="img" aria-label="Login indicator"';
  return '<span class="pick-login-wait' + cls + '"' + aria + '>'
    + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<circle cx="12" cy="12" r="9.25"/>'
    + '<circle class="login-avatar-fill" cx="12" cy="9.35" r="2.85"/>'
    + '<path class="login-avatar-fill" d="M6.35 18.1c.85-2.65 2.95-4.05 5.65-4.05s4.8 1.4 5.65 4.05"/>'
    + '</svg></span>';
}
function phoneHarvestLedKind(p){
  const opsBusy = phoneActivityOpsBusy(p);
  const harvestIdle = !p.running || opsBusy;
  const waiting = p.running && !opsBusy && p.state === "대기";
  if (harvestIdle) return " idle";
  if (!p.adb_connected) return " off";
  if (waiting) return " yellow";
  return " green";
}
function phoneActiveStateSig(p){
  return [
    p.shard,
    p.name,
    p.running ? 1 : 0,
    p.activity_mode || "",
    p.state || "",
    p.adb_connected ? 1 : 0,
    phoneCaptchaActive(p) ? 1 : 0,
    phoneLoginWaiting(p) ? 1 : 0
  ].join("|");
}
function phoneLifeState(p, ledKind, captchaOn){
  if (captchaOn) return "captcha";
  if (ledKind === " off") return "fault";
  if (ledKind === " yellow") return "wait";
  if (ledKind === " green") return "live";
  if (phoneActivityOpsBusy(p)) return "ops";
  return "dormant";
}
function expandPhoneSlots(phones, slots){
  function shardNum(v){ return parseInt(v, 10) || 0; }
  // 등록된 폰을 폰 이름순으로 먼저 놓고, 남은 shard 는 빈 슬롯으로 뒤에 붙인다.
  const registered = (phones || []).slice().sort(comparePhoneName);
  const used = {};
  registered.forEach(function(p){ used[shardNum(p.shard)] = true; });
  const out = registered.slice();
  for (let shard = 0; shard < slots; shard++) {
    if (used[shard]) continue;
    out.push({
      shard: shard,
      name: "",
      running: false,
      activity_mode: "idle",
      state: "",
      adb_connected: false,
      _slot: true
    });
  }
  return out;
}
function phoneCollectTone(p){
  const opsBusy = phoneActivityOpsBusy(p);
  const harvestIdle = !p.running || opsBusy;
  const ledKind = phoneHarvestLedKind(p);
  if (harvestIdle || opsBusy) return "tone-blue";
  if (ledKind === " off") return "tone-red";
  if (ledKind === " yellow") return "tone-yellow";
  return "tone-green";
}
function activePhoneMini(p, slotIndex){
  const captchaOn = phoneCaptchaActive(p);
  const loginOn = phoneLoginWaiting(p);
  const ledKind = phoneHarvestLedKind(p);
  const life = pickLifeSignal(p, ledKind, captchaOn);
  const tone = phoneCollectTone(p);
  const lifeState = phoneLifeState(p, ledKind, captchaOn);
  let extra = "";
  if (captchaOn) extra += " captcha-wait";
  if (loginOn) extra += " login-wait";
  if (p._slot) extra += " is-slot-empty";
  const label = p.name || ("Slot " + (Number(p.shard) + 1));
  return '<div class="wl-active-glyph ' + tone + extra + ' sig-' + lifeState + '" style="--slot-i:' + slotIndex + '" title="' + esc(label) + '">'
    + '<svg class="wl-ap-premium" viewBox="0 0 20 32" aria-hidden="true">'
    + '<rect class="wl-ap-body" x="1.5" y="1" width="17" height="30" rx="4.2"/>'
    + '<rect class="wl-ap-glass" x="3" y="8" width="14" height="18" rx="2.5"/>'
    + '<rect class="wl-ap-island" x="6.5" y="3.6" width="7" height="2.4" rx="1.2"/>'
    + '<rect class="wl-ap-home" x="7.2" y="27.2" width="5.6" height="1.6" rx="0.8"/>'
    + '</svg>'
    + '<span class="wl-ap-life">' + life + '</span>'
    + '</div>';
}
function paintActivePhones(phones, slots){
  const host = document.getElementById("wl-active-phones");
  if (!host) return;
  slots = slots || uiPhoneSlots || 10;
  const list = expandPhoneSlots(phones, slots);
  const sig = list.map(phoneActiveStateSig).join(";");
  if (host.dataset.sig === sig && host.children.length === list.length) return;
  host.dataset.sig = sig;
  host.innerHTML = list.map(function(p, i){ return activePhoneMini(p, i); }).join("");
}
function pickLifeSignal(p, ledKind, captchaOn){
  let state = "dormant";
  if (captchaOn) state = "captcha";
  else if (ledKind === " off") state = "fault";
  else if (ledKind === " yellow") state = "wait";
  else if (ledKind === " green") state = "live";
  else if (phoneActivityOpsBusy(p)) state = "ops";
  const labels = {
    live: "Phone collecting",
    wait: "Phone waiting",
    dormant: "Phone idle",
    fault: "ADB disconnected",
    captcha: "Captcha on screen",
    ops: "Phone task running"
  };
  return '<span class="pick-life-signal is-' + state + '" role="status" aria-label="' + labels[state] + '">'
    + '<span class="pls-halo" aria-hidden="true"></span>'
    + '<span class="pls-ring" aria-hidden="true"></span>'
    + '<span class="pls-ring r2" aria-hidden="true"></span>'
    + '<span class="pls-orbit" aria-hidden="true"><i></i></span>'
    + '<span class="pls-core" aria-hidden="true"></span>'
    + '</span>';
}
function collectCardStatusClass(p){
  if (phoneActivityOpsBusy(p)) return "warn";
  if (!p.running) return "warn";
  if (p.state === "대기") return "warn";
  return "ok";
}
function collectionMobilizedCount(phones, harvest){
  harvest = harvest || {};
  const list = phones || [];
  const total = list.length ? list.length : (harvest.phones_n == null ? 0 : harvest.phones_n);
  let active = 0;
  if (list.length) {
    list.forEach(function(p){
      if (p.running || phoneActivityOpsBusy(p)) active++;
    });
  } else if (harvest.running_n != null) {
    active = harvest.running_n;
  }
  return { active: active, total: total };
}
let wlListTotalSeen = null;
let wlUpdatedSeen = "";
let wlIngestUntil = 0;
let wlLastIngestKind = "";
function paintListWatch(wl){
  wl = wl || {};
  const listTotal = wl.list_total == null ? null : Number(wl.list_total);
  const updated = String(wl.updated_at || "");
  // LIST WATCH 는 서버가 나에게 보내온 목록수(수신 건수)를 감시한다.
  const serverCount = wl.list_server_count == null ? listTotal : Number(wl.list_server_count);
  const watchTotalEl = document.getElementById("wl-watch-total");
  if (watchTotalEl) watchTotalEl.textContent = (serverCount == null ? "-" : serverCount);
  const ingestEl = document.getElementById("wl-ingest");
  const radarEl = document.getElementById("wl-radar");
  const now = Date.now();
  if (listTotal != null && wlListTotalSeen != null && listTotal > wlListTotalSeen) {
    wlIngestUntil = now + 4500;
    wlLastIngestKind = "new";
  } else if (updated && wlUpdatedSeen && updated !== wlUpdatedSeen) {
    wlIngestUntil = now + 3200;
    wlLastIngestKind = "same";
  }
  const active = now < wlIngestUntil;
  if (ingestEl) {
    ingestEl.classList.remove("is-idle", "is-active", "is-new", "is-same");
    if (active && wlLastIngestKind) {
      ingestEl.classList.add("is-active", wlLastIngestKind === "new" ? "is-new" : "is-same");
      ingestEl.setAttribute(
        "aria-label",
        wlLastIngestKind === "new" ? "Ingesting new list" : "Refreshing existing list"
      );
    } else {
      ingestEl.classList.add("is-idle");
      ingestEl.setAttribute("aria-label", "Waiting for list ingest");
    }
  }
  if (radarEl) {
    radarEl.classList.remove("is-hit", "is-new", "is-same");
    if (active && wlLastIngestKind) {
      radarEl.classList.add("is-hit", wlLastIngestKind === "new" ? "is-new" : "is-same");
    }
  }
  if (listTotal != null) wlListTotalSeen = listTotal;
  if (updated) wlUpdatedSeen = updated;
}
function paintCycle(wl){
  wl = wl || {};
  const setN = function(id, v){
    const el = document.getElementById(id);
    if (el) el.textContent = (v == null ? 0 : v);
  };
  setN("wl-cyc-gone", wl.list_temp_gone);
  const perm = document.getElementById("wl-cyc-perm");
  if (perm){
    const onList = wl.list_perm_on_server == null ? 0 : wl.list_perm_on_server;
    const global = wl.list_perm_global == null ? 0 : wl.list_perm_global;
    perm.textContent = onList + "/" + global;
  }
  const acct = document.getElementById("wl-cyc-acct");
  if (acct){
    const ok = wl.list_accounting_ok !== false;
    acct.className = "wl-cycle-acct " + (ok ? "ok" : "bad");
    acct.textContent = ok ? "OK" : ("Δ" + (wl.list_accounting_delta == null ? "?" : wl.list_accounting_delta));
  }
}
let wlPctSeen = null;
let wlPctRiseUntil = 0;
function paintPctDisplay(pct, total){
  const wrap = document.getElementById("wl-pct-wrap");
  const pctEl = document.getElementById("wl-pct");
  const pctRing = document.getElementById("wl-pct-ring");
  const now = Date.now();
  let numPct = null;
  if (total !== "-") {
    numPct = Math.min(100, Math.max(0, pct));
    if (wlPctSeen != null && numPct > wlPctSeen + 0.04) {
      wlPctRiseUntil = now + 2400;
    }
  }
  if (pctEl) pctEl.textContent = total === "-" ? "-" : (pct + "%");
  if (pctRing) {
    pctRing.style.setProperty("--wl-pct", total === "-" ? "0" : String(numPct));
  }
  if (wrap) {
    wrap.classList.remove("is-rising", "is-idle", "is-complete", "is-empty");
    if (total === "-") wrap.classList.add("is-empty");
    else if (numPct >= 100) wrap.classList.add("is-complete");
    else if (now < wlPctRiseUntil) wrap.classList.add("is-rising");
    else wrap.classList.add("is-idle");
  }
  if (numPct != null) wlPctSeen = numPct;
  else if (total === "-") wlPctSeen = null;
}
function collectMethod(text){
  const raw = String(text || "");
  if (raw.indexOf("기본방식") >= 0) {
    return "Default method · fresh list · one extra pass for misses · no upstream proxy";
  }
  return raw || "-";
}
let wlUploadSentSeen = null;
let wlUploadSigSeen = "";
let wlUploadReady = false;
function paintUploadBeacon(pulse, roundDone){
  pulse = pulse || {};
  const el = document.getElementById("wl-upload-beacon");
  const sub = document.getElementById("wl-upload-sent");
  if (!el) return;
  const total = pulse.sent == null ? 0 : pulse.sent;
  let sessionSent = pulse.session_sent;
  if (sessionSent == null) sessionSent = total;
  sessionSent = Math.max(0, Number(sessionSent) || 0);
  if (roundDone != null && roundDone >= 0) {
    sessionSent = Math.max(sessionSent, Number(roundDone) || 0);
  }
  const sig = String(pulse.recent_sig || "");
  const bridge = !!pulse.bridge;
  el.classList.remove("is-bridge", "is-idle", "is-burst");
  el.classList.add(bridge ? "is-bridge" : "is-idle");
  el.setAttribute("aria-label", bridge ? "Upload bridge live" : "Upload bridge offline");
  if (sub) {
    sub.textContent = Number(sessionSent).toLocaleString() + " uploaded";
    sub.title = "Uploaded this collection round (registry since Fresh reset). Often matches Done. "
      + "Registry total " + Number(total).toLocaleString() + ". Bridge "
      + (bridge ? "live." : "offline.");
  }
  let burst = false;
  if (wlUploadReady) {
    if (sig && sig !== wlUploadSigSeen) burst = true;
    else if (wlUploadSentSeen != null && sessionSent > wlUploadSentSeen) burst = true;
  }
  wlUploadSentSeen = sessionSent;
  if (sig) wlUploadSigSeen = sig;
  wlUploadReady = true;
  if (burst) {
    void el.offsetWidth;
    el.classList.add("is-burst");
    window.setTimeout(function(){
      if (el) el.classList.remove("is-burst");
    }, 1300);
  }
}
function paintWorkload(wl, upload, phones, harvest, serverPulse){
  wl = wl || {};
  harvest = harvest || {};
  const total = wl.list_total == null ? "-" : wl.list_total;
  const done = wl.list_done == null ? 0 : wl.list_done;
  const queueLeft = wl.queue_pending == null ? null : Number(wl.queue_pending);
  const left = queueLeft != null && !Number.isNaN(queueLeft)
    ? queueLeft
    : (wl.list_pending == null ? "-" : wl.list_pending);
  const missed = wl.list_missed == null ? 0 : wl.list_missed;
  const mode2 = wl.list_mode === "mode2";
  const processed = wl.list_processed != null ? wl.list_processed : (done + missed);
  let pct = 0;
  if (total !== "-" && total > 0) {
    pct = Math.round(1000 * processed / total) / 10;
  } else if (wl.progress_pct != null) {
    pct = wl.progress_pct;
  }
  document.getElementById("wl-total").textContent = total;
  document.getElementById("wl-done").textContent = mode2
    ? (wl.list_saved_not_uploaded == null ? 0 : wl.list_saved_not_uploaded)
    : done;
  const missedLabel = document.getElementById("wl-missed-label");
  const missedEl = document.getElementById("wl-missed");
  if (mode2) {
    if (missedLabel) missedLabel.textContent = "Uploaded";
    if (missedEl) {
      missedEl.className = "ok";
      missedEl.textContent = wl.list_uploaded_on_list == null ? 0 : wl.list_uploaded_on_list;
      missedEl.title = "이번 서버 목록이 온 뒤 멤버 허브에 올린 총수";
    }
  } else {
    if (missedLabel) missedLabel.textContent = "Missed";
    if (missedEl) {
      missedEl.className = "bad";
      missedEl.textContent = missed;
      missedEl.title = "";
    }
  }
  const missedClearBtn = document.getElementById("wl-missed-clear");
  if (missedClearBtn) missedClearBtn.disabled = !(Number(missed) > 0);
  const missedRecollectBtn = document.getElementById("wl-missed-recollect");
  if (missedRecollectBtn) {
    const pendingN = wl.list_pending == null ? 0 : Number(wl.list_pending);
    missedRecollectBtn.disabled = !(Number(missed) > 0 || pendingN > 0);
  }
  const mobilEl = document.getElementById("wl-mobil");
  if (mobilEl) {
    const mob = collectionMobilizedCount(phones, harvest);
    const totalN = mob.total;
    mobilEl.textContent = totalN ? (mob.active + "/" + totalN) : "-";
    mobilEl.className = "v wl-mobil-val " + (mob.active > 0 ? "ok" : "idle");
  }
  document.getElementById("wl-pending").textContent = mode2
    ? (wl.list_work_primary == null ? 0 : wl.list_work_primary)
    : left;
  const toCollectResetBtn = document.getElementById("wl-to-collect-reset");
  if (toCollectResetBtn) {
    const running = harvest.running_n != null ? Number(harvest.running_n) : 0;
    toCollectResetBtn.disabled = running > 0;
  }
  const unTop = document.getElementById("wl-unassigned-top");
  if (unTop) unTop.textContent = mode2
    ? (wl.list_temp_missed == null ? 0 : wl.list_temp_missed)
    : (wl.unassigned == null ? 0 : wl.unassigned);
  const unLabel = document.getElementById("wl-unassigned-label");
  if (unLabel) unLabel.textContent = mode2 ? "Temp missed" : "Unassigned";
  const lavBadgeK = document.getElementById("wl-lav-badge-k");
  if (lavBadgeK) lavBadgeK.textContent = mode2 ? "Temp gone" : "Auto";
  paintPhoneAssignGrid(wl);
  paintPctDisplay(pct, total);
  const rateMin = document.getElementById("wl-rate-min");
  const rateHour = document.getElementById("wl-rate-hour");
  if (rateMin) rateMin.textContent = harvest.per_min == null ? "-" : harvest.per_min;
  if (rateHour) {
    if (harvest.per_hour == null) {
      rateHour.innerHTML = 'Hourly<span class="wl-foot-val">-</span>';
    } else {
      rateHour.innerHTML = 'Hourly <span class="wl-foot-val">' + String(harvest.per_hour) + "</span>";
    }
  }
  const elapsedEl = document.getElementById("wl-elapsed");
  if (elapsedEl) {
    const sec = harvest.elapsed_s;
    elapsedEl.textContent = fmtElapsed(sec);
    elapsedEl.className = "wl-stat-side-val wl-elapsed-val " + (sec != null && sec > 0 ? "live" : "idle");
  }
  bindWlAutoOnce();
  bindWlMissActionsOnce();
  bindWlToCollectResetOnce();
  paintUploadBeacon(serverPulse, done);
  paintActivePhones(phones || [], uiPhoneSlots);
  paintListWatch(wl);
  paintCycle(wl);
  paintListPicks(wl, phones || []);
  const hint = document.getElementById("upload-hint");
  if (hint && upload) {
    const auto = upload.auto;
    hint.textContent = auto
      ? "Auto mode sends each product JSON to the member hub right after it is saved locally (bridge polling, duplicates skipped)."
      : "Manual mode keeps files in the PC Upload folder only. Use Upload now or switch to Auto on the Server tab.";
  }
}
function wlCountForShard(wl, shard){
  const rows = wl.phones || [];
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].shard === shard) return rows[i].pending == null ? 0 : rows[i].pending;
  }
  return 0;
}
const wlQtyByShard = {};
function readWlQtyFromDom(){
  document.querySelectorAll("#wl-picks input[id^='wl-n-']").forEach(function(el){
    const key = el.id.slice(5);
    const v = parseInt(el.value, 10);
    if (key && v >= 1) wlQtyByShard[key] = v;
  });
}
function wlQtyValue(shard){
  const key = String(shard);
  const v = wlQtyByShard[key];
  return v != null && v >= 1 ? v : 10;
}
function bindWlAutoOnce(){
  const btn = document.getElementById("wl-auto-distribute");
  if (!btn || btn.dataset.bound === "1") return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", function(){
    listTransfer("auto_distribute", -1);
  });
}
function bindWlMissActionsOnce(){
  const clearBtn = document.getElementById("wl-missed-clear");
  if (clearBtn && clearBtn.dataset.bound !== "1") {
    clearBtn.dataset.bound = "1";
    clearBtn.addEventListener("click", clearMissedList);
  }
  const recBtn = document.getElementById("wl-missed-recollect");
  if (recBtn && recBtn.dataset.bound !== "1") {
    recBtn.dataset.bound = "1";
    recBtn.addEventListener("click", recollectMissedList);
  }
}
async function clearMissedList(){
  const btn = document.getElementById("wl-missed-clear");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const msg = document.getElementById("harvest-msg");
  try {
    const r = await fetch("/api/list/transfer", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: "clear_missed"})
    });
    const d = await r.json();
    if (!d || !d.ok) {
      if (msg) msg.textContent = "Could not clear missed items.";
      return;
    }
    if (msg) msg.textContent = "";
    await tick();
  } catch (err) {
    if (msg) msg.textContent = "Could not clear missed items.";
  }
}
async function recollectMissedList(){
  const btn = document.getElementById("wl-missed-recollect");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const msg = document.getElementById("harvest-msg");
  try {
    const r = await fetch("/api/list/transfer", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: "recollect_missed"})
    });
    const d = await r.json();
    if (!d || !d.ok) {
      if (msg) msg.textContent = "Could not re-collect missed items.";
      return;
    }
    if (msg) msg.textContent = "";
    await tick();
  } catch (err) {
    if (msg) msg.textContent = "Could not re-collect missed items.";
  }
}
function bindWlToCollectResetOnce(){
  const btn = document.getElementById("wl-to-collect-reset");
  if (!btn || btn.dataset.bound === "1") return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", resetCollectionState);
}
async function resetCollectionState(){
  const btn = document.getElementById("wl-to-collect-reset");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const msg = document.getElementById("harvest-msg");
  try {
    const r = await fetch("/api/collect/reset", {method: "POST"});
    const d = await r.json();
    if (!d || !d.ok) {
      if (msg) msg.textContent = (d && d.error) ? d.error : "Could not reset collection state.";
      return;
    }
    if (msg) msg.textContent = "";
    await tick();
  } catch (err) {
    if (msg) msg.textContent = "Could not reset collection state.";
  }
}
function bindWlPickControls(host){
  if (!host || host.dataset.wlBound === "1") return;
  host.dataset.wlBound = "1";
  host.addEventListener("input", function(ev){
    const el = ev.target;
    if (!el || el.tagName !== "INPUT" || el.id.indexOf("wl-n-") !== 0) return;
    const key = el.id.slice(5);
    const v = parseInt(el.value, 10);
    if (key && v >= 1) wlQtyByShard[key] = v;
  });
  host.addEventListener("click", function(ev){
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    listTransfer(btn.getAttribute("data-act"), parseInt(btn.getAttribute("data-shard") || "0", 10));
  });
}
const WL_ICON_ASSIGN = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v11"/><path d="M8 11l4 4 4-4"/><path d="M5 20h14"/></svg>';
const WL_ICON_WITHDRAW = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20V9"/><path d="M8 13l4-4 4 4"/><path d="M5 4h14"/></svg>';
function paintPickQueueVisual(pickEl, cnt){
  if (!pickEl) return;
  pickEl.classList.toggle("has-queue", cnt > 0);
  const countEl = pickEl.querySelector(".wl-count");
  if (countEl) countEl.classList.toggle("is-zero", cnt <= 0);
}
function fmtPhoneQueueVal(n){
  let v = Math.floor(Number(n) || 0);
  if (v < 0) v = 0;
  if (v > 9999) v = 9999;
  return String(v);
}
function phoneAssignLineText(wl){
  wl = wl || {};
  const pendingByShard = {};
  (wl.phones || []).forEach(function(row){
    pendingByShard[row.shard] = row.pending == null ? 0 : row.pending;
  });
  const slots = uiPhoneSlots || 10;
  const parts = [];
  for (let shard = 0; shard < slots; shard++) {
    const n = pendingByShard[shard] != null ? pendingByShard[shard] : 0;
    parts.push(fmtPhoneQueueVal(n));
  }
  return parts.join(" · ");
}
function syncPhoneAssignMarquee(host, text){
  if (!host) return;
  let track = host.querySelector(".wl-phone-assign-track");
  if (!track) {
    host.innerHTML = '<span class="wl-phone-assign-track" aria-hidden="false"></span>';
    track = host.querySelector(".wl-phone-assign-track");
  }
  host.classList.remove("is-scroll");
  if (track) track.style.removeProperty("--wl-marquee-dur");
  if (!text) {
    if (track) track.textContent = "";
    return;
  }
  track.textContent = text;
  const overflow = track.scrollWidth > host.clientWidth + 2;
  if (overflow) {
    track.textContent = text + " · " + text;
    host.classList.add("is-scroll");
    const sec = Math.max(20, Math.min(55, track.scrollWidth / 24));
    track.style.setProperty("--wl-marquee-dur", sec + "s");
  }
}
function paintPhoneAssignGrid(wl){
  wl = wl || {};
  const host = document.getElementById("wl-phone-assign");
  if (!host) return;
  const pendingByShard = {};
  (wl.phones || []).forEach(function(row){
    pendingByShard[row.shard] = row.pending == null ? 0 : row.pending;
  });
  const slots = uiPhoneSlots || 10;
  const sig = slots + ":" + Array.from({length: slots}, function(_, shard){
    return pendingByShard[shard] != null ? pendingByShard[shard] : 0;
  }).join(",");
  const text = phoneAssignLineText(wl);
  if (host.dataset.sig !== sig || !host.querySelector(".wl-phone-assign-track")) {
    host.dataset.sig = sig;
    host.innerHTML = '<span class="wl-phone-assign-track"></span>';
  }
  syncPhoneAssignMarquee(host, text);
}
function paintListPicks(wl, phones){
  const wrap = document.getElementById("wl-picks");
  const phonesHost = document.getElementById("wl-picks-phones");
  if (!wrap || !phonesHost) return;
  readWlQtyFromDom();
  bindWlPickControls(wrap);
  const pendingByShard = {};
  (wl.phones || []).forEach(function(row){
    pendingByShard[row.shard] = row.pending == null ? 0 : row.pending;
  });
  const phoneList = expandPhoneSlots(phones || [], uiPhoneSlots || 10);
  const sig = phoneList.map(function(p){
    return String(p.shard) + ":" + (p.name || "") + ":" + (p._slot ? "0" : "1");
  }).join("|");
  const counts = phoneList.map(function(p){
    const shard = p.shard;
    return pendingByShard[shard] == null ? (p.list_pending == null ? 0 : p.list_pending) : pendingByShard[shard];
  });
  if (wrap.dataset.sig === sig && phonesHost.querySelector(".wl-pick")) {
    phoneList.forEach(function(p, idx){
      const shard = p.shard;
      const cnt = counts[idx];
      const el = document.getElementById("wl-c-" + shard);
      if (el) el.textContent = cnt;
      paintPickQueueVisual(phonesHost.querySelector('.wl-pick[data-shard="' + shard + '"]'), cnt);
    });
    return;
  }
  wrap.dataset.sig = sig;
  let html = "";
  phoneList.forEach(function(p, idx){
    const shard = p.shard;
    const num = idx + 1;
    const cnt = counts[idx];
    const qty = wlQtyValue(shard);
    const hasQ = cnt > 0 ? " has-queue" : "";
    const zeroCls = cnt > 0 ? "" : " is-zero";
    const slotCls = p._slot ? " is-slot-empty" : "";
    const slotDisabled = p._slot ? " disabled" : "";
    const phoneLabel = p.name || ("Slot " + num);
    html += '<div class="wl-pick wl-top-accent' + hasQ + slotCls + '" data-shard="' + shard + '">'
      + '<div class="wl-pick-top">'
      + '<span class="wl-index" title="Phone ' + num + '">' + esc(String(num)) + '</span>'
      + '<div class="wl-pick-head-body">'
      + '<span class="wl-pick-phone" title="' + esc(phoneLabel) + '">' + esc(phoneLabel) + '</span>'
      + '<span class="wl-count' + zeroCls + '" id="wl-c-' + shard + '">' + esc(cnt) + '</span>'
      + '</div></div>'
      + '<div class="wl-actions">'
      + '<input type="number" class="wl-qty-input" min="1" value="' + esc(String(qty)) + '" id="wl-n-' + shard + '" inputmode="numeric" aria-label="Quantity" title="Quantity"/>'
      + '<div class="wl-icon-btns">'
      + '<button type="button" class="wl-icon-btn" data-act="assign" data-shard="' + shard + '" title="Assign" aria-label="Assign"' + slotDisabled + '>' + WL_ICON_ASSIGN + '</button>'
      + '<button type="button" class="wl-icon-btn secondary" data-act="withdraw" data-shard="' + shard + '" title="Withdraw" aria-label="Withdraw"' + slotDisabled + '>' + WL_ICON_WITHDRAW + '</button>'
      + '</div></div></div>';
  });
  phonesHost.innerHTML = html;
}
async function listTransfer(action, shard){
  let count = 0;
  if (action === "assign" || action === "withdraw") {
    const input = document.getElementById("wl-n-" + shard);
    count = input ? parseInt(input.value, 10) : 0;
    if (!count || count < 1) return;
  }
  const autoWrap = document.getElementById("wl-auto-wrap");
  const autoBtn = document.getElementById("wl-auto-distribute");
  const busyAuto = action === "auto_distribute";
  if (busyAuto && autoWrap) autoWrap.classList.add("is-busy");
  if (busyAuto && autoBtn) autoBtn.disabled = true;
  const msg = document.getElementById("harvest-msg");
  try {
    const r = await fetch("/api/list/transfer", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: action, shard: shard, count: count})
    });
    const d = await r.json();
    if (d && d.error && msg) msg.textContent = d.error;
    else if (msg) msg.textContent = "";
    await tick();
  } catch (err) {
    if (msg) msg.textContent = "List transfer failed";
  } finally {
    if (busyAuto && autoWrap) autoWrap.classList.remove("is-busy");
    if (busyAuto && autoBtn) autoBtn.disabled = false;
  }
}
const pickedPhones = new Set();
let phoneSnap = [];
let uiPhoneSlots = 10;
function comparePhoneName(a, b){
  return String(a.name || "").localeCompare(String(b.name || ""), "ko", { numeric: true, sensitivity: "base" });
}
function phoneSessionCountHtml(p){
  if (p._slot) return '<span class="pick-session-count">—</span>';
  const avail = p.sessions_available == null ? 0 : Number(p.sessions_available);
  const total = p.sessions_total == null ? 0 : Number(p.sessions_total);
  return '<span class="pick-session-count" title="Available sessions / total assigned">'
    + avail + "/" + total + "</span>";
}
function phoneTitleHtml(p, opsBusy){
  let html = '<span class="pick-title-main"><span class="pick-name">' + esc(p.name || "") + "</span>";
  if (!opsBusy && p.userid) {
    html += '<span class="pick-account"> - ' + esc(p.userid) + "</span>";
  }
  html += "</span>";
  return html;
}
function pickSelectedMarkHtml(){
  return '<span class="pick-selected-mark" title="Selected" aria-hidden="true">'
    + '<svg viewBox="0 0 20 20" aria-hidden="true">'
    + '<circle cx="10" cy="10" r="9" fill="#14b8a6"/>'
    + '<path d="M6 10.2 8.6 12.8 14 7.4" stroke="#fff" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    + '</svg></span>';
}
function adbPhoneIcon(connected){
  if (connected) {
    return '<span class="adb-icon ok" title="ADB connected" aria-label="ADB connected">'
      + '<svg viewBox="0 0 20 20" aria-hidden="true">'
      + '<rect x="2" y="13" width="2.5" height="5" rx="0.6" fill="#7dd3fc"/>'
      + '<rect x="6" y="10" width="2.5" height="8" rx="0.6" fill="#38bdf8"/>'
      + '<rect x="10" y="7" width="2.5" height="11" rx="0.6" fill="#3ec0ff"/>'
      + '<rect x="14" y="4" width="2.5" height="14" rx="0.6" fill="#2563eb"/>'
      + '</svg></span>';
  }
  return '<span class="adb-icon bad" title="ADB disconnected" aria-label="ADB disconnected">'
    + '<svg viewBox="0 0 20 20" aria-hidden="true">'
    + '<rect x="2" y="13" width="2.5" height="5" rx="0.6" fill="#fca5a5"/>'
    + '<rect x="6" y="10" width="2.5" height="8" rx="0.6" fill="#f87171"/>'
    + '<rect x="10" y="7" width="2.5" height="11" rx="0.6" fill="#ef4444" opacity="0.65"/>'
    + '<line x1="1" y1="3" x2="19" y2="17" stroke="#dc2626" stroke-width="1.5" stroke-linecap="round"/>'
    + '</svg></span>';
}
function collectPhonePick(p){
  if (p._slot) {
    const num = Number(p.shard) + 1;
    const label = "Slot " + num;
    return '<div class="pick collect-phone tone-blue is-slot-empty" aria-disabled="true" title="Register a device on shard ' + num + ' (phone_fleet.register_phone)">'
      + '<div class="pick-earpiece"><div class="pick-earpiece-line"><span class="pick-notch"></span></div></div>'
      + '<span class="pick-screen collect-body">'
      + '<div class="pick-title-row"><span class="pick-name">' + esc(label) + '</span></div>'
      + '<p class="meta">Empty fleet slot · shard ' + num + '</p>'
      + '<div class="mini">'
      + '<div class="card status-col"><div class="k">Status</div><div class="v">—</div></div>'
      + '<div class="card"><div class="k">Saved</div><div class="v ok">0</div></div>'
      + '<div class="card"><div class="k">Missed</div><div class="v bad">0</div></div>'
      + '<div class="card"><div class="k">Per min</div><div class="v">—</div></div>'
      + '</div><pre class="empty"></pre></span><span class="pick-home"></span></div>';
  }
  const picked = pickedPhones.has(p.name) ? " on" : "";
  const regionBusy = p.activity_mode === "region";
  const opsBusy = phoneActivityOpsBusy(p);
  const harvestIdle = !p.running || opsBusy;
  const waiting = p.running && !opsBusy && p.state === "대기";
  const activelyCollecting = p.running && !opsBusy && !waiting;
  const collecting = activelyCollecting ? " collecting" : "";
  const waitTone = waiting ? " waiting" : "";
  const regionTone = regionBusy ? " region" : "";
  let ledKind = phoneHarvestLedKind(p);
  let tone = "tone-blue";
  if (harvestIdle) {
    tone = "tone-blue";
  } else if (ledKind === " off") {
    tone = "tone-red";
  } else if (ledKind === " yellow") {
    tone = "tone-yellow";
  } else {
    tone = "tone-green";
  }
  const captchaOn = phoneCaptchaActive(p);
  const loginOn = phoneLoginWaiting(p);
  const capBeaconCls = captchaOn ? " is-live" : "";
  const capAria = captchaOn ? ' role="img" aria-label="Captcha on screen"' : ' aria-hidden="true"';
  const capBeacon = '<span class="pick-cap-beacon' + capBeaconCls + '"' + capAria + '>'
    + '<span class="pick-cap-beacon-dot"></span><span class="pick-cap-beacon-stem"></span></span>';
  const earLeft = '<span class="pick-ear-left">' + pickSelectedMarkHtml() + capBeacon + pickLoginWaitIcon(loginOn) + '</span>';
  const lifeSig = pickLifeSignal(p, ledKind, captchaOn);
  const earpiece = '<div class="pick-earpiece"><div class="pick-earpiece-line">' + earLeft
    + '<span class="pick-notch"></span>' + lifeSig + '</div></div>';
  const statusIdle = opsBusy || !p.running;
  let run = collectCardStatus(p);
  let runCls = collectCardStatusClass(p);
  let saved = opsBusy ? 0 : p.saved;
  let miss = opsBusy ? 0 : p.miss;
  let rate = opsBusy ? "-" : (p.per_min == null ? "-" : p.per_min);
  let regionBar = "";
  let metaBlock = "";
  let noteLine = "";
  const tailBlock = phoneTailHtml(p);
  if (regionBusy) {
    const pct = Math.max(0, Math.min(100, Number(p.region_progress) || 0));
    regionBar = '<div class="pick-bar"><span style="width:' + pct + '%"></span></div>'
      + '<div class="pick-pct">' + pct + '%</div>';
  }
  if (opsBusy) {
    noteLine = '<p class="note">' + esc(p.note || "-") + '</p>';
    metaBlock = tailBlock;
  } else if (p.running) {
    const last = p.last || "-";
    const lastSec = p.last_sec == null ? "" : (" · " + p.last_sec + "s");
    const okRate = p.ok_rate == null ? "-" : (p.ok_rate + "%");
    noteLine = '<p class="note">' + esc(p.note || "-") + '</p>';
    metaBlock = '<p class="meta">Last ' + esc(last) + esc(lastSec)
      + '<br>Success ' + esc(okRate)
      + ' · Elapsed ' + fmtElapsed(p.elapsed_s) + '</p>'
      + tailBlock;
  } else {
    const okRate = p.ok_rate == null ? "-" : (p.ok_rate + "%");
    metaBlock = '<p class="meta">Last -'
      + '<br>Success ' + esc(okRate)
      + ' · Elapsed ' + fmtElapsed(0) + '</p>'
      + tailBlock;
  }
  const body = '<div class="pick-title-row">' + adbPhoneIcon(!!p.adb_connected)
    + phoneTitleHtml(p, opsBusy)
    + phoneSessionCountHtml(p) + '</div>'
    + '<p class="meta">' + esc(p.serial || "-") + ' · port ' + esc(p.port) + '</p>'
    + '<div class="mini">'
    + '<div class="card status-col"><div class="k">Status</div><div class="v ' + runCls + '">' + esc(run) + '</div></div>'
    + '<div class="card"><div class="k">Saved</div><div class="v ok">' + esc(saved) + '</div></div>'
    + '<div class="card"><div class="k">Missed</div><div class="v bad">' + esc(miss) + '</div></div>'
    + '<div class="card"><div class="k">Per min</div><div class="v">' + esc(rate) + '</div></div>'
    + '</div>'
    + regionBar
    + ((opsBusy || p.running) ? noteLine : "")
    + metaBlock;
  const captchaTone = captchaOn ? " captcha-wait" : "";
  const loginTone = loginOn ? " login-wait" : "";
  return '<button type="button" class="pick collect-phone ' + tone + collecting + waitTone + regionTone + captchaTone + loginTone + picked + '" data-phone="' + esc(p.name) + '" aria-pressed="' + (picked ? "true" : "false") + '">'
    + earpiece
    + '<span class="pick-screen collect-body">'
    + body
    + '</span><span class="pick-home"></span></button>';
}
function syncCollectActions(){
  const selected = phoneSnap.filter(function(p){ return pickedPhones.has(p.name); });
  const start = document.getElementById("collect-start");
  const stop = document.getElementById("collect-stop");
  const regionBtn = document.getElementById("collect-region");
  const sessionBtn = document.getElementById("collect-session");
  const sessionStopBtn = document.getElementById("collect-session-stop");
  const adbReconnectBtn = document.getElementById("collect-adb-reconnect");
  const reinstallBtn = document.getElementById("collect-reinstall-app");
  const all = document.getElementById("collect-all");
  const sessionOpsBusy = sessionBtn && sessionBtn.dataset.busy === "1";
  const toolOpsBusy = (adbReconnectBtn && adbReconnectBtn.dataset.busy === "1")
    || (reinstallBtn && reinstallBtn.dataset.busy === "1");
  const inRegion = selected.some(function(p){ return p.activity_mode === "region"; });
  const inSession = selected.some(function(p){ return p.activity_mode === "session"; });
  const inOps = selected.some(function(p){ return phoneActivityOpsBusy(p); });
  const waiting = selected.some(function(p){
    return !p.running && !phoneActivityOpsBusy(p);
  });
  const collecting = selected.some(function(p){ return p.running; });
  const canRegion = selected.some(function(p){ return p.activity_mode !== "region"; });
  if (start) start.disabled = sessionOpsBusy || selected.length === 0 || !waiting || inOps;
  if (stop) stop.disabled = sessionOpsBusy || selected.length === 0 || waiting || !collecting;
  if (regionBtn) {
    regionBtn.disabled = selected.length === 0 || !canRegion || inOps || (regionBtn.dataset.busy === "1");
  }
  if (sessionBtn) {
    sessionBtn.disabled = sessionOpsBusy || selected.length === 0 || inOps;
  }
  if (sessionStopBtn) {
    sessionStopBtn.disabled = sessionOpsBusy || toolOpsBusy || selected.length === 0 || !inSession;
  }
  const canTool = selected.length > 0 && !inRegion && !inSession;
  // ADB reconnect: enable when a selected card has a dropped ADB connection,
  // except while it is doing Change region. Otherwise fall back to normal rule.
  const selDisconnected = selected.some(function(p){
    return !p.adb_connected && p.activity_mode !== "region";
  });
  if (adbReconnectBtn) {
    adbReconnectBtn.disabled = selected.length === 0 || toolOpsBusy || inRegion
      || !(selDisconnected || (!sessionOpsBusy && !inSession));
  }
  if (reinstallBtn) {
    reinstallBtn.disabled = sessionOpsBusy || toolOpsBusy || !canTool;
  }
  if (all) all.checked = phoneSnap.length > 0 && phoneSnap.every(function(p){ return pickedPhones.has(p.name); });
}
function setPhoneOpsBusy(on){
  ["collect-session", "collect-session-stop", "collect-adb-reconnect", "collect-reinstall-app"].forEach(function(id){
    const el = document.getElementById(id);
    if (el) el.dataset.busy = on ? "1" : "";
  });
  syncCollectActions();
}
function selectedPhoneNames(){
  return phoneSnap.filter(function(p){ return pickedPhones.has(p.name); }).map(function(p){ return p.name; });
}
async function runPhoneToolAction(action, failMsg){
  const phones = selectedPhoneNames();
  if (!phones.length) return;
  setPhoneOpsBusy(true);
  const msg = document.getElementById("harvest-msg");
  try {
    const r = await fetch("/api/sessions/action", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: action, phones: phones})
    });
    const d = await r.json();
    if (msg && d && d.skipped && d.skipped.length && !(d.started && d.started.length)) {
      msg.textContent = failMsg;
    } else if (msg) msg.textContent = "";
    await tick();
  } catch (err) {
    if (msg) msg.textContent = failMsg;
  } finally {
    setPhoneOpsBusy(false);
  }
}
async function runCollectAdbReconnect(){
  const btn = document.getElementById("collect-adb-reconnect");
  if (!btn || btn.disabled) return;
  await runPhoneToolAction("adb", "Could not start ADB reconnect.");
}
async function runCollectReinstallApp(){
  const btn = document.getElementById("collect-reinstall-app");
  if (!btn || btn.disabled) return;
  await runPhoneToolAction("reinstall", "Could not start app reinstall.");
}
function setRegionActionBusy(on){
  const el = document.getElementById("collect-region");
  if (el) el.dataset.busy = on ? "1" : "";
  syncCollectActions();
}
async function runCollectRegion(){
  const regionBtn = document.getElementById("collect-region");
  if (!regionBtn || regionBtn.disabled) return;
  const phones = phoneSnap.filter(function(p){ return pickedPhones.has(p.name); }).map(function(p){ return p.name; });
  if (!phones.length) return;
  setRegionActionBusy(true);
  const msg = document.getElementById("harvest-msg");
  try {
    const r = await fetch("/api/sessions/action", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: "region", phones: phones})
    });
    const d = await r.json();
    if (msg && d && d.skipped && d.skipped.length && !d.started.length) {
      msg.textContent = "Could not start region change.";
    } else if (msg) msg.textContent = "";
    await tick();
  } catch (err) {
    if (msg) msg.textContent = "Region change request failed.";
  } finally {
    setRegionActionBusy(false);
  }
}
async function runCollectSessionStop(){
  const btn = document.getElementById("collect-session-stop");
  if (!btn || btn.disabled) return;
  const phones = phoneSnap.filter(function(p){ return pickedPhones.has(p.name); }).map(function(p){ return p.name; });
  if (!phones.length) return;
  setPhoneOpsBusy(true);
  const msg = document.getElementById("harvest-msg");
  try {
    const r = await fetch("/api/sessions/action", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: "stop_session", phones: phones})
    });
    const d = await r.json();
    if (msg && d && d.skipped && d.skipped.length && !(d.stopped && d.stopped.length)) {
      msg.textContent = "Could not stop session collection.";
    } else if (msg) msg.textContent = "";
    await tick();
  } catch (err) {
    if (msg) msg.textContent = "Session stop request failed.";
  } finally {
    setPhoneOpsBusy(false);
  }
}
async function runCollectSession(){
  const sessionBtn = document.getElementById("collect-session");
  if (!sessionBtn || sessionBtn.disabled) return;
  const phones = phoneSnap.filter(function(p){ return pickedPhones.has(p.name); }).map(function(p){ return p.name; });
  if (!phones.length) return;
  setPhoneOpsBusy(true);
  const msg = document.getElementById("harvest-msg");
  try {
    const r = await fetch("/api/sessions/action", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: "session", phones: phones})
    });
    const d = await r.json();
    if (msg && d && d.skipped && d.skipped.length && !(d.started && d.started.length)) {
      msg.textContent = "Could not start session collection.";
    } else if (msg) msg.textContent = "";
    await tick();
  } catch (err) {
    if (msg) msg.textContent = "Session collection request failed.";
  } finally {
    setPhoneOpsBusy(false);
  }
}
function toggleCollectAll(on){
  pickedPhones.clear();
  if (on) phoneSnap.forEach(function(p){ pickedPhones.add(p.name); });
  document.querySelectorAll("#collect-phones .pick").forEach(function(card){
    const name = card.getAttribute("data-phone");
    const onCard = pickedPhones.has(name);
    card.classList.toggle("on", onCard);
    card.setAttribute("aria-pressed", onCard ? "true" : "false");
  });
  syncCollectActions();
}
async function runCollectAction(action){
  const start = document.getElementById("collect-start");
  const stop = document.getElementById("collect-stop");
  if ((action === "start" && start.disabled) || (action === "stop" && stop.disabled)) return;
  const selected = phoneSnap.filter(function(p){ return pickedPhones.has(p.name); });
  const targets = selected.filter(function(p){ return action === "stop" ? p.running : !p.running; });
  if (!targets.length) return;
  start.disabled = true;
  stop.disabled = true;
  const msg = document.getElementById("harvest-msg");
  const errors = [];
  for (let i = 0; i < targets.length; i++) {
    try {
      const r = await fetch("/api/collect/action", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action: action, phone: targets[i].name})
      });
      const d = await r.json();
      if (d && d.error) errors.push(d.error);
    } catch (err) {
      errors.push("Could not reach the collector.");
    }
  }
  if (msg) msg.textContent = errors.join(" ");
  await tick();
}
let tickBusy = false;
let tickQueued = false;
async function tick(){
  if (tickBusy) {
    tickQueued = true;
    return;
  }
  tickBusy = true;
  try {
    const r = await fetch("/api/status?t=" + Date.now(), {cache: "no-store"});
    if (!r.ok) {
      throw new Error("HTTP " + r.status);
    }
    const d = await r.json();
    const lm = d.list_mode || "mode2";
    document.getElementById("method").textContent =
      collectMethod(d.method) + " · " + (lm === "mode2" ? "목록관리2" : "목록관리1");
    paintListMode(lm);
    document.getElementById("sub").textContent = "Up to " + uiPhoneSlots + " phones · refreshes every 2 seconds.";
    document.getElementById("collect-path").textContent = d.folder || "-";
    uiPhoneSlots = d.ui_phone_slots == null ? 10 : d.ui_phone_slots;
    const phones = (d.phones || []).slice().sort(comparePhoneName);
    paintWorkload(d.list_workload, d.upload, phones, {
      per_min: d.per_min,
      per_hour: d.per_hour,
      running_n: d.running_n,
      phones_n: d.phones_n,
      elapsed_s: d.elapsed_s
    }, d.server_pulse);
    const names = {};
    phones.forEach(function(p){ names[p.name] = true; });
    Array.from(pickedPhones).forEach(function(name){ if (!names[name]) pickedPhones.delete(name); });
    phoneSnap = phones;
    const collectList = expandPhoneSlots(phones, uiPhoneSlots);
    document.getElementById("collect-phones").innerHTML = collectList.map(collectPhonePick).join("");
    syncCollectActions();
    const msg = document.getElementById("harvest-msg");
    if (msg && msg.textContent && msg.textContent.indexOf("UI refresh failed") === 0) msg.textContent = "";
  } catch (err) {
    const msg = document.getElementById("harvest-msg");
    if (msg) msg.textContent = "UI refresh failed — " + (err && err.message ? err.message : String(err));
  } finally {
    tickBusy = false;
    if (tickQueued) {
      tickQueued = false;
      tick();
    }
  }
}
function fmtWhen(ts){
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "-";
  const p = n => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth()+1) + "-" + p(d.getDate())
    + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}
function stateMark(state){
  if (state === "신선") return "fresh";
  if (state === "이용중") return "busy";
  return "";
}
function stateLabel(state){
  if (state === "신선") return "Fresh";
  if (state === "이용중") return "In use";
  if (state === "이용 끝") return "Used";
  return state || "-";
}
function reasonLabel(reason){
  const text = String(reason || "");
  const known = {
    "시작": "start",
    "수집 시작": "Active",
    "차단": "Blocked",
    "페이지 사용 불가": "Page unavailable",
    "로그인 필요": "Login required",
    "화면에 캡차 없음": "No captcha on screen",
    "보관 세션 다시": "saved session again",
    "연속 미저장": "Consecutive misses"
  };
  if (known[text]) return known[text];
  const every = text.match(/^(\\d+)건$/);
  if (every) return "Completed " + every[1] + " items and handed over";
  return text;
}
function accountStatus(phone, row){
  if (row.open && phone.collecting) return "Collecting";
  if (row.open) return "Active";
  if (!row.used) return "Fresh";
  return reasonLabel(row.reason) || "Used";
}
function showTab(name){
  document.getElementById("panel-collect").hidden = name !== "collect";
  document.getElementById("panel-sessions").hidden = name !== "sessions";
  document.getElementById("panel-sever").hidden = name !== "sever";
  const collect = document.getElementById("tab-collect");
  const sessions = document.getElementById("tab-sessions");
  const sever = document.getElementById("tab-sever");
  collect.className = name === "collect" ? "on" : "";
  sessions.className = name === "sessions" ? "on" : "";
  sever.className = name === "sever" ? "on" : "";
  collect.setAttribute("aria-selected", name === "collect" ? "true" : "false");
  sessions.setAttribute("aria-selected", name === "sessions" ? "true" : "false");
  sever.setAttribute("aria-selected", name === "sever" ? "true" : "false");
  if (name === "sessions") tickSessions();
  if (name === "sever") tickSever();
}
function useLine(u){
  const end = u.ended_at ? fmtWhen(u.ended_at) : "in use";
  const why = u.reason ? " · " + esc(reasonLabel(u.reason)) : "";
  return esc(u.phone || "-") + " · " + esc(u.saved) + " saved · "
    + fmtWhen(u.started_at) + " → " + end + why;
}
function regionColumn(phone){
  const items = (phone && phone.roster) || [];
  const unset = phone && phone.unset ? " unset" : "";
  const n = items.length;
  const head = "<h3><span class='phone-h'>" + esc(phone.name || "") + "</span><span class='phone-n'>" + n + "</span></h3>";
  if (!items.length) {
    return "<section class='manage-col" + unset + "'>" + head + "<div class='sess-empty'>None</div></section>";
  }
  const rows = items.map(function(s){
    const lamp = (s.lamp === "live" || s.lamp === "yellow") ? s.lamp : "blue";
    const when = s.saved_at ? fmtWhen(s.saved_at) : "-";
    const saved = s.saved == null ? "-" : s.saved;
    return "<div class='sess-item'><span class='sess-led " + lamp + "'></span>"
      + "<span class='sess-id'>" + esc(s.userid)
      + "<span class='sess-state'>" + esc(accountStatus(phone, s)) + "</span></span>"
      + "<span class='sess-when'>" + esc(when) + "</span>"
      + "<span class='sess-n'>" + esc(saved) + "</span></div>";
  }).join("");
  return "<section class='manage-col" + unset + "'>" + head
    + "<div class='sess-col'>"
    + "<div class='sess-item labels'><span></span><span>Session</span><span>Saved at</span><span>Saved</span></div>"
    + rows + "</div></section>";
}
function bindSessionDrag(root){
  root.querySelectorAll(".sess-col").forEach(function(col){
    col.addEventListener("pointerdown", function(ev){
      if (ev.button !== 0) return;
      const rect = col.getBoundingClientRect();
      if (ev.clientX > rect.right - 14) return;
      col._drag = { y: ev.clientY, top: col.scrollTop };
      col.classList.add("dragging");
      try { col.setPointerCapture(ev.pointerId); } catch (err) {}
    });
    col.addEventListener("pointermove", function(ev){
      if (!col._drag) return;
      col.scrollTop = col._drag.top - (ev.clientY - col._drag.y);
    });
    function endDrag(){
      if (!col._drag) return;
      col._drag = null;
      col.classList.remove("dragging");
    }
    col.addEventListener("pointerup", endDrag);
    col.addEventListener("pointercancel", endDrag);
  });
}
function renderSessions(d){
  document.getElementById("sess-path").textContent = "Storage  " + (d.path || "-");
  document.getElementById("sess-total").textContent = d.total || 0;
  document.getElementById("sess-free").textContent = d.available || 0;
  document.getElementById("sess-busy").textContent = d.in_use || 0;
  document.getElementById("sess-fresh").textContent = d.fresh || 0;
  const box = document.getElementById("sess-manage");
  if (box.querySelector(".sess-col.dragging")) return;
  const tops = Array.from(box.querySelectorAll(".sess-col")).map(function(el){ return el.scrollTop; });
  const phones = d.phones || [];
  box.innerHTML = phones.map(regionColumn).join("");
  box.querySelectorAll(".sess-col").forEach(function(el, i){
    if (tops[i]) el.scrollTop = tops[i];
  });
  bindSessionDrag(box);
}
async function loadSessions(){
  const r = await fetch("/api/sessions?t=" + Date.now(), {cache: "no-store"});
  if (!r.ok) {
    const msg = await r.text();
    document.getElementById("sess-manage").innerHTML =
      "<p class='sub sess-empty'>Sessions API error (" + r.status + "): " + esc(msg.slice(0, 200)) + "</p>";
    return;
  }
  renderSessions(await r.json());
  const now = new Date();
  const p = n => String(n).padStart(2, "0");
  document.getElementById("sess-refreshed").textContent =
    "Updated " + p(now.getHours()) + ":" + p(now.getMinutes()) + ":" + p(now.getSeconds());
}
async function refreshSessions(){
  const btn = document.getElementById("sess-refresh");
  btn.disabled = true;
  try {
    await loadSessions();
  } finally {
    btn.disabled = false;
  }
}
async function tickSessions(){
  if (document.getElementById("panel-sessions").hidden) return;
  await loadSessions();
}
function paintLink(d){
  const el = document.getElementById("link-live");
  const on = !!(d && d.ok && d.bridge);
  el.className = "link-live" + (on ? " on" : " off");
  const label = on ? "Live — COM linked to server (bridge)" : "Offline — no server bridge";
  el.title = label;
  el.setAttribute("aria-label", label);
}
function severConn(label, value){
  return "<tr><td>" + esc(label) + "</td><td>" + esc(value) + "</td></tr>";
}
function renderSever(d){
  const auto = d.mode !== "manual";
  document.getElementById("mode-auto").className = auto ? "on" : "";
  document.getElementById("mode-manual").className = auto ? "" : "on";
  document.getElementById("sever-upload-now").disabled = auto;
  document.getElementById("sever-path").textContent = "Upload  " + (d.folder || "-");
  document.getElementById("sever-down").textContent = "Download  " + (d.download || "-");
  document.getElementById("sever-note").textContent = d.note || (d.error || "");
  document.getElementById("sever-vault").textContent = d.packets == null ? "-" : d.packets;
  document.getElementById("sever-inbox").textContent = d.inbox == null ? "-" : d.inbox;
  document.getElementById("sever-tasks").textContent = d.tasks == null ? "-" : d.tasks;
  document.getElementById("sever-assigned").textContent = d.assigned == null ? "-" : d.assigned;
  document.getElementById("sever-sent").textContent = d.sent || 0;
  document.getElementById("sever-pending").textContent = d.pending || 0;
  const wl = d.list_workload || {};
  const wlDone = wl.list_done == null ? 0 : wl.list_done;
  const wlMiss = wl.list_missed == null ? 0 : wl.list_missed;
  const wlProcessed = wl.list_processed != null ? wl.list_processed : (wlDone + wlMiss);
  document.getElementById("sever-list-done").textContent = wlProcessed;
  document.getElementById("sever-list-pending").textContent = wl.list_pending == null ? 0 : wl.list_pending;
  const link = (d.host || "-") + ":" + (d.port || "-");
  if (d.list_mode) paintListMode(d.list_mode);
  document.getElementById("sever-conn").innerHTML = [
    severConn("Member", (d.member || "-") + (d.name ? " · " + d.name : "")),
    severConn("Host", link),
    severConn("Key", d.key_stored ? "stored locally" : "missing"),
    severConn("Hub", d.ok ? "connected" : (d.error || "offline")),
    severConn("Dispatch open", d.dispatch_open == null ? "-" : d.dispatch_open),
    severConn("Dispatch uploaded", d.dispatch_uploaded == null ? "-" : d.dispatch_uploaded),
    severConn("Local products", d.local_products || 0),
    severConn("Upload failures", d.upload_fail || 0),
    severConn("Harvest", d.harvest || "waiting"),
    severConn("Bridge", d.bridge ? ("live " + (d.bridge_at || "")) : "stopped"),
    severConn("List updated", d.tasks_at || "-"),
    severConn("Upload mode", d.upload_auto ? "Auto (on save)" : "Manual"),
    severConn("List progress", wl.list_total == null ? "-" : (wlProcessed + " / " + wl.list_total + " (done+missed)"))
  ].join("");
  const samples = d.samples || [];
  document.getElementById("sever-samples").innerHTML = samples.length
    ? samples.map(function(row){
        return "<tr><td>" + esc(row.shop_id) + "</td><td>" + esc(row.item_id)
          + "</td><td>" + esc(row.expire || "-") + "</td></tr>";
      }).join("")
    : "<tr><td class='pcell empty' colspan='3'>Waiting for the server list.</td></tr>";
  const recent = d.recent || [];
  document.getElementById("sever-recent").innerHTML = recent.length
    ? recent.map(function(row){
        return "<tr><td>" + esc(row.at || "-") + "</td><td>" + esc(row.file)
          + "</td><td>" + esc(row.result || "-") + "</td></tr>";
      }).join("")
    : "<tr><td class='pcell empty' colspan='3'>No upload yet. New product files are sent as they are saved.</td></tr>";
}
async function loadSever(){
  const r = await fetch("/api/sever?t=" + Date.now(), {cache: "no-store"});
  const d = await r.json();
  paintLink(d);
  renderSever(d);
  const now = new Date();
  const p = n => String(n).padStart(2, "0");
  document.getElementById("sever-refreshed").textContent =
    "Updated " + p(now.getHours()) + ":" + p(now.getMinutes()) + ":" + p(now.getSeconds());
}
async function tickLink(){
  if (!document.getElementById("panel-sever").hidden) {
    await loadSever();
    return;
  }
  try {
    const r = await fetch("/api/sever?t=" + Date.now(), {cache: "no-store"});
    paintLink(await r.json());
  } catch (e) {
    paintLink(null);
  }
}
async function refreshSever(){
  const btn = document.getElementById("sever-refresh");
  btn.disabled = true;
  try { await loadSever(); } finally { btn.disabled = false; }
}
async function tickSever(){
  if (document.getElementById("panel-sever").hidden) return;
  await loadSever();
}
async function setSeverMode(mode){
  await fetch("/api/sever/mode", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({mode: mode})
  });
  await loadSever();
}
async function uploadNow(){
  const btn = document.getElementById("sever-upload-now");
  btn.disabled = true;
  try {
    await fetch("/api/sever/upload", {method: "POST"});
    await loadSever();
  } finally {
    btn.disabled = document.getElementById("mode-manual").className !== "on";
  }
}
document.getElementById("collect-phones").addEventListener("click", function(ev){
  const card = ev.target.closest(".pick");
  if (!card) return;
  const name = card.getAttribute("data-phone");
  if (!name) return;
  if (pickedPhones.has(name)) pickedPhones.delete(name);
  else pickedPhones.add(name);
  const onCard = pickedPhones.has(name);
  card.classList.toggle("on", onCard);
  card.setAttribute("aria-pressed", onCard ? "true" : "false");
  syncCollectActions();
});
function paintListMode(mode){
  const lm2 = (mode || "mode2") === "mode2";
  const a = document.getElementById("listmode-1");
  const b = document.getElementById("listmode-2");
  if (a) a.className = lm2 ? "" : "on";
  if (b) b.className = lm2 ? "on" : "";
  document.body.classList.toggle("lm2", lm2);
}
async function setListMode(mode){
  const r = await fetch("/api/sever/list_mode", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({list_mode: mode})
  });
  try { const j = await r.json(); paintListMode(j.list_mode); } catch (e) {}
}
tick();
tickLink();
setInterval(tick, 2000);
setInterval(tickSessions, 2000);
setInterval(tickLink, 2000);
</script>
</body>
</html>
"""


def _page_html() -> str:
    stamp = datetime.fromtimestamp(Path(__file__).stat().st_mtime).strftime("%m/%d %H:%M")
    return HTML.replace("{{UI_BUILD}}", stamp)


def _decode(path: Path) -> str:
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    elif raw.count(b"\x00") > max(8, len(raw) // 8):
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.replace("\x00", "")


def _session_text(path: Path) -> str:
    text = _decode(path)
    idx = max(
        text.rfind("---- list shard"),
        text.rfind("---- row128 shard"),
        text.rfind("---- rows25 shard"),
    )
    if idx >= 0:
        return text[idx:]
    idx = text.rfind("【VMOS 앱 전용 캡처】")
    if idx >= 0:
        return text[idx:]
    return text


def _procs() -> list[dict]:
    now = time.time()
    if now - float(_PROC_CACHE["at"]) < 5:
        return list(_PROC_CACHE["rows"])
    rows: list[dict] = []
    try:
        out = subprocess.check_output(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -like '*_run_row128.py*' -or $_.CommandLine -like '*_run_rows25_27.py*' -or $_.CommandLine -like '*_run_list.py*' } | "
                "ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId, "
                "([DateTime]$_.CreationDate).ToUniversalTime().ToString('o'), $_.CommandLine }",
            ],
            text=True, timeout=8, encoding="utf-8", errors="replace",
        )
        for line in (out or "").splitlines():
            parts = line.strip().split("|", 2)
            if len(parts) < 3:
                continue
            m = re.search(r"(_run_row128|_run_rows25_27|_run_list)\.py\D+(\d+)\s*$", parts[2])
            if not m:
                continue
            started = datetime.fromisoformat(parts[1].replace("Z", "+00:00")).timestamp()
            rows.append({
                "script": m.group(1),
                "shard": int(m.group(2)),
                "pid": int(parts[0]),
                "started_at": started,
            })
    except Exception:
        rows = list(_PROC_CACHE["rows"])
    _PROC_CACHE["at"] = now
    _PROC_CACHE["rows"] = rows
    return rows


def _captcha_waiting(phone: dict) -> bool:
    if phone.get("state") == "캡차 대기":
        return True
    note = str(phone.get("note") or "")
    if "캡차 대기" in note or "PDP 418" in note:
        return True
    for ln in phone.get("tail") or []:
        s = str(ln)
        if "캡차 대기" in s or "PDP 418" in s or "/verify/traffic" in s:
            return True
    return False


_PHONE_TAIL_LINES = 16
_OPS_ACTIVITY = frozenset({"region", "session", "adb", "reinstall"})


def _tail_recent(lines: list[str], keep: int = _PHONE_TAIL_LINES) -> list[str]:
    picked = [ln for ln in lines if str(ln).strip()]
    if not picked:
        return []
    return list(reversed(picked[-keep:]))


def _ops_log_tail(name: str, pad: str = "", keep: int = _PHONE_TAIL_LINES) -> list[str]:
    path = RUNTIME / "_run_br_three.log"
    if not path.is_file():
        return []
    text = _decode(path)
    keys = [part for part in (name, pad) if part]
    if not keys:
        return []
    hits = [ln for ln in text.splitlines() if any(key in ln for key in keys)]
    return _tail_recent(hits, keep=keep)


def _login_waiting(phone: dict) -> bool:
    if phone.get("activity_mode") == "session":
        return True
    note = str(phone.get("note") or "")
    if "로그인 대기" in note or "로그인 필요" in note:
        return True
    for ln in phone.get("tail") or []:
        s = str(ln)
        if "로그인 대기" in s or "로그인 필요" in s:
            return True
    return False


def _last_ui(last: str) -> str:
    s = str(last or "")
    if s.startswith("저장 "):
        return "Saved " + s[3:]
    if s.startswith("미저장 "):
        return "Missed " + s[4:]
    return s


def _state(note: str, running: bool) -> str:
    if "할당 대기" in note:
        return "대기"
    if "끝날 때까지 대기" in note:
        return "대기"
    if "목록 갱신 대기" in note:
        return "대기"
    if "재수집 시작" in note:
        return "재수집"
    if "완료 저장" in note or "재수집 생략" in note or "재수집 끝" in note:
        return "종료"
    if not running:
        return "중지"
    if "캡차 대기" in note:
        return "캡차 대기"
    if "다음 세션" in note or "계정 전환" in note or "세션 복원" in note or "세션 보관" in note:
        return "계정 전환"
    if "완료 저장" in note:
        return "종료"
    if "미저장" in note:
        return "재기동 중"
    return "수집 중"


def _note(lines: list[str], running: bool) -> str:
    for ln in reversed(lines):
        s = ln.strip()
        if not s:
            continue
        if "할당 대기" in s or "끝날 때까지 대기" in s or "목록 갱신 대기" in s or "재수집" in s:
            return s
        if "캡차 대기" in s:
            return s
        if "45초" in s or "다음 세션으로 전환" in s:
            return s
        if "캡차 통과" in s:
            return "캡차 통과 — 수집 재개"
        if "PDP 418" in s:
            return "캡차 대기"
        if "계정 전환" in s or "세션 복원" in s or "세션 보관" in s:
            return s
        if s.startswith("✅"):
            return "수집 진행 중"
        if "미저장" in s and "i." in s:
            return "미저장 — 다음 상품 재기동"
        if "완료 저장" in s:
            return s
        if "로그인" in s:
            return s
    return "수집 진행 중" if running else "중지"


def _log_serial(shard: int) -> str:
    path = RUNTIME / f"list_{shard}.log"
    if not path.is_file():
        return ""
    text = path.read_bytes()[-12000:].decode("utf-8", errors="replace")
    found = ""
    for line in text.splitlines():
        if "localhost:" not in line:
            continue
        if "ADB 재사용" not in line and "기기 " not in line:
            continue
        for part in line.split():
            if part.startswith("localhost:"):
                found = part.strip(".,")
    return found


def _adb_connected(serial: str, adb_port: str) -> bool:
    serial = (serial or "").strip()
    adb_port = (adb_port or "").strip()
    if not serial or not adb_port or not ADB.is_file():
        return False
    key = f"{adb_port}:{serial}"
    now = time.time()
    cached = _ADB_CACHE.get(key)
    if cached and now - cached[0] < 2.0:
        return cached[1]
    env = os.environ.copy()
    env["ANDROID_ADB_SERVER_PORT"] = adb_port
    ok = False
    try:
        out = subprocess.check_output(
            [str(ADB), "-P", adb_port, "-s", serial, "get-state"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=2,
            env=env,
        ).strip().lower()
        ok = out == "device"
    except (OSError, subprocess.SubprocessError):
        ok = False
    _ADB_CACHE[key] = (now, ok)
    return ok


def _pick_log(spec: dict, proc: dict | None) -> tuple[Path, str]:
    script = (proc or {}).get("script") or ""
    if "list" in script:
        return RUNTIME / f"list_{spec['shard']}.log", "List"
    if "rows25_27" in script:
        return RUNTIME / f"rows2527_{spec['shard']}.log", "row 25–27"
    if "row128" in script:
        return RUNTIME / f"row128_{spec['shard']}.log", "row_128"
    cands: list[tuple[float, Path, str]] = []
    for job, name in (
        ("List", f"list_{spec['shard']}.log"),
        ("row 25–27", f"rows2527_{spec['shard']}.log"),
        ("row_128", f"row128_{spec['shard']}.log"),
    ):
        path = RUNTIME / name
        if path.is_file():
            cands.append((path.stat().st_mtime, path, job))
    if not cands:
        return RUNTIME / f"rows2527_{spec['shard']}.log", "row 25–27"
    cands.sort()
    _, path, job = cands[-1]
    return path, job


def _phone(spec: dict, proc: dict | None) -> dict:
    path, job = _pick_log(spec, proc)
    text = _session_text(path)
    lines = text.splitlines()
    ok = [m.groups() for ln in lines if (m := _OK.search(ln))]
    miss = [m.groups() for ln in lines if (m := _MISS.search(ln))]
    saved = len(ok)
    missed = len(miss)
    name = spec["name"]
    log_serial = ""
    for ln in reversed(lines):
        dm = _DEVICE.search(ln)
        if dm:
            log_serial = dm.group(1)
            break
    saved_serial = (spec.get("serial") or "").strip()
    running = proc is not None
    if running and log_serial:
        serial = log_serial
    else:
        serial = saved_serial or log_serial
    uid = ""
    for m in _UID.finditer(text):
        if m.group(1) != "0":
            uid = m.group(1)
    pending = None
    tm = None
    for m in _TARGET.finditer(text):
        tm = m
    if tm:
        pending = int(tm.group(1))
    if not running and path.is_file():
        running = (time.time() - path.stat().st_mtime) < 20
    now = time.time()
    started = (proc or {}).get("started_at")
    elapsed = max(0.0, now - float(started)) if started else 0.0
    per_min = round(saved / (elapsed / 60.0), 2) if saved and elapsed > 1 else (0.0 if running else None)
    last = ""
    last_sec = None
    if ok:
        last = f"Saved i.{ok[-1][0]}.{ok[-1][1]}"
        last_sec = float(ok[-1][2])
    elif miss:
        last = f"Missed i.{miss[-1][0]}.{miss[-1][1]}"
        last_sec = float(miss[-1][2])
    done = saved + missed
    left = None if pending is None else max(0, pending - done)
    ok_rate = round(100.0 * saved / done, 1) if done else None
    tail = _tail_recent(lines)
    note = _note(lines, running)
    return {
        "shard": spec["shard"],
        "job": job,
        "name": name,
        "serial": serial,
        "port": spec["port"],
        "running": running,
        "userid": uid,
        "saved": saved,
        "miss": missed,
        "pending_total": pending,
        "pending_left": left,
        "elapsed_s": round(elapsed, 1),
        "per_min": per_min,
        "per_hour": round(per_min * 60.0, 1) if per_min is not None else None,
        "note": note,
        "state": _state(note, running),
        "last": last,
        "last_sec": last_sec,
        "ok_rate": ok_rate,
        "tail": tail,
        "started_at": started,
    }


def _lists() -> list:
    import task_lists
    try:
        return task_lists.catalog()
    except Exception:
        return []


def _arrival() -> dict:
    import harvest_control
    try:
        return harvest_control.arrival()
    except Exception:
        return {"count": 0, "arrived_at": ""}


def _list_log(shard: int, list_name: str) -> str:
    path = RUNTIME / f"list_{shard}.log"
    if not list_name or not path.is_file():
        return ""
    text = _decode(path)
    marker = f"---- list {list_name} shard {shard} ----"
    idx = text.rfind(marker)
    if idx < 0:
        return ""
    return text[idx:]


def _apply_selected_list(phones: list[dict]) -> None:
    import task_lists
    source = task_lists.selected_path()
    source_name = source.name if source is not None else ""
    marker = RUNTIME / "list_source.txt"
    running_source = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    for phone in phones:
        shard = int(phone["shard"])
        if not source_name:
            continue
        phone["job"] = "List"
        slice_text = _list_log(shard, source_name)
        on_this_list = phone["running"] and running_source == source_name
        if not slice_text:
            phone["miss"] = 0
            phone["last"] = ""
            phone["last_sec"] = None
            phone["tail"] = []
            phone["userid"] = ""
            phone["note"] = "중지"
            phone["state"] = "중지"
            phone["ok_rate"] = None
            if not on_this_list:
                phone["running"] = False
                phone["per_min"] = None
                phone["per_hour"] = None
                phone["elapsed_s"] = 0
            else:
                phone["per_min"] = 0.0
                phone["per_hour"] = 0.0
            continue
        lines = slice_text.splitlines()
        ok = [m for ln in lines if (m := _OK.search(ln))]
        miss = [m for ln in lines if (m := _MISS.search(ln))]
        phone["miss"] = len(miss)
        if ok:
            phone["last"] = f"Saved i.{ok[-1].group(1)}.{ok[-1].group(2)}"
            phone["last_sec"] = float(ok[-1].group(3))
        elif miss:
            phone["last"] = f"Missed i.{miss[-1].group(1)}.{miss[-1].group(2)}"
            phone["last_sec"] = float(miss[-1].group(3))
        else:
            phone["last"] = ""
            phone["last_sec"] = None
        uid = ""
        for found in _UID.finditer(slice_text):
            if found.group(1) != "0":
                uid = found.group(1)
        phone["userid"] = uid
        phone["tail"] = _tail_recent(lines)
        if not on_this_list:
            phone["running"] = False
            phone["per_min"] = None
            phone["per_hour"] = None
            phone["elapsed_s"] = 0
        phone["note"] = _note(lines, phone["running"])
        phone["state"] = _state(phone["note"], phone["running"])
        phone["ok_rate"] = None


def _refresh_snapshot_async() -> None:
    if not _SNAPSHOT_BUILD_LOCK.acquire(blocking=False):
        return
    try:
        payload = _build_snapshot()
        _SNAPSHOT_CACHE["payload"] = payload
        _SNAPSHOT_CACHE["at"] = time.time()
    finally:
        _SNAPSHOT_BUILD_LOCK.release()


def snapshot() -> dict:
    now = time.time()
    cached = _SNAPSHOT_CACHE.get("payload")
    age = now - float(_SNAPSHOT_CACHE.get("at") or 0)
    if cached is not None:
        if age >= _SNAPSHOT_STALE_S:
            threading.Thread(target=_refresh_snapshot_async, daemon=True).start()
        return cached
    if not _SNAPSHOT_BUILD_LOCK.acquire(blocking=False):
        if cached is not None:
            return cached
        _SNAPSHOT_BUILD_LOCK.acquire()
    try:
        cached = _SNAPSHOT_CACHE.get("payload")
        if cached is not None:
            return cached
        payload = _build_snapshot()
        _SNAPSHOT_CACHE["at"] = time.time()
        _SNAPSHOT_CACHE["payload"] = payload
        return payload
    finally:
        _SNAPSHOT_BUILD_LOCK.release()


def _build_snapshot() -> dict:
    global _PRUNE_AT
    import phone_fleet

    phone_fleet.ensure_fleet_runtime()
    procs: dict[int, dict] = {}
    for proc in _procs():
        prev = procs.get(proc["shard"])
        script = proc.get("script", "")
        if prev is None or "list" in script or ("rows25" in script and "list" not in prev.get("script", "")):
            procs[proc["shard"]] = proc
    spec_list = phones_live()
    spec_by_shard = {int(s["shard"]): s for s in spec_list}
    phones = [_phone(spec, procs.get(spec["shard"])) for spec in spec_list]
    for phone in phones:
        spec = spec_by_shard.get(int(phone["shard"]), {})
        serial = (phone.get("serial") or "").strip() or _log_serial(int(phone["shard"]))
        phone["adb_connected"] = _adb_connected(serial, spec.get("adb_port") or "")
    try:
        import phone_activity

        activity_by_name = {str(row["name"]): row for row in phone_activity.cards()}
    except Exception:
        activity_by_name = {}
    for phone in phones:
        act = activity_by_name.get(str(phone.get("name") or ""), {})
        phone["activity_mode"] = str(act.get("mode") or "idle")
        phone["region_progress"] = int(act.get("progress") or 0)
        phone["region_label"] = str(act.get("label") or "")
    _apply_selected_list(phones)
    import collect_layout
    import collect_stats
    import list_progress
    import task_lists
    if list_progress.pool_enabled():
        now_prune = time.time()
        if now_prune - _PRUNE_AT >= 45.0:
            list_progress.prune_saved_from_queues(ui_phone_slot_count())
            list_progress.withdraw_unregistered_phone_queues()
            _PRUNE_AT = now_prune
    slots = ui_phone_slot_count()
    workload = list_progress.workload_view(slots)
    for phone in phones:
        phone["list_pending"] = list_progress.queue_left(int(phone["shard"]))
    for row in workload.get("phones") or []:
        row["pending"] = list_progress.queue_left(int(row["shard"]))
    fresh_at = 0.0
    for phone in phones:
        shard = int(phone["shard"])
        stat = collect_stats.observe(shard, RUNTIME / f"list_{shard}.log")
        phone_fresh = float(stat.get("fresh_at") or 0)
        fresh_at = max(fresh_at, phone_fresh)
        phone["saved"] = int(stat["saved"])
        phone["miss"] = int(stat["miss"])
        phone["last"] = stat.get("last") or ""
        phone["last_sec"] = stat.get("last_sec")
        phone["_fresh_at"] = phone_fresh
        done = phone["saved"] + phone["miss"]
        phone["ok_rate"] = round(100.0 * phone["saved"] / done, 1) if done else None
    counted = phones
    saved = sum(p["saved"] for p in counted)
    missed = sum(p["miss"] for p in counted)
    running_n = sum(1 for p in phones if p["running"])
    now = time.time()
    elapsed = max(0.0, now - fresh_at) if running_n and fresh_at else 0.0
    for phone in phones:
        phone_elapsed = 0.0
        if phone["running"]:
            pf = float(phone.get("_fresh_at") or 0)
            phone_elapsed = max(0.0, now - pf) if pf else 0.0
        throughput = int(phone["saved"]) + int(phone["miss"])
        if phone["running"] and phone_elapsed > 1 and throughput > 0:
            phone["per_min"] = round(throughput / (phone_elapsed / 60.0), 2)
            phone["per_hour"] = round(phone["per_min"] * 60.0, 1)
        elif phone["running"]:
            phone["per_min"] = 0.0
            phone["per_hour"] = 0.0
        else:
            phone["per_min"] = None
            phone["per_hour"] = None
        phone["elapsed_s"] = round(phone_elapsed, 1) if phone["running"] else 0
        phone.pop("_fresh_at", None)
    for phone in phones:
        mode = str(phone.get("activity_mode") or "")
        if mode not in _OPS_ACTIVITY:
            continue
        act = activity_by_name.get(str(phone.get("name") or ""), {})
        spec = spec_by_shard.get(int(phone["shard"]), {})
        pad = str(spec.get("pad") or "")
        phone["running"] = False
        if mode == "region":
            phone["state"] = "지역변경중"
            step = str(act.get("label") or phone.get("region_label") or "").strip()
            phone["note"] = step if step else "지역변경중"
            phone["region_progress"] = int(act.get("progress") or phone.get("region_progress") or 0)
        elif mode == "reinstall":
            phone["state"] = "재설치중"
            phone["note"] = "Shopee 재설치"
            phone["region_progress"] = 0
        elif mode == "adb":
            phone["state"] = "ADB연결중"
            phone["note"] = "ADB 재연결"
            phone["region_progress"] = 0
        else:
            phone["state"] = "세션수집중"
            phone["note"] = "세션수집 — 로그인 대기"
            phone["region_progress"] = 0
        phone["saved"] = 0
        phone["miss"] = 0
        phone["last"] = ""
        phone["last_sec"] = None
        phone["tail"] = _ops_log_tail(str(phone.get("name") or ""), pad)
        phone["userid"] = ""
        phone["per_min"] = None
        phone["per_hour"] = None
        phone["elapsed_s"] = 0
        phone["ok_rate"] = None
    for phone in phones:
        if phone.get("running") or phone.get("activity_mode") in _OPS_ACTIVITY:
            continue
        phone["note"] = ""
        phone["tail"] = []
    running_n = sum(1 for p in phones if p["running"])
    saved = sum(p["saved"] for p in phones)
    missed = sum(p["miss"] for p in phones)
    per_min = round(sum(p["per_min"] or 0 for p in phones if p["running"]), 2) if running_n else None
    per_hour = round(per_min * 60.0, 1) if per_min is not None else None
    method = "Default method · fresh list · one extra pass for misses · no upstream proxy"
    subtitle = "List totals · per-phone job · refreshes every 2 seconds"
    try:
        import session_ledger

        session_ledger.enrich_phone_session_counts(phones)
    except Exception as exc:
        import logging

        logging.getLogger("status_server").warning("session counts: %s", exc)
        for phone in phones:
            phone["sessions_total"] = 0
            phone["sessions_available"] = 0
    for phone in phones:
        if phone.get("last"):
            phone["last"] = _last_ui(phone["last"])
        phone["captcha_waiting"] = _captcha_waiting(phone)
        phone["login_waiting"] = _login_waiting(phone)
    layout = collect_layout.load()
    upload_auto = (layout.get("mode") or "auto") == "auto"
    try:
        import sever_hub

        server_pulse = sever_hub.pulse()
    except Exception:
        server_pulse = {"sent": 0, "session_sent": 0, "bridge": False, "recent_sig": ""}
    return {
        "subtitle": subtitle,
        "method": method,
        "running": running_n > 0,
        "running_n": running_n,
        "phones_n": len(phones),
        "saved": saved,
        "miss": missed,
        "list_count": task_lists.live_count(),
        "list_workload": workload,
        "upload": {"mode": layout.get("mode") or "auto", "auto": upload_auto},
        "list_mode": layout.get("list_mode") or "mode2",
        "server_pulse": server_pulse,
        "elapsed_s": round(elapsed, 1),
        "per_min": per_min,
        "per_hour": per_hour,
        "phones": phones,
        "ui_phone_slots": slots,
        "folder": str(OUT),
        "arrival": _arrival(),
        "lists": _lists(),
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            data = {}
        return data if isinstance(data, dict) else {}

    def do_POST(self):
        try:
            import collect_layout
            if self.path.startswith("/api/collect/list"):
                import task_lists
                body = self._read_json()
                result = task_lists.select(str(body.get("file") or ""))
                self._json(200, result)
                return
            if self.path.startswith("/api/collect/fresh"):
                import collect_stats
                live = phones_live()
                logs = {int(spec["shard"]): RUNTIME / f"list_{spec['shard']}.log" for spec in live}
                collect_stats.fresh([int(spec["shard"]) for spec in live], logs)
                _invalidate_snapshot_cache()
                self._json(200, {"ok": True})
                return
            if self.path.startswith("/api/collect/reset"):
                if any(proc.get("script") and "list" in str(proc.get("script") or "") for proc in _procs()):
                    self._json(409, {"ok": False, "error": "Stop collection on all phones first."})
                    return
                import collect_stats
                import list_progress

                live = phones_live()
                nphones = ui_phone_slot_count()
                logs = {int(spec["shard"]): RUNTIME / f"list_{spec['shard']}.log" for spec in live}
                list_result = list_progress.reset_collection_state(nphones)
                collect_stats.fresh([int(spec["shard"]) for spec in live], logs)
                _PROC_CACHE["at"] = 0.0
                _invalidate_snapshot_cache()
                self._json(200, {"ok": True, "list": list_result})
                return
            if self.path.startswith("/api/collect/action"):
                import harvest_control
                body = self._read_json()
                action = str(body.get("action") or "")
                phone = str(body.get("phone") or "")
                if action == "start":
                    result = harvest_control.start(phone)
                elif action == "stop":
                    result = harvest_control.stop(phone)
                else:
                    result = {"ok": False, "error": "Unknown action."}
                _PROC_CACHE["at"] = 0
                _invalidate_snapshot_cache()
                self._json(200, result)
                return
            if self.path.startswith("/api/sessions/action"):
                import phone_activity
                body = self._read_json()
                phones = body.get("phones") if isinstance(body.get("phones"), list) else []
                action = str(body.get("action") or "")
                names = [str(name) for name in phones]
                if action == "stop_session":
                    result = phone_activity.stop_session(names)
                elif action in ("adb", "reinstall"):
                    result = phone_activity.start_tool(action, names)
                else:
                    result = phone_activity.start(action, names)
                _PROC_CACHE["at"] = 0
                _invalidate_snapshot_cache()
                self._json(200, result)
                return
            if self.path.startswith("/api/sever/list_mode"):
                lmode = str(self._read_json().get("list_mode") or "")
                saved = collect_layout.set_list_mode(lmode)
                _invalidate_snapshot_cache()
                self._json(200, {"list_mode": saved["list_mode"]})
                return
            if self.path.startswith("/api/sever/mode"):
                mode = str(self._read_json().get("mode") or "")
                saved = collect_layout.set_mode(mode)
                self._json(200, {"mode": saved["mode"]})
                return
            if self.path.startswith("/api/fleet/register"):
                import phone_fleet

                body = self._read_json()
                result = phone_fleet.register_phone(
                    pad=str(body.get("pad") or ""),
                    name=str(body.get("name") or ""),
                    shard=body.get("shard"),
                    port=str(body.get("port") or ""),
                    adb_port=str(body.get("adb_port") or body.get("adbPort") or ""),
                    serial=str(body.get("serial") or ""),
                )
                _invalidate_snapshot_cache()
                self._json(200, result)
                return
            if self.path.startswith("/api/list/transfer"):
                import list_progress
                body = self._read_json()
                action = str(body.get("action") or "")
                shard = int(body.get("shard") if body.get("shard") is not None else -1)
                count = int(body.get("count") or 0)
                nphones = ui_phone_slot_count()
                if action == "withdraw":
                    result = list_progress.transfer_withdraw(shard, count, nphones)
                elif action == "assign":
                    result = list_progress.transfer_assign(shard, count, nphones)
                elif action == "auto_distribute":
                    import phone_fleet

                    result = list_progress.auto_distribute_evenly(
                        phone_fleet.registered_phone_count() or nphones
                    )
                elif action == "clear_missed":
                    result = list_progress.clear_missed(nphones)
                elif action == "recollect_missed":
                    result = list_progress.recollect_missed(nphones)
                elif action == "recall_unregistered":
                    result = list_progress.withdraw_unregistered_phone_queues()
                else:
                    result = {"ok": False, "error": "Unknown action."}
                _invalidate_snapshot_cache()
                self._json(200, result)
                return
            if self.path.startswith("/api/sever/upload"):
                saved = collect_layout.request_upload()
                self._json(200, {"mode": saved["mode"], "upload_once": saved["upload_once"]})
                return
            self._json(404, {"error": "not found"})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_GET(self):
        try:
            self._do_GET()
        except Exception as exc:
            body = str(exc).encode("utf-8", errors="replace")
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    def _do_GET(self):
        if self.path.startswith("/api/status"):
            body = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/sessions"):
            import session_ledger
            import phone_activity
            payload = session_ledger.view()
            payload["cards"] = phone_activity.cards()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/sever"):
            import sever_hub
            body = json.dumps(sever_hub.view(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        body = _page_html().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _watch_session_limit() -> None:
    import phone_activity
    while True:
        try:
            phone_activity.stop_at_limit()
        except Exception:
            pass
        time.sleep(4)


def _watch_server_list() -> None:
    from datetime import datetime, timezone

    def _now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    while True:
        try:
            from _sever_bridge import _env, _get
            import task_lists

            env = _env()
            data = _get(
                f"http://{env['BR_MEMBER_HOST']}:{env['BR_MEMBER_PORT']}/v1/tasks?limit=15000",
                env["BR_MEMBER_KEY"],
            )
            raw = data.get("tasks") if isinstance(data, dict) else None
            tasks = [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []
            for row in tasks:
                shop, item = row.get("shop_id"), row.get("item_id")
                if not row.get("url") and shop and item:
                    row["url"] = f"https://shopee.com.br/product/{shop}/{item}"
            if tasks:
                task_lists.save_incoming(tasks, _now())
        except Exception:
            pass
        time.sleep(20)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    threading.Thread(target=_watch_session_limit, daemon=True).start()
    threading.Thread(target=_watch_server_list, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Open http://127.0.0.1:{port}/", flush=True)
    server.serve_forever()