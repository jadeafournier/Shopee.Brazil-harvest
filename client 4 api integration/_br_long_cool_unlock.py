# -*- coding: utf-8 -*-
"""Continuous BR get_pc to 1000: multi-jar sticky unlock, per-account cool, no thrash.

Proven unlock (17:26): 2h silence → strip AC_CERT → Rio sticky → br_1705283243
→ keep browser open → ~90× keys=103. On 90309999 stop that jar 2h; try next jar.
"""
from __future__ import annotations

import json
import random
import re
import string
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(HERE))

from br_cookies import br_context_kwargs, pw_cookie_list, normalize_br_cookies, enforce_br_locale
import br_human as human
import captcha_mint as mint
import proxy_pool

DELIV = HERE / "br_deliverable_getpc"
DELIV.mkdir(exist_ok=True)
SESS = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
URLS = HERE / "br_active_product_urls_100k.txt"
LOG = HERE / "br_long_cool_unlock.log"
LOCK = HERE / "br_long_cool.lock"
STATE = HERE / "br_long_cool_state.json"
TARGET = 1000
MIN_KEYS = 40
JAR_COOL_S = 2 * 3600
GAP_BETWEEN_JARS_S = 8 * 60  # brief IP settle when rotating jars
QUOTA = 150

# Hot first, then other live logins
HOT = [
    "br_1705283243_F3zrbhjv_live.json",
    "br_1705283243.json",
    "br_1707407334_mRNUQARQ_live.json",
    "br_5196720216_hsRjFTxp_live.json",
    "br_1706801030_ydvUxsIK_live.json",
    "br_1706785017_CoOHjjSQ_live.json",
    "br_1706784820_OUk7hm0A_live.json",
    "br_1707139756_0EAyNQaW_live.json",
    "br_1707408898_LAxNlL5a_live.json",
]

CITIES = [
    ("Rio de Janeiro", "Rio de Janeiro"),
    ("São Paulo", "Barueri"),
    ("São Paulo", "Campinas"),
]


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def n_ok() -> int:
    return sum(1 for p in DELIV.glob("output_*.json") if re.match(r"output_\d+_\d+\.json", p.name))


def load_state() -> dict:
    if STATE.is_file():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"cooldown": {}}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def set_proxy(city_i: int = 0) -> None:
    sid = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))
    st, city = CITIES[city_i % len(CITIES)]
    user = f"gdxc12191-region-BR-st-{st}-city-{city}-sid-{sid}-t-120"
    (ROOT / "proxy(BR shopee).txt").write_text(
        f"us.1024proxy.io:3000:{user}:qadnrubc\n", encoding="utf-8"
    )
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()


def list_accounts() -> list[str]:
    out = []
    for name in HOT:
        p = SESS / name
        if not p.is_file():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            if (doc.get("cookies") or doc).get("SPC_U"):
                out.append(name)
        except Exception:
            pass
    for p in SESS.glob("br_*_live.json"):
        if p.name in out:
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            if (doc.get("cookies") or doc).get("SPC_U"):
                out.append(p.name)
        except Exception:
            pass
    return out


def have_ids():
    s = set()
    for f in DELIV.glob("output_*.json"):
        m = re.match(r"output_(\d+)_(\d+)\.json", f.name)
        if m:
            s.add((m.group(1), m.group(2)))
    return s


def parse_ids(u: str):
    m = re.search(r"i\.(\d+)\.(\d+)", u) or re.search(r"/product/(\d+)/(\d+)", u)
    return (m.group(1), m.group(2)) if m else None


