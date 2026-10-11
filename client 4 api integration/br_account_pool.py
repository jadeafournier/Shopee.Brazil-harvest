# -*- coding: utf-8 -*-
"""BR account pool — protect login cookies for long-term crawling.

Does NOT forge X-Sap-Sec / Af-Ac-Enc-* / Sz-Token / AC_CERT_D.
Those are minted inside real Chrome (web_enhance_sap + traffic path).

This module protects them by:
  * Soft per-shift URL quotas (stop before heat)
  * Cooldown after traffic_error / captcha (never hammer a hot account)
  * Persisting harvested cookies without rewriting BR TEST.txt
  * Multi-account rotation when several session jars are available
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from br_cookies import enforce_br_locale, normalize_br_cookies

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
POOL_STATE = SESSIONS_DIR / "br_account_pool_state.json"
BR_TEST = Path(__file__).resolve().parent / "BR TEST.txt"

# Empirically: traffic_error often starts ~80–120 URLs/session. Stop earlier.
DEFAULT_SHIFT_QUOTA = 40
DEFAULT_COOLDOWN_S = 4 * 3600  # 4 hours after account-risk signal
CAPTCHA_COOLDOWN_S = 12 * 3600


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: float | None = None) -> str:
    if ts is None:
        return _utc_now().isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_cookie_line(line: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in line.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def load_br_test_cookies(path: Path = BR_TEST) -> dict[str, str]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower().rstrip(":")
        if low == "cookie":
            section = "cookie"
            rest = line.split(":", 1)
            if len(rest) > 1 and rest[1].strip():
                return _parse_cookie_line(rest[1].strip())
            continue
        if low in ("proxy",) or low.startswith("test url"):
            break
        if section == "cookie":
            return _parse_cookie_line(line)
    return {}


def account_id(cookies: dict) -> str:
    u = str(cookies.get("SPC_U") or "anon")
    f = str(cookies.get("SPC_F") or "")[:8]
    return f"br_{u}" if not f else f"br_{u}_{f}"


class AccountPool:
    """Tracks per-account quotas, cooldowns, and live cookie jars."""

    def __init__(
        self,
        *,
        shift_quota: int = DEFAULT_SHIFT_QUOTA,
        cooldown_s: int = DEFAULT_COOLDOWN_S,
        captcha_cooldown_s: int = CAPTCHA_COOLDOWN_S,
        state_path: Path = POOL_STATE,
    ):
        self.shift_quota = max(1, int(shift_quota))
        self.cooldown_s = max(60, int(cooldown_s))
        self.captcha_cooldown_s = max(self.cooldown_s, int(captcha_cooldown_s))
        self.state_path = state_path
        self.state: dict[str, Any] = {"accounts": {}, "updated_at": None}
        self._load_state()

    def _load_state(self) -> None:
        if self.state_path.is_file():
            try:
                self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                self.state = {"accounts": {}, "updated_at": None}
        self.state.setdefault("accounts", {})

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state["updated_at"] = _iso()
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def _acc(self, aid: str) -> dict:
        accounts = self.state["accounts"]
        if aid not in accounts:
            accounts[aid] = {
                "spc_u": aid.split("_")[1] if "_" in aid else aid,
                "ok_total": 0,
                "fail_total": 0,
                "shift_ok": 0,
                "cooldown_until": 0.0,
                "last_status": "idle",
                "last_risk": None,
                "session_file": None,
            }
        return accounts[aid]

    def register(self, cookies: dict, *, session_file: Path | None = None, fresh: bool = False) -> str:
        work = enforce_br_locale(normalize_br_cookies(cookies, fresh_cert=False))
        aid = account_id(work)
        rec = self._acc(aid)
        rec["spc_u"] = work.get("SPC_U")
        preferred = SESSIONS_DIR / f"{aid}_live.json"
        out_path = preferred if session_file is None else Path(session_file)
        if "br_client4" in out_path.name:
            out_path = preferred
        rec["session_file"] = str(out_path)
        self._write_session(aid, work, out_path)
        if fresh:
            rec["shift_ok"] = 0
            rec["cooldown_until"] = 0.0
            rec["last_status"] = "fresh_import"
            self.state["primary_account"] = aid
        self.save()
        return aid

    def register_br_test(self) -> str | None:
        """Load BR TEST.txt, refresh session jar, reset shift/cooldown for this account."""
        ck = load_br_test_cookies()
        if not ck.get("SPC_U"):
            return None
        return self.register(ck, fresh=True)

    def sync_br_test(self) -> str | None:
        """Sync BR TEST.txt into session jar without clearing an active risk cooldown."""
        ck = load_br_test_cookies()
        if not ck.get("SPC_U"):
            return None
        aid = account_id(ck)
        old = self.load_cookies(aid) if aid in self.state.get("accounts", {}) else {}
        rec = self._acc(aid) if aid in self.state.get("accounts", {}) else None
        cookies_changed = (
            old.get("SPC_U") != ck.get("SPC_U")
            or (ck.get("AC_CERT_D") and ck.get("AC_CERT_D") != old.get("AC_CERT_D"))
        )
        in_risk_cooldown = False
        if rec:
            now = time.time()
            cool = float(rec.get("cooldown_until") or 0)
            risk = rec.get("last_status") in ("traffic_error", "captcha")
            in_risk_cooldown = risk and cool > now
        if cookies_changed and not in_risk_cooldown:
            return self.register(ck, fresh=True)
        if cookies_changed and in_risk_cooldown:
            # New cookies pasted while old session cooling — user replaced account.
            return self.register(ck, fresh=True)
        return self.register(ck, fresh=False)

    def session_path(self, aid: str) -> Path:
        rec = self._acc(aid)
        if rec.get("session_file"):
            return Path(rec["session_file"])
        return SESSIONS_DIR / f"{aid}_live.json"

    def _write_session(self, aid: str, cookies: dict, path: Path | None = None) -> Path:
        out = path or self.session_path(aid)
        out.parent.mkdir(parents=True, exist_ok=True)
        safe = enforce_br_locale(cookies)
        payload = {
            "account_name": aid,
            "region": "BR",
            "updated_at": _iso(),
            "cookies": safe,
            "protect": {
                "note": "Browser-minted credentials only — do not forge SAP/AC_CERT",
                "shift_quota": self.shift_quota,
            },
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._acc(aid)["session_file"] = str(out)
        return out

    def load_cookies(self, aid: str) -> dict[str, str]:
        path = self.session_path(aid)
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return enforce_br_locale(data.get("cookies") or {})

    def persist_cookies(self, aid: str, cookies: dict) -> None:
        """Save harvested cookies; keep AC_CERT_D from browser, force BR locale."""
        self._write_session(aid, cookies)
        self.save()

    def available(self, aid: str) -> tuple[bool, str]:
        rec = self._acc(aid)
        now = time.time()
        cool = float(rec.get("cooldown_until") or 0)
        if cool > now:
            mins = int((cool - now) / 60) + 1
            return False, f"cooldown {mins}m (until {_iso(cool)})"
        if int(rec.get("shift_ok") or 0) >= self.shift_quota:
            return False, f"shift_quota {rec['shift_ok']}/{self.shift_quota} — rest or --reset-shift"
        return True, "ready"

    def pick(self, *, primary_only: bool = False) -> str | None:
        primary = self.state.get("primary_account")
        if primary_only:
            if not primary:
                ck = load_br_test_cookies()
                if ck.get("SPC_U"):
                    primary = account_id(ck)
            if primary:
                ok, _ = self.available(primary)
                if ok and self.load_cookies(primary):
                    return primary
            return None
        if primary:
            ok, _ = self.available(primary)
            if ok and self.load_cookies(primary):
                return primary
        ready = []
        for aid in self.state["accounts"]:
            if aid == primary:
                continue
            ok, _ = self.available(aid)
            if ok and self.load_cookies(aid):
                ready.append(aid)
        if not ready:
            return None
        ready.sort(key=lambda a: (self._acc(a).get("shift_ok", 0), self._acc(a).get("cooldown_until", 0)))
        return ready[0]

    def reset_quota_exhausted(self) -> int:
        """Clear shift_ok for accounts at quota but not in risk cooldown (next shift ready)."""
        n = 0
        now = time.time()
        for aid, rec in self.state["accounts"].items():
            if int(rec.get("shift_ok") or 0) >= self.shift_quota:
                if float(rec.get("cooldown_until") or 0) <= now:
                    rec["shift_ok"] = 0
                    n += 1
        if n:
            self.save()
        return n

    def record_ok(self, aid: str, cookies: dict | None = None) -> None:
        rec = self._acc(aid)
        rec["ok_total"] = int(rec.get("ok_total") or 0) + 1
        rec["shift_ok"] = int(rec.get("shift_ok") or 0) + 1
        rec["last_status"] = "ok"
        if cookies:
            self.persist_cookies(aid, cookies)
        else:
            self.save()

    def mark_risk(self, aid: str, reason: str, cookies: dict | None = None) -> None:
        """Account-risk: cool down immediately; persist cookies once; stop using account."""
        rec = self._acc(aid)
        rec["fail_total"] = int(rec.get("fail_total") or 0) + 1
        rec["last_status"] = reason
        rec["last_risk"] = _iso()
        cool = self.captcha_cooldown_s if reason == "captcha" else self.cooldown_s
        rec["cooldown_until"] = time.time() + cool
        # Cap shift so this account won't be reused this window.
        rec["shift_ok"] = max(int(rec.get("shift_ok") or 0), self.shift_quota)
        if cookies:
            self.persist_cookies(aid, cookies)
        else:
            self.save()

    def reset_shift(self, aid: str | None = None) -> None:
        for key in ([aid] if aid else list(self.state["accounts"].keys())):
            if key in self.state["accounts"]:
                self.state["accounts"][key]["shift_ok"] = 0
        self.save()

    def next_available_wait_s(self, *, primary_only: bool = False) -> float:
        now = time.time()
        waits = []
        items = self.state["accounts"].items()
        if primary_only:
            primary = self.state.get("primary_account")
            if not primary:
                return 0.0
            items = [(primary, self.state["accounts"].get(primary, {}))]
        for aid, rec in items:
            cool = float(rec.get("cooldown_until") or 0)
            if cool > now and self.load_cookies(aid):
                waits.append(cool - now)
            elif int(rec.get("shift_ok") or 0) >= self.shift_quota and cool <= now:
                pass
        return min(waits) if waits else 0.0

    def summary(self) -> list[dict]:
        rows = []
        for aid, rec in self.state["accounts"].items():
            ok, why = self.available(aid)
            rows.append({
                "account": aid,
                "spc_u": rec.get("spc_u"),
                "available": ok,
                "reason": why,
                "shift_ok": rec.get("shift_ok"),
                "ok_total": rec.get("ok_total"),
                "fail_total": rec.get("fail_total"),
                "last_status": rec.get("last_status"),
                "cooldown_until": _iso(rec["cooldown_until"]) if rec.get("cooldown_until") else None,
                "session_file": rec.get("session_file"),
            })
        return rows


def discover_accounts(pool: AccountPool, *, primary_only: bool = False) -> list[str]:
    """Register BR TEST.txt (primary); optionally skip stale backup session jars."""
    found: list[str] = []
    ck = load_br_test_cookies()
    if ck.get("SPC_U"):
        aid = pool.register(ck)
        pool.state["primary_account"] = aid
        pool.save()
        found.append(aid)

    if primary_only:
        return found

    if SESSIONS_DIR.is_dir():
        for path in sorted(SESSIONS_DIR.glob("*.json")):
            if path.name in ("br_account_pool_state.json", "br_client4_live.json", "br_client4_test.json"):
                continue
            if not path.name.endswith("_live.json") and not path.name.startswith("br_"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            ck = data.get("cookies") or {}
            if not ck.get("SPC_U"):
                continue
            aid = account_id(ck)
            # Skip re-register if already from BR TEST (fresher).
            if aid in found:
                continue
            pool.register(ck, session_file=SESSIONS_DIR / f"{aid}_live.json")
            found.append(aid)
    return found
