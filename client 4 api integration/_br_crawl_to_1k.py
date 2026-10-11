# -*- coding: utf-8 -*-
"""Push BR deliverable get_pc to 1000 — proven path: login jar + strip AC_CERT + SPA intercept.

Fixes vs scale_regen: do NOT abort shift on passable /verify/traffic; only stop on
traffic/error or captcha. Parallel workers with distinct account jars + sticky SIDs.
Logs progress to br_crawl_to_1k.log.
"""
from __future__ import annotations

import json
import random
import re
import string
import sys
import tempfile
import time
from datetime import datetime, timezone
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

DOM = "shopee.com.br"
DELIV = HERE / "br_deliverable_getpc"
URLS = HERE / "br_active_product_urls_100k.txt"
SESS = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
LOG = HERE / "br_crawl_to_1k.log"
BR_TEST = HERE / "BR TEST.txt"
TARGET = 1000
MIN_ITEM_KEYS = 40

ACCOUNT_JARS = [
    "br_1706801030_ydvUxsIK_live.json",
    "br_1706785017_CoOHjjSQ_live.json",
    "br_1706784820_OUk7hm0A_live.json",
    "br_1705283243_F3zrbhjv_live.json",
    "br_1707139756_0EAyNQaW_live.json",
    "br_1707407334_mRNUQARQ_live.json",
    "br_1707408898_LAxNlL5a_live.json",
    "br_5196720216_hsRjFTxp_live.json",
]

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Stick to the proven 1024proxy geo that previously yielded get_pc.
CITIES = [
    ("São Paulo", "Barueri"),
]


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_full(data: dict | None) -> bool:
    if not isinstance(data, dict):
        return False
    item = data.get("item")
    if not isinstance(item, dict) or len(item) < MIN_ITEM_KEYS:
        return False
    return sum(1 for k in ("product_price", "product_images", "product_meta", "account") if k in data) >= 2


def n_ok() -> int:
    """Fast unique count: filenames matching output_{shop}_{item}.json with parseable item."""
    n = 0
    for p in DELIV.glob("output_*.json"):
        if not re.match(r"output_\d+_\d+\.json", p.name):
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            payload = doc.get("data") if isinstance(doc.get("data"), dict) else doc
            item = payload.get("item") if isinstance(payload, dict) else None
            if isinstance(item, dict) and len(item) >= MIN_ITEM_KEYS:
                n += 1
        except Exception:
            continue
    return n

def have_ids() -> set[tuple[str, str]]:
    s: set[tuple[str, str]] = set()
    for f in DELIV.glob("output_*.json"):
        m = re.match(r"output_(\d+)_(\d+)\.json", f.name)
        if m:
            s.add((m.group(1), m.group(2)))
    return s


def parse_ids(u: str):
    m = re.search(r"i\.(\d+)\.(\d+)", u) or re.search(r"/product/(\d+)/(\d+)", u)
    return (m.group(1), m.group(2)) if m else None