def save_full(j: dict, fallback_url: str) -> bool:
    data = j.get("data")
    if not isinstance(data, dict) or j.get("error") not in (0, None):
        return False
    item = data.get("item")
    if not isinstance(item, dict) or len(item) < MIN_KEYS:
        return False
    shop = str(item.get("shop_id") or item.get("shopid") or "")
    it = str(item.get("item_id") or item.get("itemid") or "")
    if not shop or not it:
        return False
    out = DELIV / f"output_{shop}_{it}.json"
    if out.is_file():
        return False
    payload = {
        "task_link": fallback_url,
        "token": "br_long_cool",
        "api_key": "br_long_cool",
        "bff_meta": {"extract_method": "browser_get_pc"},
        "error": None,
        "error_msg": None,
        "data": data,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return True


def sticky_harvest(acc: str, urls: list[str], *, city_i: int, quota: int = QUOTA) -> dict:
    from patchright.sync_api import sync_playwright

    doc = json.loads((SESS / acc).read_text(encoding="utf-8"))
    jar = dict(doc.get("cookies") or doc)
    # Always strip cert — remint on this fingerprint (proven unlock path)
    for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
        jar.pop(k, None)
    jar = enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
    if not jar.get("SPC_U"):
        return {"ok": 0, "fatal": True, "reason": "no_login"}

    set_proxy(city_i)
    proxy, _, sub = mint._proxy_dict("BR")
    stats = {"ok": 0, "fail": 0, "fatal": False, "reason": None}
    got = {"n": 0}
    log(f"STICKY {acc} SPC_U={jar.get('SPC_U')} strip_cert quota={quota} sub=...{(sub or '')[-40:]}")

    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="brlcu_")
    cx = br_context_kwargs()
    ctx = None
    try:
        ctx = p.chromium.launch_persistent_context(
            udd,
            channel="chrome",
            headless=False,
            proxy=proxy,
            viewport={"width": 1440, "height": 900},
            locale=cx["locale"],
            timezone_id=cx["timezone_id"],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            extra_http_headers=cx["extra_http_headers"],
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        ctx.add_cookies(pw_cookie_list(jar, fresh_cert=False))
        page = ctx.new_page()

        def on_resp(r):
            try:
                if "/api/v4/pdp/get_pc" not in r.url or r.status != 200:
                    return
                j = r.json()
                item = ((j.get("data") or {}).get("item") if isinstance(j.get("data"), dict) else None) or {}
                err = j.get("error")
                keys = len(item) if isinstance(item, dict) else 0
                log(f"get_pc err={err} keys={keys}")
                if err == 90309999:
                    stats["fatal"] = True
                    stats["reason"] = "need_app_sign"
                    log("FATAL need_app_sign — stop jar")
                elif err in (0, None) and keys >= MIN_KEYS:
                    got["n"] += 1
                    if save_full(j, r.url):
                        stats["ok"] += 1
                        log(f"OK +1 shift={got['n']} total={n_ok()}")
                    else:
                        stats["ok"] += 1
                        log(f"DUP shift={got['n']} total={n_ok()}")
            except Exception:
                pass

        page.on("response", on_resp)
        try:
            human.warm_home(page, "shopee.com.br")
        except Exception as e:
            log(f"warm {e}")
        for _ in range(5):
            u = page.url or ""
            if "/verify/traffic/error" in u:
                stats["fatal"] = True
                stats["reason"] = "traffic_error_home"
                return stats
            if "/verify/traffic" in u:
                mint._wait_traffic_pass(page, "shopee.com.br", secs=30)
            else:
                break
            page.wait_for_timeout(800)

        for i, url in enumerate(urls):
            if n_ok() >= TARGET or got["n"] >= quota or stats["fatal"]:
                break
            before = got["n"]
            log(f"goto[{i}] …{url[-48:]}")
            try:
                page.goto(url, wait_until="commit", timeout=55000)
            except Exception as e:
                log(f"goto {type(e).__name__}")
            deadline = time.time() + 55
            while time.time() < deadline:
                u = page.url or ""
                if "/verify/captcha" in u:
                    stats["fatal"] = True
                    stats["reason"] = "captcha"
                    break
                if "/verify/traffic/error" in u:
                    stats["fatal"] = True
                    # keep need_app_sign if already set
                    if stats.get("reason") != "need_app_sign":
                        stats["reason"] = "traffic_error"
                    break
                if "/verify/traffic" in u:
                    mint._wait_traffic_pass(page, "shopee.com.br", secs=22)
                    try:
                        page.goto(url, wait_until="commit", timeout=55000)
                    except Exception:
                        pass
                if got["n"] > before:
                    break
                page.wait_for_timeout(350)
            if stats["fatal"]:
                break
            if got["n"] == before:
                stats["fail"] += 1
                log(f"miss fail={stats['fail']}")
                if stats["ok"] == 0 and stats["fail"] >= 2:
                    stats["fatal"] = True
                    stats["reason"] = stats.get("reason") or "cold"
                    break
                if stats["fail"] >= 6:
                    break
            else:
                stats["fail"] = 0
                if stats["ok"] >= 20:
                    quota = max(quota, 220)
            try:
                human.idle(page, 0.5, 1.4)
            except Exception:
                time.sleep(0.8)

        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        jar.update(ck)
        (SESS / acc).write_text(
            json.dumps({"cookies": jar, "ts": time.time(), "hot": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        log(
            f"session done ok={stats['ok']} fail={stats['fail']} "
            f"fatal={stats['fatal']}/{stats['reason']} total={n_ok()}"
        )
    except Exception as e:
        log(f"EXC {e}")
        stats["fatal"] = True
        stats["reason"] = str(e)[:60]
    finally:
        try:
            if ctx:
                ctx.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
    return stats


def pick_account(accounts: list[str], cooldown: dict[str, float]) -> str | None:
    now = time.time()
    for a in accounts:
        if now >= float(cooldown.get(a, 0)):
            return a
    return None


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    boot_cool = 0
    if len(sys.argv) > 1:
        boot_cool = max(0, int(sys.argv[1]))

    if LOCK.is_file() and time.time() - LOCK.stat().st_mtime < 5 * 3600:
        # allow replace if same machine restart
        try:
            old = int(LOCK.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            old = 0
        if old and time.time() - old < 60:
            log("lock held — exit")
            return 3
    LOCK.write_text(str(int(time.time())), encoding="utf-8")

    try:
        st = load_state()
        cooldown: dict[str, float] = {k: float(v) for k, v in (st.get("cooldown") or {}).items()}
        # Seed hot jar cool from last burn (~19:06) if still mid-cool
        hot = HOT[0]
        if hot not in cooldown or cooldown[hot] < time.time():
            # if boot_cool given, apply to ALL currently burned-looking jars only as fleet settle
            pass
        if boot_cool > 0:
            log(f"boot silence {boot_cool}s (no requests)")
            time.sleep(boot_cool)

        accounts = list_accounts()
        log(f"CONT start n={n_ok()}/{TARGET} accounts={accounts[:8]}")
        have = have_ids()
        pending = []
        for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            ids = parse_ids(line)
            if ids and ids not in have:
                pending.append(line)
        log(f"pending={len(pending)}")

        cursor = 0
        idle = 0
        city_i = 0
        stick_acc: str | None = None

        while n_ok() < TARGET and cursor < len(pending) and idle < 40:
            # Prefer stick account if unlocked recently and not cooling
            acc = None
            if stick_acc and time.time() >= cooldown.get(stick_acc, 0):
                acc = stick_acc
            if not acc:
                acc = pick_account(accounts, cooldown)
            if not acc:
                wait = min(cooldown.values()) - time.time() if cooldown else 600
                wait = max(120, min(int(wait) + 5, JAR_COOL_S))
                log(f"all jars cooling — sleep {wait}s n={n_ok()}")
                time.sleep(wait)
                continue

            chunk = pending[cursor : cursor + 160]
            cursor += 160
            stats = sticky_harvest(acc, chunk, city_i=city_i, quota=QUOTA)
            city_i += 1

            if stats["ok"] > 0 and not stats.get("fatal"):
                idle = 0
                stick_acc = acc
                # put account first
                if acc in accounts:
                    accounts.remove(acc)
                    accounts.insert(0, acc)
                log(f"clean burst ok={stats['ok']} — reopen same jar")
                time.sleep(20)
            elif stats["ok"] > 0 and stats.get("fatal"):
                idle = 0
                stick_acc = None
                # Fleet silence — IP heat burns sibling jars if we rotate early
                until = time.time() + JAR_COOL_S
                for a in accounts:
                    cooldown[a] = max(float(cooldown.get(a, 0)), until)
                st["cooldown"] = cooldown
                save_state(st)
                log(f"burst ok={stats['ok']} then burned — FLEET silence {JAR_COOL_S}s")
                time.sleep(JAR_COOL_S)
            else:
                idle += 1
                stick_acc = None
                until = time.time() + JAR_COOL_S
                for a in accounts:
                    cooldown[a] = max(float(cooldown.get(a, 0)), until)
                st["cooldown"] = cooldown
                save_state(st)
                log(f"burned {acc} ({stats.get('reason')}) — FLEET silence {JAR_COOL_S}s (no jar rotate)")
                time.sleep(JAR_COOL_S)

            log(f"progress {n_ok()}/{TARGET} idle={idle} cursor={cursor}")

        final = n_ok()
        log(f"DONE n={final}")
        print(f"FINAL_COUNT={final}")
        return 0 if final >= TARGET else 2
    finally:
        try:
            LOCK.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
