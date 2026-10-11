# -*- coding: utf-8 -*-
"""Normalize BR Shopee session cookies before browser injection.

Exports often come from TW/EN sessions (ssr-tz=Asia/Taipei, language=en).
Those mismatches are a trust poison signal on shopee.com.br.
"""
from __future__ import annotations

BR_TIMEZONE = "America/Sao_Paulo"
BR_TZ_OFFSET = 180  # minutes west of UTC for get_pc tz_offset_in_minutes
BR_LOCALE = "pt-BR"
BR_LANGUAGE = "pt-BR"
BR_ACCEPT_LANGUAGE = "pt-BR,pt;q=0.9,en;q=0.8"

# Always forced on inject / after harvest sync.
_BR_OVERRIDES = {
    "ssr-tz": BR_TIMEZONE,
    "language": BR_LANGUAGE,
}


def normalize_br_cookies(cookies: dict, *, fresh_cert: bool = True) -> dict:
    """Fix region-mismatched cookie fields for Brazil storefront.

    fresh_cert=True drops stale AC_CERT_* so Shopee can mint a new cert via the
    browser challenge path (we do not forge certs).
    """
    out = dict(cookies)
    for k, v in _BR_OVERRIDES.items():
        out[k] = v
    if fresh_cert:
        for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
            out.pop(k, None)
    return out


def enforce_br_locale(cookies: dict) -> dict:
    """Re-apply BR locale/tz after browser cookie harvest (in-place safe copy)."""
    return normalize_br_cookies(cookies, fresh_cert=False)


def pw_cookie_list(
    cookies: dict,
    domain: str = "shopee.com.br",
    *,
    fresh_cert: bool = False,
) -> list[dict]:
    """Playwright cookie list with BR locale/tz forced."""
    work = normalize_br_cookies(cookies, fresh_cert=fresh_cert)
    return [
        {"name": k, "value": v, "domain": f".{domain}", "path": "/"}
        for k, v in work.items()
        if v and v not in ("-", "deleted")
    ]


def br_context_kwargs() -> dict:
    """Shared Patchright/Playwright context locale settings for BR."""
    return {
        "locale": BR_LOCALE,
        "timezone_id": BR_TIMEZONE,
        "extra_http_headers": {
            "Accept-Language": BR_ACCEPT_LANGUAGE,
        },
    }
