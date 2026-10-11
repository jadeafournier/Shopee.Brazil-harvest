# -*- coding: utf-8 -*-
"""VMOS에 열린 상품 1건의 get_pc JSON 저장."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from br_pdp_methods import classify_api_url, is_ok_payload, match_item_id  # noqa: E402
from client5_cat721_json_capture import Capture721  # noqa: E402
from client5_related_week import (  # noqa: E402
    COOKIE_TXT, parse_cookie_text, pause, traffic_challenge, warm_home,
)

SHOP, ITEM = 1119985490, 21299840174
URL = f"https://shopee.com.br/-i.{SHOP}.{ITEM}"
DEST = Path(__file__).resolve().parent / f"product_{SHOP}_{ITEM}.json"
WAIT_S = 36.0


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    cookies = parse_cookie_text(COOKIE_TXT.read_text(encoding="utf-8"))
    job = Capture721(cookies, {"cate_l3_nm": "vmos_open", "excel_row": 0})
    holder = {"get_pc": None, "raw": None, "hard": None, "seen": []}

    def on_pdp(r):
        u = r.url or ""
        kind = classify_api_url(u)
        if "get_pc" in u or "get_rw" in u or "item/get" in u:
            holder["seen"].append(u[:180])
        if not kind or str(ITEM) not in u:
            return
        try:
            j = json.loads(r.text())
        except Exception:
            return
        err = j.get("error") if isinstance(j, dict) else None
        if err in (90309999, "90309999"):
            holder["hard"] = "blocked_90309999"
            return
        if not is_ok_payload(j):
            return
        data = j["data"]
        if kind in ("get_pc", "get_rw") and not match_item_id(data, ITEM):
            return
        if holder.get("get_pc") is None:
            holder["get_pc"] = data
            holder["raw"] = j
            holder["kind"] = kind

    rec = {"shop_id": SHOP, "item_id": ITEM, "url": URL, "ok": False}
    try:
        job.start()
        job.page.on("response", on_pdp)
        warm_home(job.page)
        print(f"goto {URL}", flush=True)
        job.page.goto(URL, wait_until="commit", timeout=80000)
        t0 = time.time()
        while time.time() - t0 < WAIT_S and not holder["get_pc"] and not holder["hard"]:
            if "/verify/traffic" in (job.page.url or ""):
                traffic_challenge(job.page, 12.0)
            else:
                try:
                    job.page.mouse.wheel(0, 400)
                except Exception:
                    pass
                pause(job.page, 400, 800)
        if not holder["get_pc"] and not holder["hard"]:
            print("reload once", flush=True)
            job.page.reload(wait_until="commit", timeout=80000)
            t1 = time.time()
            while time.time() - t1 < WAIT_S and not holder["get_pc"] and not holder["hard"]:
                pause(job.page, 400, 800)
        rec["landed"] = job.page.url
        rec["seen"] = holder["seen"]
        rec["hard"] = holder["hard"]
        if holder["raw"]:
            DEST.write_text(
                json.dumps(holder["raw"], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            rec["ok"] = True
            rec["kind"] = holder.get("kind")
            rec["file"] = str(DEST)
            rec["item_keys"] = len((holder["get_pc"] or {}).get("item") or {})
    finally:
        job.close()
    print(json.dumps({k: rec[k] for k in rec if k != "seen" or rec.get("ok")}, ensure_ascii=False, indent=2))
    if rec.get("seen") and not rec.get("ok"):
        print("seen", rec["seen"], flush=True)
    return 0 if rec.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
