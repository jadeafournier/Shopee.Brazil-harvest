# -*- coding: utf-8 -*-
"""브라질 Shopee 상품 중 가격 1000-1200 을 찾고, 폰 화면 글자를 남긴다."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRED = HERE / "smscode_credentials.json"
OUT = HERE / "_smscode_br_shopee.json"
ADB = Path(r"D:\Luxury\Great Scraper2\Great Scraper\emu_setup\platform-tools\adb.exe")
TOKEN = json.loads(CRED.read_text(encoding="utf-8"))["token"]


def api(path: str, query: dict | None = None) -> dict:
    url = "https://api.smscode.gg/v1" + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Accept": "application/json",
            "User-Agent": "GreatScraper/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} {path} {body}") from None


def main() -> int:
    countries = api("/catalog/countries").get("data") or []
    brazil = [
        c for c in countries
        if str(c.get("code") or "").upper() in ("BR", "BRA")
        or "brazil" in str(c.get("name") or "").lower()
        or "brasil" in str(c.get("name") or "").lower()
    ]
    services = []
    products = []
    if brazil:
        cid = brazil[0]["id"]
        services = api("/catalog/services", {"country_id": cid}).get("data") or []
        shopee = [
            s for s in services
            if "shopee" in str(s.get("name") or "").lower()
            or "shopee" in str(s.get("code") or "").lower()
        ]
        for svc in shopee:
            page = 1
            while page <= 20:
                raw = api(
                    "/catalog/products",
                    {
                        "country_id": cid,
                        "platform_id": svc["id"],
                        "sort": "price_asc",
                        "limit": 1000,
                        "page": page,
                    },
                )
                rows = raw.get("data") or []
                products.extend(rows)
                if len(rows) < 1000:
                    break
                page += 1
    band = []
    for row in products:
        try:
            price = int(row.get("price"))
        except (TypeError, ValueError):
            continue
        if 1000 <= price <= 1030 and row.get("active") and int(row.get("available") or 0) > 0:
            band.append(
                {
                    "id": row.get("id"),
                    "catalog_product_id": row.get("catalog_product_id"),
                    "name": row.get("name"),
                    "price": price,
                    "available": row.get("available"),
                    "operator_name": row.get("operator_name"),
                }
            )
    prices = sorted({int(r.get("price")) for r in products if str(r.get("price") or "").isdigit() or isinstance(r.get("price"), int)})
    phones = []
    for port, serial, name in (
        ("5071", "localhost:64759", "Phone3"),
        ("5072", "localhost:55949", "Phone2"),
        ("5073", "localhost:53463", "Phone1"),
    ):
        env = os.environ.copy()
        env["ANDROID_ADB_SERVER_PORT"] = port
        subprocess.run([str(ADB), "-s", serial, "shell", "uiautomator", "dump", "/sdcard/_ui.xml"], capture_output=True, text=True, timeout=25, env=env)
        cat = subprocess.run([str(ADB), "-s", serial, "shell", "cat", "/sdcard/_ui.xml"], capture_output=True, text=True, timeout=15, env=env)
        xml = cat.stdout or ""
        import re
        nodes = []
        for m in re.finditer(r'<node[^>]*>', xml):
            tag = m.group(0)
            text = re.search(r'text="([^"]*)"', tag)
            desc = re.search(r'content-desc="([^"]*)"', tag)
            bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
            cls = re.search(r'class="([^"]*)"', tag)
            label = ((text.group(1) if text else "") or (desc.group(1) if desc else "")).strip()
            if not label or not bounds:
                continue
            x1, y1, x2, y2 = (int(bounds.group(i)) for i in range(1, 5))
            nodes.append({
                "text": label,
                "class": (cls.group(1) if cls else "").split(".")[-1],
                "x": (x1 + x2) // 2,
                "y": (y1 + y2) // 2,
            })
        phones.append({"name": name, "nodes": nodes[:30]})
    OUT.write_text(
        json.dumps(
            {
                "brazil": [{"id": c.get("id"), "code": c.get("code"), "name": c.get("name"), "dial": c.get("dial_code")} for c in brazil],
                "shopee_services": [{"id": s.get("id"), "code": s.get("code"), "name": s.get("name")} for s in services if "shopee" in str(s.get("name") or "").lower() or "shopee" in str(s.get("code") or "").lower()],
                "product_count": len(products),
                "prices": prices[:40],
                "band": band,
                "phones": phones,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
