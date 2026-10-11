# -*- coding: utf-8 -*-
"""Paste a live TW Shopee login cookie string -> sessions/gm_tw_live.json then health-check.

Usage:
  python _tw_import_cookies.py "SPC_EC=...; SPC_ST=...; SPC_U=...; ..."
  python _tw_import_cookies.py path\to\cookies.txt
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\Great Scraper\Great Scraper")
SESS = ROOT / "shopee_getpc_crawler" / "sessions"
OUT = SESS / "gm_tw_live.json"

KEEP = {
    "SPC_EC", "SPC_ST", "SPC_U", "SPC_F", "SPC_R_T_ID", "SPC_R_T_IV",
    "SPC_T_ID", "SPC_T_IV", "SPC_SI", "SPC_SEC_SI", "csrftoken",
    "AC_CERT_D", "AC_CERT_S", "AC_CERT_A", "SPC_CLIENTID", "REC_T_ID",
    "shopee_webUnique_ccd", "ds", "_sapid",
}


def parse_cookie_header(s: str) -> dict:
    out = {}
    for part in s.replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and v and v not in ("deleted",):
            out[k] = v
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: _tw_import_cookies.py <cookie-string|file>")
        return 2
    arg = sys.argv[1]
    p = Path(arg)
    raw = p.read_text(encoding="utf-8") if p.is_file() else arg
    # strip accidental URL line
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.startswith("http")]
    blob = " ".join(lines) if lines else raw
    ck = parse_cookie_header(blob)
    if not ck:
        print("no cookies parsed")
        return 3
    slim = {k: v for k, v in ck.items() if k in KEEP} or ck
    ec = (slim.get("SPC_EC") or "").strip()
    ok_login = bool(ec and ec not in ("-", "deleted")) or (
        bool(slim.get("SPC_ST")) and bool(slim.get("SPC_U"))
    )
    doc = {
        "account_name": "gm_tw_live",
        "domain": "shopee.tw",
        "region": "TW",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cookies": slim,
    }
    SESS.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {OUT}")
    print(f"keys={len(slim)} login_candidate={ok_login} U={slim.get('SPC_U')} EC={bool(ec and ec not in ('-','deleted'))}")
    if not ok_login:
        print("WARNING: missing SPC_EC / (SPC_ST+SPC_U) — health will fail")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
