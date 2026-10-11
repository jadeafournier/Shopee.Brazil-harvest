# -*- coding: utf-8 -*-
"""세션 수집 시작 시 전화번호 로그인 화면으로 이동하고, SMS 번호를 넣어 확인을 누른다."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ADB = str(ROOT / "emu_setup" / "platform-tools" / "adb.exe")
CRED = HERE / "smscode_credentials.json"
ORDERS = HERE / "sms_orders.jsonl"
MIN_PRICE = 1000
MAX_PRICE = 1200
_NAV_Y = 2050


def _fold(text: str) -> str:
    raw = (text or "").replace("’", "'").replace("‘", "'")
    raw = unicodedata.normalize("NFKD", raw)
    out = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return out.lower().replace("'", "")


def _port() -> str:
    return (os.environ.get("ANDROID_ADB_SERVER_PORT") or "5037").strip()


def _adb(serial: str, *args: str, timeout: int = 25, port: str | None = None) -> str:
    proc = subprocess.run(
        [ADB, "-P", port or _port(), "-s", serial, *args],
        capture_output=True,
        timeout=timeout,
    )
    return (proc.stdout or b"").decode("utf-8", "replace") + (proc.stderr or b"").decode("utf-8", "replace")


def _nodes(serial: str, port: str | None = None) -> list[dict]:
    _adb(serial, "shell", "uiautomator", "dump", "/sdcard/_ui.xml", timeout=25, port=port)
    xml = _adb(serial, "shell", "cat", "/sdcard/_ui.xml", timeout=15, port=port)
    rows = []
    for tag in re.findall(r"<node[^>]*>", xml):
        text = re.search(r'text="([^"]*)"', tag)
        desc = re.search(r'content-desc="([^"]*)"', tag)
        bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
        kind = re.search(r'class="([^"]*)"', tag)
        label = ((text.group(1) if text else "") or (desc.group(1) if desc else "")).strip()
        if not label or not bounds:
            continue
        x1, y1, x2, y2 = (int(bounds.group(i)) for i in range(1, 5))
        rows.append({
            "text": label,
            "fold": _fold(label),
            "class": (kind.group(1) if kind else "").rsplit(".", 1)[-1],
            "x": (x1 + x2) // 2,
            "y": (y1 + y2) // 2,
        })
    return rows


def _tap(serial: str, node: dict, port: str | None = None) -> None:
    _adb(serial, "shell", "input", "tap", str(node["x"]), str(node["y"]), port=port)


def _find(rows: list[dict], pred) -> dict | None:
    for row in rows:
        if pred(row):
            return row
    return None


def _password_screen(rows: list[dict]) -> bool:
    """첫 화면. Senha 가 있고 Próximo 가 없다."""
    return _find(rows, lambda row: row["fold"] == "senha") is not None and _find(rows, lambda row: row["fold"] == "proximo") is None


def _sms_switch(rows: list[dict]) -> dict | None:
    """첫 화면의 Fazer login com SMS. 비밀번호 링크(Com Senha)는 제외한다."""
    return _find(rows, lambda row: "login com sms" in row["fold"] and "senha" not in row["fold"])


def _on_phone_screen(rows: list[dict]) -> dict | None:
    """두 번째 화면. Próximo 가 보이고 비밀번호 칸이 없다."""
    if _password_screen(rows) or not _find(rows, lambda row: row["fold"] == "proximo"):
        return None
    return _find(rows, lambda row: row["class"] == "EditText")


def _pregrant(serial: str, port: str | None, log) -> None:
    """권한 창은 탭이 먹지 않으므로, 미리 허용한 뒤 앱을 다시 연다."""
    pkg = "com.shopee.br"
    for perm in (
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.ACCESS_FINE_LOCATION",
    ):
        _adb(serial, "shell", "pm", "grant", pkg, perm, port=port)
    _adb(serial, "shell", "am", "force-stop", "com.android.permissioncontroller", port=port)
    _adb(serial, "shell", "am", "force-stop", pkg, port=port)
    time.sleep(0.8)
    _adb(serial, "shell", "am", "start", "-n", f"{pkg}/com.shopee.app.ui.home.HomeActivity_", port=port)
    log("  위치·알림 권한 허용 후 앱 재실행")
    time.sleep(2.5)


def open_phone_login(serial: str, log, port: str | None = None) -> list[dict]:
    """COMEÇAR 이나 다른 안내를 넘기고 Número de telefone 화면에서 멈춘다."""
    _pregrant(serial, port, log)
    for attempt in range(16):
        rows = _nodes(serial, port)
        field = _on_phone_screen(rows)
        if field:
            log("  전화번호 화면 (Próximo)")
            return rows
        sms = _sms_switch(rows) if _password_screen(rows) else None
        if sms:
            log(f"  비밀번호 화면에서 SMS 화면으로 {sms['x']},{sms['y']}")
            _tap(serial, sms, port)
            time.sleep(1.2)
            continue
        start = _find(rows, lambda row: row["fold"] == "comecar")
        entrar = _find(rows, lambda row: row["fold"] == "entrar" and row["y"] > 400)
        if entrar is None:
            entrar = _find(rows, lambda row: row["fold"] == "entrar")
        cookies = _find(rows, lambda row: "aceitar todos" in row["fold"])
        deny = _find(rows, lambda row: "dont allow" in row["fold"] or "nao permitir" in row["fold"])
        allow_loc = _find(rows, lambda row: "while using" in row["fold"])
        me = _find(rows, lambda row: row["fold"] == "eu")
        retry = _find(rows, lambda row: "tente novamente" in row["fold"] or "try again" in row["fold"])
        signup = _find(rows, lambda row: row["fold"] == "ok" and any("cadastre" in item["fold"] or "para executar" in item["fold"] for item in rows))
        if signup:
            log(f"  가입 안내 확인 {signup['x']},{signup['y']}")
            _tap(serial, signup, port)
        elif cookies:
            log(f"  쿠키 동의 {cookies['x']},{cookies['y']}")
            _tap(serial, cookies, port)
        elif allow_loc:
            log(f"  위치 허용 {allow_loc['x']},{allow_loc['y']}")
            _tap(serial, allow_loc, port)
        elif deny:
            log(f"  알림 거절 {deny['x']},{deny['y']}")
            _tap(serial, deny, port)
        elif start:
            log(f"  COMEÇAR {start['x']},{start['y']}")
            _tap(serial, start, port)
        elif entrar:
            log(f"  Entrar {entrar['x']},{entrar['y']}")
            _tap(serial, entrar, port)
        elif me:
            log(f"  Eu 탭 {me['x']},{me['y']}")
            _tap(serial, me, port)
        elif retry:
            log(f"  다시 시도 {retry['x']},{retry['y']}")
            _tap(serial, retry, port)
        else:
            shown = " | ".join(row["text"] for row in rows[:12]) or "빈 화면"
            log(f"  로그인 화면 대기 {attempt + 1} ({shown})")
        time.sleep(2.5)
    raise RuntimeError("전화번호 로그인 화면으로 이동하지 못함")


def _api(method: str, path: str, body: dict | None = None, key: str | None = None) -> dict:
    token = json.loads(CRED.read_text(encoding="utf-8"))["token"]
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "User-Agent": "GreatScraper/1.0",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request("https://api.smscode.gg/v1" + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"SMS API {exc.code} {detail}") from None


def _national(phone: str) -> str:
    """국가번호 55 를 유지한다. 빼면 입력창이 679 같은 다른 나라로 읽는다."""
    return re.sub(r"\D", "", phone or "")


def buy_brazil_shopee(log) -> dict:
    """가격 1000-1030 인 브라질 Shopee 번호를 하나 산다."""
    catalog = _api("GET", "/catalog/countries")
    brazil = next(
        row for row in (catalog.get("data") or [])
        if str(row.get("code") or "").upper() == "BR"
    )
    services = _api_get("/catalog/services", {"country_id": brazil["id"]})
    shopee = next(
        row for row in (services.get("data") or [])
        if "shopee" in str(row.get("name") or "").lower() or str(row.get("code") or "").lower() == "shopee"
    )
    found = None
    page = 1
    while page <= 20 and found is None:
        raw = _api_get(
            "/catalog/products",
            {
                "country_id": brazil["id"],
                "platform_id": shopee["id"],
                "sort": "price_asc",
                "limit": 1000,
                "page": page,
            },
        )
        rows = raw.get("data") or []
        for row in rows:
            try:
                price = int(row.get("price"))
            except (TypeError, ValueError):
                continue
            if MIN_PRICE <= price <= MAX_PRICE and row.get("active") and int(row.get("available") or 0) > 0:
                found = row
                break
        if len(rows) < 1000:
            break
        page += 1
    if not found:
        raise RuntimeError(f"가격 {MIN_PRICE}-{MAX_PRICE} 브라질 Shopee 번호가 없습니다")
    created = _api(
        "POST",
        "/orders/create",
        {"product_id": int(found["id"]), "quantity": 1},
        key="br-shopee-" + uuid.uuid4().hex,
    )
    order = ((created.get("data") or {}).get("orders") or [None])[0]
    if not order or not order.get("phone_number"):
        raise RuntimeError("번호 구매 응답에 전화번호가 없습니다")
    order["paid_price"] = int(found["price"])
    log(f"  번호 구매 {order['phone_number']} 가격 {order['paid_price']} 주문 {order.get('id')}")
    with ORDERS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "order_id": order.get("id"),
            "phone_number": order.get("phone_number"),
            "price": order["paid_price"],
            "saved_at": time.time(),
        }, ensure_ascii=False) + "\n")
    return order


def _api_get(path: str, query: dict) -> dict:
    import urllib.parse
    return _api("GET", path + "?" + urllib.parse.urlencode(query))


def _shown_digits(rows: list[dict]) -> str:
    field = _find(rows, lambda row: row["class"] == "EditText")
    if not field:
        return ""
    return re.sub(r"\D", "", field.get("text") or "")


def confirm_number(serial: str, digits: str, log, port: str | None = None) -> None:
    """두 번째 화면에 번호만 넣는다. Próximo 는 누르지 않는다."""
    for _cycle in range(3):
        rows = _nodes(serial, port)
        if _password_screen(rows):
            sms = _sms_switch(rows)
            if not sms:
                raise RuntimeError("Fazer login com SMS 를 찾지 못함")
            log(f"  비밀번호 화면에서 SMS 화면으로 {sms['x']},{sms['y']}")
            _tap(serial, sms, port)
            time.sleep(1.2)
            continue
        field = _on_phone_screen(rows)
        if not field:
            raise RuntimeError("전화번호 화면(Próximo)을 찾지 못함")
        if _shown_digits(rows) != digits:
            log(f"  번호 입력 {digits}")
            _tap(serial, field, port)
            time.sleep(0.3)
            _adb(serial, "shell", "input", "keyevent", "123", *["67"] * 18, port=port)
            time.sleep(0.2)
            _adb(serial, "shell", "input", "text", digits, port=port)
        for _ in range(12):
            time.sleep(0.3)
            fresh = _nodes(serial, port)
            if _password_screen(fresh):
                break
            if _shown_digits(fresh) == digits and _find(fresh, lambda row: row["fold"] == "proximo"):
                log("  번호만 입력함. Próximo 는 누르지 않음")
                return
        else:
            break
    raise RuntimeError("번호를 입력하지 못함")


def submit_bought_number(serial: str, log, port: str | None = None) -> str:
    """로그인 화면으로 이동한 뒤 구매한 번호를 넣는다. 다음 단추는 누르지 않는다."""
    open_phone_login(serial, log, port)
    order = buy_brazil_shopee(log)
    digits = _national(str(order.get("phone_number") or ""))
    if len(digits) < 10:
        raise RuntimeError(f"전화번호가 짧습니다: {digits}")
    confirm_number(serial, digits, log, port)
    return digits
