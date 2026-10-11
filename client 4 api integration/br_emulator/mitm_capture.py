# -*- coding: utf-8 -*-
"""mitmproxy 애드온 — 에뮬레이터 Shopee 앱의 자체 서명 API 응답을 캡처.

원리(sign_oracle 와 동일하나 '네이티브 앱'판):
  실기기/에뮬 안의 Shopee 앱은 get_pc/item_get 등을 호출할 때 앱이 스스로
  x-sap-sec, af-ac-enc-dat 서명을 붙인다. 우리는 서명을 위조하지 않고, 앱이
  이미 붙인 정상 요청의 **응답**을 프록시에서 가로채 그대로 저장한다.
  → 90309999(need_app_sign) 벽을 앱 서명으로 우회(정상 트래픽 캡처).

실행:
  .venv/Scripts/mitmdump.exe -s br_emulator/mitm_capture.py --set stream_large_bodies=1m

옵션(환경변수):
  BR_EMU_HARVEST_URLS=1   리스팅(search/recommend) 응답에서 상품 URL 추가 수확
  BR_EMU_INJECT_COOKIES=<path>  차단 쿠키 jar(JSON) 를 웹뷰 API 요청 Cookie 헤더에 주입
  BR_EMU_VERBOSE=1        상세 로그

이 애드온은 mitmproxy 의 파이썬에서 로드되므로 반드시 우리 .venv 의 mitmdump 로 실행.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
for _p in (str(_DIR), str(_DIR.parent), str(_DIR.parent.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from br_emulator import _save  # noqa: E402

# get_pc/get_rw/item_get 는 완전 PDP. search/recommend 는 리스팅(URL 수확용).
# 웹 BFF 는 get_pc/get_rw, 앱 네이티브는 /api/v4/pdp/get?...
PDP_MARKERS = (
    "/api/v4/pdp/get_pc",
    "/api/v4/pdp/get_rw",
    "/api/v4/pdp/get?",
    "/api/v4/item/get",
)
LISTING_MARKERS = ("/api/v4/search/search_items", "/api/v4/recommend/recommend",
                   "rcmd_items", "search_items")
# PDP 가 status 200 + 이 error + data=null 로 응답하면 상품 삭제/없음.
PDP_GONE_ERRORS = (266900002,)

HARVEST_URLS = os.environ.get("BR_EMU_HARVEST_URLS") == "1"
VERBOSE = os.environ.get("BR_EMU_VERBOSE") == "1"
_INJECT_PATH = os.environ.get("BR_EMU_INJECT_COOKIES") or ""

URL_FILE = _save.CLIENT4 / "br_active_product_urls_100k.txt"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _log(msg: str) -> None:
    try:
        print(f"[emu-mitm] {msg}", flush=True)
    except Exception:
        print(f"[emu-mitm] {msg.encode('ascii', 'replace').decode('ascii')}", flush=True)


def _decode_block_meta(payload: dict):
    """90309999 응답의 안티크롤러 메타(JSON)를 복원. 실패 시 None."""
    import base64
    try:
        raw = payload.get("6")
        blob = raw.get("1") if isinstance(raw, dict) else None
        if not isinstance(blob, str):
            return None
        idx = blob.find("eyJ")  # base64('{"') 위치부터가 실제 payload
        if idx < 0:
            return None
        b = blob[idx:]
        b += "=" * (-len(b) % 4)
        return json.loads(base64.b64decode(b).decode("utf-8", "replace"))
    except Exception:
        return None


class ShopeeCapture:
    def __init__(self) -> None:
        self.saved = 0
        self.listing = 0
        self.harvested = 0
        self._url_keys: set[tuple[int, int]] = set()
        self._inject_cookie_header = ""
        if _INJECT_PATH:
            self._load_inject_cookies(_INJECT_PATH)
        self._load_existing_url_keys()
        _log("애드온 로드 완료 — get_pc/get_rw/item_get 응답 캡처 대기")
        if self._inject_cookie_header:
            _log("차단 쿠키 주입 활성 (웹뷰 API 요청 Cookie 보강)")
        if HARVEST_URLS:
            _log("리스팅 URL 수확 활성")

    # --- setup -----------------------------------------------------------
    def _load_inject_cookies(self, path: str) -> None:
        try:
            raw = Path(path).read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                jar = data.get("cookies") if isinstance(data.get("cookies"), dict) else data
                self._inject_cookie_header = "; ".join(
                    f"{k}={v}" for k, v in jar.items() if v and v not in ("-", "deleted")
                )
        except Exception as e:
            _log(f"쿠키 주입 로드 실패: {e}")

    def _load_existing_url_keys(self) -> None:
        try:
            if URL_FILE.is_file():
                for ln in URL_FILE.read_text(encoding="utf-8").splitlines():
                    ids = _save.parse_ids(ln.strip())
                    if ids:
                        self._url_keys.add(ids)
        except Exception:
            pass

    # --- mitmproxy hooks -------------------------------------------------
    def request(self, flow) -> None:
        host = flow.request.pretty_host or ""
        path = flow.request.path or ""
        if "native/homepage" not in path and ("shopee" in host or "product" in path):
            _log(f"REQ {flow.request.method} {host}{path[:180]}")
        if not self._inject_cookie_header:
            return
        if not any(h in host for h in ("shopee.com.br", "shopee.co.id", "mall.shopee")):
            return
        if "/api/v" not in flow.request.path:
            return
        # 웹뷰 API 요청에만 차단 쿠키를 보강(네이티브 앱 인증은 건드리지 않음).
        existing = flow.request.headers.get("Cookie", "")
        if "SPC_U=" in existing:
            return
        merged = (existing + "; " + self._inject_cookie_header).strip("; ")
        flow.request.headers["Cookie"] = merged

    def error(self, flow) -> None:
        req = getattr(flow, "request", None)
        url = (req.pretty_url if req is not None else "") or ""
        err = getattr(flow, "error", None)
        msg = getattr(err, "msg", None) or err or ""
        _log(f"ERR {msg} {url[:200]}")

    def response(self, flow) -> None:
        try:
            url = flow.request.pretty_url or ""
            host = flow.request.pretty_host or ""
        except Exception:
            return
        # 「Página indisponível」= WebPageActivity 의 verify/traffic/error.
        # 호스트 필터보다 먼저 남겨, 이 화면이 뜨면 수집이 바로 새 로그인으로 가게 한다.
        if "verify/traffic/error" in url:
            _log(f"TRAFFIC-ERROR {url[:240]}")
        if not any(h in host for h in ("shopee.com.br", "shopee.co.id", "mall.shopee")):
            return
        if "get_login_nonce" in url and flow.response is not None:
            try:
                raw = flow.response.get_text(strict=False) or ""
                _log(f"get_login_nonce body {raw[:400]}")
            except Exception as exc:
                _log(f"get_login_nonce read {exc}")
        if "cookie_get_preferences" in url and flow.response is not None:
            try:
                raw = flow.response.get_text(strict=False) or ""
                pref = json.loads(raw) if raw else None
            except Exception:
                pref = None
            if isinstance(pref, dict) and isinstance(pref.get("data"), dict):
                pref["data"]["need_consent"] = False
                items = pref["data"].get("preferences")
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, dict):
                            it["status"] = 1
                flow.response.set_text(json.dumps(pref, ensure_ascii=False, separators=(",", ":")))
                _log("cookie_get_preferences — 동의 배너 숨김")

        is_pdp = any(m in url for m in PDP_MARKERS)
        is_listing = any(m in url for m in LISTING_MARKERS)
        is_api = "/api/v4/" in url or "/api/v2/" in url
        is_login = any(m in url for m in ("/account/basic/get_account_info", "/native/homepage"))
        is_captcha = "captcha" in url or "anti_crawler" in url or "anti_fraud" in url
        if is_captcha:
            _log(f"API captcha st={flow.response.status_code if flow.response else '-'} {url[:180]}")
        if not (is_pdp or is_listing or is_login or is_captcha or (VERBOSE and is_api)):
            return
        if flow.response is None:
            return
        status = flow.response.status_code
        try:
            body = flow.response.get_text(strict=False)
            payload = json.loads(body) if body else None
        except Exception:
            payload = None
            body = None
        if is_pdp or is_login or (VERBOSE and is_api):
            err = payload.get("error") if isinstance(payload, dict) else None
            _log(f"API st={status} err={err} {url[:160]}")
            # 90309999(안티크롤러) 차단 메타데이터를 노출한다.
            # payload["6"]["1"] = "mfr" + base64(JSON: business/mfr/mfr_captcha/pop_up)
            if is_pdp and err == 90309999 and isinstance(payload, dict):
                meta = _decode_block_meta(payload)
                if meta is not None:
                    pop = meta.get("pop_up")
                    cap = bool(meta.get("mfr_captcha"))
                    biz = meta.get("business")
                    _log(f"PDP-BLOCK business={biz} pop_up={pop} captcha={cap} mfr={meta.get('mfr')}")
            # 상품 삭제/없음: status 200 + item-not-found 에러 + data 없음.
            if (
                is_pdp
                and err in PDP_GONE_ERRORS
                and isinstance(payload, dict)
                and payload.get("data") is None
            ):
                _log(f"PDP-GONE item_not_found err={err} {url[:120]}")
            # Always dump PDP-ish responses for debugging (incl. errors).
            if is_pdp or (isinstance(payload, dict) and ("item" in (payload.get("data") or {}) or "get_pc" in url or "item/get" in url)):
                try:
                    dump_dir = _save.CLIENT4 / "br_deliverable_getpc" / "_mitm_debug"
                    dump_dir.mkdir(parents=True, exist_ok=True)
                    ids = _save.parse_ids(url) or (0, 0)
                    dump_path = dump_dir / f"debug_{ids[0]}_{ids[1]}_{status}_{err}.json"
                    dump_path.write_text(
                        json.dumps({"url": url, "status": status, "payload": payload}, ensure_ascii=False, indent=2)[:2_000_000],
                        encoding="utf-8",
                    )
                    _log(f"debug dump {dump_path.name}")
                except Exception as e:
                    _log(f"debug dump fail: {e}")
        if status != 200:
            return
        if not isinstance(payload, dict):
            return
        err = payload.get("error")
        if err not in (0, None):
            if VERBOSE:
                _log(f"응답 error={err} — skip save {url[:120]}")
            return

        if is_pdp:
            self._handle_pdp(url, payload)
        elif is_listing and HARVEST_URLS:
            self._handle_listing(payload)

    # --- handlers --------------------------------------------------------
    def _handle_pdp(self, url: str, payload: dict) -> None:
        data = payload.get("data")
        if not isinstance(data, dict) or not data:
            return
        ids = _save.parse_ids(url)
        prod_url = _save.product_url(*ids) if ids else url
        if "get_pc" in url:
            src = "emulator_get_pc"
        elif "get_rw" in url:
            src = "emulator_get_rw"
        elif "/pdp/get" in url:
            src = "emulator_pdp_get"
        else:
            src = "emulator_item_get"
        path = _save.save_product(prod_url, payload, source=src)
        if path:
            self.saved += 1
            item = data.get("item") if isinstance(data.get("item"), dict) else {}
            title = str((item or {}).get("title") or (item or {}).get("name") or "")[:50]
            _log(f"✅ 저장 #{self.saved} [{src}] {title!r}")

    def _handle_listing(self, payload: dict) -> None:
        self.listing += 1
        pairs: set[tuple[int, int]] = set()
        self._walk_pairs(payload, pairs)
        new = 0
        lines = []
        for sid, iid in pairs:
            if (sid, iid) in self._url_keys:
                continue
            self._url_keys.add((sid, iid))
            lines.append(_save.product_url(sid, iid))
            new += 1
        if lines:
            try:
                with URL_FILE.open("a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                self.harvested += new
                _log(f"🔗 리스팅 URL +{new} (누적 수확 {self.harvested})")
            except Exception:
                pass

    def _walk_pairs(self, obj, out: set) -> None:
        if isinstance(obj, dict):
            sid = obj.get("shopid") or obj.get("shop_id")
            iid = obj.get("itemid") or obj.get("item_id")
            if isinstance(sid, int) and isinstance(iid, int) and sid > 0 and iid > 0:
                out.add((sid, iid))
            for v in obj.values():
                self._walk_pairs(v, out)
        elif isinstance(obj, list):
            for v in obj:
                self._walk_pairs(v, out)

    def done(self) -> None:
        _log(f"종료 — PDP 저장 {self.saved} | 리스팅 {self.listing} | URL 수확 {self.harvested}")


addons = [ShopeeCapture()]
