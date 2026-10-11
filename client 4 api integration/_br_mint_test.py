# -*- coding: utf-8 -*-
"""Decisive test: mint ONE fresh BR AC_CERT_D device cert (no login) via
captcha_mint (proxy rotation + local CV captcha solve), then report whether the
freshly minted cert yields get_pc error=0. This validates the entire non-login
solution before scaling to a cert pool."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_probe import BR_TEST, parse_br_test  # noqa: E402
import proxy_pool  # noqa: E402
import captcha_mint as mint  # noqa: E402


def _log(m):
    print(m, flush=True)


def _sync_proxies():
    ck, proxies, _ = parse_br_test(BR_TEST)
    px = ROOT / "proxy(BR shopee).txt"
    if proxies:
        px.write_text("\n".join(proxies) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()
    return ck, proxies


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ck, proxies = _sync_proxies()
    _log(f"BR proxies loaded: {len(proxies)} | seed cookies keys: {len(ck)}")
    # seed with BR_TEST cookies (mint strips AC_CERT_* itself)
    t0 = time.time()
    res = mint.mint("BR", ck, save_shots=True, verbose=True)
    dt = int(time.time() - t0)
    minted = bool((res.get("cookies") or {}).get("AC_CERT_D"))
    _log("=" * 60)
    _log(f"MINT RESULT ({dt}s): ok={res.get('ok')} reason={res.get('reason')} "
         f"getpc_err={res.get('getpc_err')}")
    _log(f"  AC_CERT_D minted? {'YES' if minted else 'no'}")
    _log(f"  state_dist: {res.get('state_dist')}")
    if minted:
        # persist the minted cert to a fresh device session file
        out = ROOT / "shopee_getpc_crawler" / "sessions" / "BR"
        out.mkdir(parents=True, exist_ok=True)
        name = f"br_mint_{int(time.time())}"
        (out / f"{name}.json").write_text(json.dumps(
            {"account_name": name, "region": "BR",
             "cookies": res["cookies"]}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        _log(f"  saved fresh device session: {name}.json")
    return 0 if minted else 3


if __name__ == "__main__":
    raise SystemExit(main())
