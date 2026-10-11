# -*- coding: utf-8 -*-
"""Shopee 모바일/iOS 스타일 API 클라이언트.

Client 1 기술팀이 쓰는 'iOS APP 방안'과 동일한 계열의 요청 형태를 흉내냅니다.
- User-Agent: iOS Shopee / React Native Beeshop
- X-API-SOURCE: rn (앱) 또는 rweb
- 엔드포인트: item/get, pdp/get_rw, pdp/get_pc

주의: 서버가 앱 서명(x-sap-sec 등)을 강제하면 쿠키만으로는 90309999 가 납니다.
그 경우 이 클라이언트는 status=need_app_sign 을 반환하고, 상위 플릿이
브라우저 get_pc 로 폴백합니다. 서명 위조는 하지 않습니다.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

from curl_cffi import requests as curlrq

# --- iOS / RN User-Agents ----------------------------------------------------
UA_IOS_SHOPEE = (
    "Shopee Beeshop locale/pt-BR version=34017 "
    "iOS/17.4.1 iPhone14,2 "
    "CFNetwork/1494.0.7 Darwin/23.4.0"
)
UA_IOS_SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
    "Mobile/15E148 Safari/604.1"
)
UA_RN_ANDROID = (
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/131.0.0.0 Mobile Safari/537.36 "
    "Shopee Beeshop locale/pt-BR version=34017"
)

REGION_HOST = {
    "BR": "https://shopee.com.br",
    "TW": "https://shopee.tw",
    "SG": "https://shopee.sg",
    "MY": "https://shopee.com.my",
    "PH": "https://shopee.ph",
    "VN": "https://shopee.vn",
    "TH": "https://shopee.co.th",
    "ID": "https://shopee.co.id",
}

LANG = {
    "BR": ("pt-BR", "pt-BR,pt;q=0.9,en;q=0.8"),
    "TW": ("zh-Hant", "zh-TW,zh;q=0.9,en;q=0.8"),
}


@dataclass
class MobileResult:
    ok: bool
    status: str
    endpoint: str
    http: int = 0
    error: Any = None
    data: Optional[dict] = None
    elapsed_s: float = 0.0
    title: str = ""
    data_keys: int = 0
    item_keys: int = 0
    notes: list[str] = field(default_factory=list)

    def summary_ko(self) -> str:
        if self.ok:
            return (
                f"성공 | {self.endpoint} | HTTP {self.http} | "
                f"섹션={self.data_keys} | 상품키={self.item_keys} | "
                f"{self.title[:40]!r} | {self.elapsed_s:.1f}s"
            )
        return (
            f"실패 | {self.status} | {self.endpoint} | "
            f"HTTP {self.http} | err={self.error} | {self.elapsed_s:.1f}s"
        )


def _cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v and v not in ("-", "deleted"))


def build_headers(
    region: str,
    *,
    shop_id: int,
    item_id: int,
    cookies: dict,
    profile: str = "ios",
    api_source: str = "rn",
) -> dict[str, str]:
    host = REGION_HOST.get(region.upper(), REGION_HOST["BR"])
    lang, accept_lang = LANG.get(region.upper(), LANG["BR"])
    if profile == "ios":
        ua = UA_IOS_SHOPEE
    elif profile == "ios_safari":
        ua = UA_IOS_SAFARI
    else:
        ua = UA_RN_ANDROID
    referer = f"{host}/product/{shop_id}/{item_id}"
    h = {
        "User-Agent": ua,
        "Accept": "application/json",
        "Accept-Language": accept_lang,
        "Referer": referer,
        "Origin": host,
        "X-API-SOURCE": api_source,
        "X-Shopee-Language": lang,
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFTOKEN": cookies.get("csrftoken", ""),
        "Cookie": _cookie_header(cookies),
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    return h


class IosMobileClient:
    """쿠키 + 모바일/iOS 헤더로 Shopee 상품 API 호출.

    signer: 선택. Client 1식 iOS 서명기가 있으면
      signer(url, headers, cookies) -> headers  형태로 주입.
      예: Frida/실기기 팜이 반환한 x-sap-sec, af-ac-enc-* 등.
    """

    def __init__(
        self,
        cookies: dict,
        *,
        region: str = "BR",
        proxy_url: str | None = None,
        profile: str = "ios",
        impersonate: str = "safari180",
        timeout: float = 25.0,
        signer=None,
    ):
        self.cookies = dict(cookies)
        self.region = region.upper()
        self.host = REGION_HOST.get(self.region, REGION_HOST["BR"])
        self.proxy_url = proxy_url
        self.profile = profile
        self.impersonate = impersonate
        self.timeout = timeout
        self.signer = signer  # Callable | None — 서명 위조 아님, 외부 민팅만

    def _proxies(self) -> dict | None:
        if not self.proxy_url:
            return None
        p = self.proxy_url if "://" in self.proxy_url else f"http://{self.proxy_url}"
        return {"http": p, "https": p}

    def _get(self, url: str, headers: dict) -> tuple[int, Any, float]:
        t0 = time.time()
        try:
            r = curlrq.get(
                url,
                headers=headers,
                proxies=self._proxies(),
                impersonate=self.impersonate,
                timeout=self.timeout,
                allow_redirects=True,
            )
            try:
                body = r.json()
            except Exception:
                body = {"_raw": (r.text or "")[:400]}
            return r.status_code, body, time.time() - t0
        except Exception as e:
            return 0, {"_exc": f"{type(e).__name__}: {e}"}, time.time() - t0

    def _pack(self, endpoint: str, http: int, body: Any, elapsed: float) -> MobileResult:
        if not isinstance(body, dict):
            return MobileResult(
                False, "bad_body", endpoint, http=http, error=type(body).__name__, elapsed_s=elapsed,
            )
        if body.get("_exc"):
            return MobileResult(False, "network", endpoint, http=http, error=body["_exc"], elapsed_s=elapsed)
        err = body.get("error")
        if err == 90309999:
            return MobileResult(
                False, "need_app_sign", endpoint, http=http, error=90309999, elapsed_s=elapsed,
                notes=["앱 서명(x-sap-sec 등) 필요 — 위조하지 않음"],
            )
        if err not in (None, 0):
            return MobileResult(False, f"api_error_{err}", endpoint, http=http, error=err, elapsed_s=elapsed)
        data = body.get("data")
        if not isinstance(data, dict) or not data:
            return MobileResult(False, "empty_data", endpoint, http=http, error=err, elapsed_s=elapsed)
        item = data.get("item") if isinstance(data.get("item"), dict) else data
        title = str(item.get("title") or item.get("name") or "")[:80]
        return MobileResult(
            True, "ok", endpoint, http=http, data=data, elapsed_s=elapsed,
            title=title, data_keys=len(data), item_keys=len(item) if isinstance(item, dict) else 0,
        )

    def _signed_headers(self, url: str, headers: dict) -> dict:
        if not self.signer:
            return headers
        try:
            out = self.signer(url, dict(headers), dict(self.cookies))
            return out if isinstance(out, dict) else headers
        except Exception:
            return headers

    def fetch_item_get(self, shop_id: int, item_id: int, *, api_source: str = "rn") -> MobileResult:
        url = f"{self.host}/api/v4/item/get?shopid={shop_id}&itemid={item_id}"
        h = build_headers(
            self.region, shop_id=shop_id, item_id=item_id,
            cookies=self.cookies, profile=self.profile, api_source=api_source,
        )
        http, body, elapsed = self._get(url, self._signed_headers(url, h))
        return self._pack("item/get", http, body, elapsed)

    def fetch_get_rw(self, shop_id: int, item_id: int, *, api_source: str = "rweb") -> MobileResult:
        url = (
            f"{self.host}/api/v4/pdp/get_rw?shop_id={shop_id}&item_id={item_id}"
            f"&tz_offset_in_minutes=180&detail_level=0"
        )
        h = build_headers(
            self.region, shop_id=shop_id, item_id=item_id,
            cookies=self.cookies, profile=self.profile, api_source=api_source,
        )
        http, body, elapsed = self._get(url, self._signed_headers(url, h))
        return self._pack("pdp/get_rw", http, body, elapsed)

    def fetch_get_pc(self, shop_id: int, item_id: int, *, api_source: str = "rn") -> MobileResult:
        url = (
            f"{self.host}/api/v4/pdp/get_pc?shop_id={shop_id}&item_id={item_id}"
            f"&tz_offset_in_minutes=180&detail_level=0"
        )
        h = build_headers(
            self.region, shop_id=shop_id, item_id=item_id,
            cookies=self.cookies, profile=self.profile, api_source=api_source,
        )
        http, body, elapsed = self._get(url, self._signed_headers(url, h))
        return self._pack("pdp/get_pc", http, body, elapsed)

    def fetch_best(self, shop_id: int, item_id: int) -> MobileResult:
        """우선순위: get_pc(rn) → get_rw → item/get. Client 1은 get_pc 선호."""
        attempts = [
            ("get_pc", lambda: self.fetch_get_pc(shop_id, item_id, api_source="rn")),
            ("get_pc_pc", lambda: self.fetch_get_pc(shop_id, item_id, api_source="pc")),
            ("get_rw", lambda: self.fetch_get_rw(shop_id, item_id, api_source="rweb")),
            ("item_get", lambda: self.fetch_item_get(shop_id, item_id, api_source="rn")),
        ]
        last: MobileResult | None = None
        for name, fn in attempts:
            r = fn()
            last = r
            if r.ok:
                r.notes.append(f"선택={name}")
                return r
            if r.status == "need_app_sign":
                # 서명이 막으면 같은 세션으로 더 때리지 않고 즉시 보고
                r.notes.append("서명 차단 — 브라우저 폴백 권장")
                return r
        return last or MobileResult(False, "no_attempt", "none")
