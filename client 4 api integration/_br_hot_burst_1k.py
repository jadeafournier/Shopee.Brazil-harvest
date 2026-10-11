# -*- coding: utf-8 -*-
"""Hot-burst BR get_pc: keep unlocked browser session open; stick to winning jars."""
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
LOG = HERE / "br_hot_burst_run.log"
TARGET = 1000
MIN_KEYS = 40

# Hot jars first (1705283243 unlocked at 08:52)
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

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]
CITIES = [
    ("Rio de Janeiro", "Rio de Janeiro"),
    ("São Paulo", "Barueri"),
    ("São Paulo", "Campinas"),
    ("Minas Gerais", "Belo Horizonte"),
    ("Paraná", "Curitiba"),
]


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def n_ok() -> int:
    return sum(1 for p in DELIV.glob("output_*.json") if re.match(r"output_\d+_\d+\.json", p.name))


def rotate(n: int = 40) -> None:
    def sid():
        return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))

    lines = []
    for i in range(n):
        st, city = CITIES[i % len(CITIES)]
        user = f"gdxc12191-region-BR-st-{st}-city-{city}-sid-{sid()}-t-120"
        lines.append(f"us.1024proxy.io:3000:{user}:qadnrubc")
    (ROOT / "proxy(BR shopee).txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()


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
    if not isinstance(data, dict):
        return False
    if j.get("error") not in (0, None):
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
        "token": "br_hot_burst",
        "api_key": "br_hot_burst",
        "bff_meta": {"extract_method": "browser_get_pc"},
        "error": None,
        "error_msg": None,
        "data": data,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return True


def load_accounts() -> list[str]:
    out = []
    for name in HOT:
        p = SESS / name
        if not p.is_file():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            ck = doc.get("cookies") or doc
            if ck.get("SPC_U"):
                out.append(name)
        except Exception:
            pass
    for p in SESS.glob("br_*_live.json"):
        if p.name not in out:
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
                if (doc.get("cookies") or doc).get("SPC_U"):
                    out.append(p.name)
            except Exception:
                pass
    return out


def burst(acc: str, urls: list[str], quota: int = 55) -> dict:
    """One browser session, many PDPs. Strip cert so SPA remints on this fingerprint."""
    from patchright.sync_api import sync_playwright

    doc = json.loads((SESS / acc).read_text(encoding="utf-8"))
    jar = dict(doc.get("cookies") or doc)
    for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
        jar.pop(k, None)
    jar = enforce_br_locale(normalize_br_cookies(jar, fresh_cert=False))
    if not jar.get("SPC_U"):
        return {"ok": 0, "fatal": True, "reason": "no_login"}

    # Prefer Rio sticky — historically unlocked this jar
    rotate(24)
    proxy, _, sub = mint._proxy_dict("BR")
    ua = UAS[0]
    stats = {"ok": 0, "fail": 0, "fatal": False, "reason": None}
    got = {"n": 0}
    log(f"BURST {acc} SPC_U={jar.get('SPC_U')} n={len(urls)} quota={quota} sub=...{(sub or '')[-36:]}")

    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="brhot_")
    cx = br_context_kwargs()
    ctx = None
    try:
        ctx = p.chromium.launch_persistent_context(
            udd,
            channel="chrome",
            headless=False,
            proxy=proxy,
            viewport={"width": random.choice([1366, 1440]), "height": random.choice([768, 900])},
            locale=cx["locale"],
            timezone_id=cx["timezone_id"],
            user_agent=ua,
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
                    log("FATAL need_app_sign 90309999")
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
                log("FATAL traffic/error home")
                return stats
            if "/verify/traffic" in u:
                mint._wait_traffic_pass(page, "shopee.com.br", secs=28)
            else:
                break
            page.wait_for_timeout(800)

        for i, url in enumerate(urls):
            if n_ok() >= TARGET or got["n"] >= quota or stats["fatal"]:
                break
            before = got["n"]
            log(f"goto[{i}] …{url[-48:]}")
            try:
                page.goto(url, wait_until="commit", timeout=50000)
            except Exception as e:
                log(f"goto {type(e).__name__}")
            deadline = time.time() + 50
            while time.time() < deadline:
                u = page.url or ""
                if "/verify/captcha" in u:
                    stats["fatal"] = True
                    stats["reason"] = "captcha"
                    break
                if "/verify/traffic/error" in u:
                    stats["fatal"] = True
                    stats["reason"] = "traffic_error"
                    break
                if "/verify/traffic" in u:
                    mint._wait_traffic_pass(page, "shopee.com.br", secs=20)
                    try:
                        page.goto(url, wait_until="commit", timeout=50000)
                    except Exception:
                        pass
                if got["n"] > before:
                    break
                page.wait_for_timeout(350)
            if got["n"] == before:
                stats["fail"] += 1
                log(f"miss fail={stats['fail']}")
                # Preserve need_app_sign from on_resp; only label cold_start if no 9030 yet
                if stats["fatal"] and stats.get("reason") == "need_app_sign":
                    break
                if stats["fail"] >= 3 and stats["ok"] == 0:
                    stats["fatal"] = True
                    if not stats.get("reason"):
                        stats["reason"] = "cold_start"
                    break
                miss_limit = 8 if stats["ok"] > 0 else 5
                if stats["fail"] >= miss_limit:
                    log(f"{miss_limit} misses — end burst")
                    break
            else:
                stats["fail"] = 0
                # stretch quota while unlocked
                if stats["ok"] >= 10:
                    quota = max(quota, 80)
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
        log(f"burst done ok={stats['ok']} fail={stats['fail']} fatal={stats['fatal']}/{stats['reason']} total={n_ok()} cert={'Y' if ck.get('AC_CERT_D') else 'N'}")
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


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    log(f"HOT start n={n_ok()}/{TARGET}")
    accounts = load_accounts()
    log(f"accounts={accounts[:8]}")
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
    acc_i = 0
    idle = 0
    cooldown: dict[str, float] = {}

    while n_ok() < TARGET and cursor < len(pending) and idle < 80:
        # pick account not cooling; prefer hot list order
        acc = None
        for _ in range(len(accounts)):
            cand = accounts[acc_i % len(accounts)]
            acc_i += 1
            if time.time() >= cooldown.get(cand, 0):
                acc = cand
                break
        if not acc:
            wait = min(cooldown.values()) - time.time() if cooldown else 60
            log(f"all cooling sleep {int(max(wait, 30))}s")
            time.sleep(max(wait, 30))
            continue

        # Larger chunk while unlocked; stop immediately on burn
        chunk = pending[cursor : cursor + 80]
        cursor += 80
        stats = burst(acc, chunk, quota=55)
        if stats["ok"] > 0:
            idle = 0
            # stick to this account for next rounds
            try:
                accounts.remove(acc)
            except ValueError:
                pass
            accounts.insert(0, acc)
            acc_i = 0
            # brief pause then reopen SAME jar (session closed after burst)
            time.sleep(12)
        else:
            idle += 1
            reason = stats.get("reason") or ""
            if reason in ("traffic_error", "traffic_error_home", "captcha", "cold_start", "need_app_sign"):
                cool_for = 3600 if reason == "need_app_sign" else 2700
                cooldown[acc] = time.time() + cool_for
                log(f"cool {acc} ({reason})")
                # Do NOT thrash sibling jars after IP/account heat — cool fleet
                if reason == "need_app_sign" or (reason == "cold_start" and idle >= 1):
                    for a in accounts:
                        cooldown[a] = max(cooldown.get(a, 0), time.time() + cool_for)
                    log(f"fleet cool {cool_for}s — stop thrashing")
                    time.sleep(cool_for)
                    idle = 0
                    continue
            time.sleep(120 + idle * 20)
        log(f"progress {n_ok()}/{TARGET} idle={idle} cursor={cursor}")

    final = n_ok()
    log(f"DONE n={final}")
    print(f"FINAL_COUNT={final}")
    return 0 if final >= TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