def restore_barueri_proxies(n: int = 50) -> None:
    """Force the proven Barueri sticky format (sibling scripts keep rewriting the pool)."""
    import random, string
    def sid() -> str:
        return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))
    lines = []
    for _ in range(n):
        user = f"gdxc12191-region-BR-st-São Paulo-city-Barueri-sid-{sid()}-t-120"
        lines.append(f"us.1024proxy.io:3000:{user}:qadnrubc")
    (ROOT / "proxy(BR shopee).txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    text = BR_TEST.read_text(encoding="utf-8")
    out = []
    in_p = False
    pi = 0
    for line in text.splitlines():
        if line.strip().startswith("Proxy:"):
            out.append(line)
            in_p = True
            continue
        if in_p:
            if not line.strip() or line.strip().startswith("Test Url"):
                in_p = False
                out.append(line)
                continue
            if pi < len(lines):
                out.append(lines[pi])
                pi += 1
            continue
        out.append(line)
    BR_TEST.write_text("\n".join(out) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()
    log(f"restored {n} Barueri sticky proxies")


def rotate_proxies(n: int = 50) -> None:
    restore_barueri_proxies(n)


def load_jar(name: str | None = None) -> tuple[str, dict]:
    names = [name] if name else ACCOUNT_JARS
    for nm in names:
        p = SESS / nm
        if not p.is_file():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            ck = doc.get("cookies") or {}
            if ck.get("SPC_U"):
                return nm, dict(ck)
        except Exception:
            continue
    # fallback BR TEST
    text = BR_TEST.read_text(encoding="utf-8")
    jar = {}
    for line in text.splitlines():
        if "SPC_F=" in line or "AC_CERT_D=" in line:
            for part in line.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    jar[k] = v
            break
    return "br_test", jar


def save_full(j: dict, fallback_url: str) -> bool:
    """Save full get_pc envelope into deliverable naming scheme."""
    # Accept either raw API body {error,data} or wrapped deliverable
    if "data" in j and isinstance(j["data"], dict) and "item" in j["data"]:
        err = j.get("error")
        data = j["data"]
    else:
        return False
    if err not in (0, None):
        return False
    if not is_full(data) and not (isinstance(data.get("item"), dict) and len(data["item"]) >= MIN_ITEM_KEYS):
        return False
    item = data["item"]
    shop = str(item.get("shop_id") or item.get("shopid") or "")
    it = str(item.get("item_id") or item.get("itemid") or "")
    if not shop or not it:
        return False
    out = DELIV / f"output_{shop}_{it}.json"
    if out.is_file():
        return False
    payload = {
        "task_link": fallback_url,
        "token": "br_crawl_to_1k",
        "api_key": "br_crawl_to_1k",
        "bff_meta": {"extract_method": "browser_get_pc"},
        "error": None,
        "error_msg": None,
        "data": data,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return True


def run_shift(jar_name: str, jar: dict, urls: list[str], *, worker_id: int, quota: int = 30) -> dict:
    from patchright.sync_api import sync_playwright

    work = dict(jar)
    # Proven: keep login, strip burned device cert so browser remints on this fingerprint
    for k in ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A"):
        work.pop(k, None)
    work = enforce_br_locale(normalize_br_cookies(work, fresh_cert=False))

    proxy, _, sub = mint._proxy_dict("BR")
    ua = random.choice(UAS)
    stats = {"ok": 0, "fail": 0, "risk": False, "reason": None}
    log(f"W{worker_id} shift jar={jar_name} SPC_U={work.get('SPC_U')} urls={len(urls)} sub=...{(sub or '')[-36:]}")

    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix=f"br1k_w{worker_id}_")
    cx = br_context_kwargs()
    try:
        ctx = p.chromium.launch_persistent_context(
            udd,
            channel="chrome",
            headless=False,
            proxy=proxy,
            viewport={"width": random.choice([1366, 1440, 1280]), "height": random.choice([768, 900, 800])},
            locale=cx["locale"],
            timezone_id=cx["timezone_id"],
            user_agent=ua,
            extra_http_headers=cx["extra_http_headers"],
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        ctx.add_cookies(pw_cookie_list(work, fresh_cert=False))
        page = ctx.new_page()
        got_this = {"n": 0}

        def on_resp(r):
            try:
                if "/api/v4/pdp/get_pc" not in r.url or r.status != 200:
                    return
                j = r.json()
                if save_full(j, r.url):
                    got_this["n"] += 1
                    stats["ok"] += 1
                    log(f"W{worker_id} OK +1 shift_ok={got_this['n']} total={n_ok()}")
            except Exception:
                pass

        page.on("response", on_resp)
        try:
            human.warm_home(page, DOM)
        except Exception as e:
            log(f"W{worker_id} warm err {e}")
        for _ in range(4):
            u = page.url or ""
            if "/verify/traffic/error" in u:
                stats["risk"] = True
                stats["reason"] = "traffic_error_home"
                break
            if "/verify/traffic" in u:
                mint._wait_traffic_pass(page, DOM, secs=25)
            else:
                break

        if stats["risk"]:
            return stats

        for i, url in enumerate(urls):
            if n_ok() >= TARGET or got_this["n"] >= quota:
                break
            if stats["risk"]:
                break
            try:
                page.goto(url, wait_until="commit", timeout=45000)
            except Exception:
                pass
            deadline = time.time() + 32
            saw_ok = got_this["n"]
            while time.time() < deadline:
                u = page.url or ""
                if "/verify/captcha" in u:
                    stats["risk"] = True
                    stats["reason"] = "captcha"
                    break
                if "/verify/traffic/error" in u:
                    stats["risk"] = True
                    stats["reason"] = "traffic_error"
                    break
                if "/verify/traffic" in u:
                    # Passable challenge — wait, do NOT kill shift
                    mint._wait_traffic_pass(page, DOM, secs=20)
                    try:
                        page.goto(url, wait_until="commit", timeout=45000)
                    except Exception:
                        pass
                if got_this["n"] > saw_ok:
                    break
                try:
                    page.wait_for_timeout(350)
                except Exception:
                    time.sleep(0.35)
            if got_this["n"] == saw_ok:
                stats["fail"] += 1
            try:
                human.idle(page, 0.6, 1.8)
            except Exception:
                time.sleep(0.8)

        # persist live cookies for this account
        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        live = SESS / f"br_w{worker_id}_live.json"
        live.write_text(json.dumps({"cookies": ck, "account": jar_name, "ts": time.time()}, ensure_ascii=False), encoding="utf-8")
        # also update source jar AC_CERT if newly minted
        if ck.get("AC_CERT_D"):
            src = SESS / jar_name
            if src.is_file():
                try:
                    doc = json.loads(src.read_text(encoding="utf-8"))
                    doc.setdefault("cookies", {}).update({k: ck[k] for k in ck if k.startswith("AC_CERT") or k in ("SPC_SI", "SPC_SEC_SI")})
                    src.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
        log(f"W{worker_id} shift done ok={stats['ok']} fail={stats['fail']} risk={stats['risk']}/{stats['reason']} total={n_ok()}")
        try:
            ctx.close()
        except Exception:
            pass
    except Exception as e:
        log(f"W{worker_id} EXC {e}")
        stats["reason"] = str(e)[:80]
    finally:
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

    worker_id = 0
    jar_name = None
    for a in sys.argv[1:]:
        if a.startswith("--worker="):
            worker_id = int(a.split("=", 1)[1])
        elif a.startswith("--jar="):
            jar_name = a.split("=", 1)[1]

    DELIV.mkdir(exist_ok=True)
    # Append-only log — never wipe (other workers / supervisors may share it)
    log(f"=== crawl_to_1k W{worker_id} boot ===")
    # Rotate pool only once at boot for worker 0; others reuse current pool
    if worker_id == 0 and "--no-rotate" not in sys.argv:
        rotate_proxies(60)

    done = n_ok()
    log(f"W{worker_id} start n={done}/{TARGET} jar_pref={jar_name}")
    if done >= TARGET:
        log(f"W{worker_id} already at target")
        return 0

    # pick jar for this worker
    if jar_name:
        nm, jar = load_jar(jar_name)
    else:
        nm = ACCOUNT_JARS[worker_id % len(ACCOUNT_JARS)]
        nm, jar = load_jar(nm)

    shards = 1
    for a in sys.argv[1:]:
        if a.startswith("--shards="):
            shards = max(1, int(a.split("=", 1)[1]))

    have = have_ids()
    pending = []
    for line in URLS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        ids = parse_ids(line)
        if not ids or ids in have:
            continue
        sid, iid = int(ids[0]), int(ids[1])
        if shards > 1 and ((sid + iid) % shards) != (worker_id % shards):
            continue
        pending.append(line)
    log(f"W{worker_id} pending={len(pending)} shards={shards} using {nm}")

    cursor = 0
    idle = 0
    max_idle = 6
    while n_ok() < TARGET and cursor < len(pending) and idle < max_idle:
        # smaller chunks — exit before heat
        chunk = pending[cursor : cursor + 20]
        cursor += 20
        if not chunk:
            break
        nm, jar = load_jar(nm)
        stats = run_shift(nm, jar, chunk, worker_id=worker_id, quota=18)
        if stats["ok"] > 0:
            idle = 0
            time.sleep(8)  # soft rest after healthy shift
        else:
            idle += 1
            # refresh full sticky SID pool only every other idle (avoid shrinking pool)
            if idle % 2 == 0:
                try:
                    rotate_proxies(50)
                except Exception:
                    pass
            time.sleep(15 + idle * 5)
        if stats["risk"]:
            time.sleep(20)
        log(f"W{worker_id} progress total={n_ok()}/{TARGET} idle={idle} cursor={cursor}")

    final = n_ok()
    log(f"W{worker_id} DONE n={final}/{TARGET} reached={final >= TARGET}")
    print(f"FINAL_COUNT={final} REACHED_1000={str(final >= TARGET).lower()}")
    return 0 if final >= TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
