# -*- coding: utf-8 -*-
"""Decisive experiment: run the PROVEN batch crawler with a fully fresh,
login-less, cert-stripped identity (like a brand-new anonymous user) and see
whether the page's own SPA-signed get_pc returns full data on fresh BR IPs.

If yes -> the non-login solution is just: rotate fresh identities gently.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopee_getpc_crawler"))
sys.path.insert(0, str(ROOT / "client 4 api integration"))

from _br_probe import BR_TEST, parse_br_test, parse_url_ids  # noqa: E402
from br_cookies import normalize_br_cookies  # noqa: E402
import proxy_pool  # noqa: E402
import _br_batch_crawl as batch  # noqa: E402

STRIP = ("AC_CERT_D", "AC_CERT_S", "AC_CERT_A", "SPC_U", "SPC_ST", "SPC_EC", "SPC_R_T_ID", "SPC_R_T_IV")


def _log(m):
    print(m, flush=True)


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6

    cookies, proxies, _ = parse_br_test(BR_TEST)
    px = ROOT / "proxy(BR shopee).txt"
    if proxies:
        px.write_text("\n".join(proxies) + "\n", encoding="utf-8")
    proxy_pool.set_region("BR")
    proxy_pool._POOL = []
    proxy_pool.load()

    # fully fresh anonymous identity: strip login + device cert
    fresh = normalize_br_cookies(cookies, fresh_cert=False)
    for k in STRIP:
        fresh.pop(k, None)
    _log(f"fresh identity cookie keys ({len(fresh)}): {sorted(fresh)[:20]}")
    _log(f"  AC_CERT_D present? {'yes' if fresh.get('AC_CERT_D') else 'NO (fresh)'}  SPC_U? {fresh.get('SPC_U')}")

    # output to a probe dir so we don't touch the real dataset
    out = Path(__file__).resolve().parent / "br_fresh_probe"
    batch.OUT = out
    out.mkdir(parents=True, exist_ok=True)

    # pick N URLs not yet in the main dataset
    main_out = Path(__file__).resolve().parent / "br_batch_output_100k"
    urls_all = batch._load_urls(Path(__file__).resolve().parent / "br_active_product_urls_100k.txt")
    urls = []
    for u in urls_all:
        sid, iid = parse_url_ids(u)
        if not (main_out / f"output_{sid}_{iid}.json").is_file():
            urls.append(u)
        if len(urls) >= n:
            break
    _log(f"testing {len(urls)} fresh URLs")

    # probe many fresh IPs; do NOT stop on traffic so it rotates through IPs
    batch.TRAFFIC_STOP = False
    batch.CAPTCHA_STOP = False
    batch.ACCOUNT_CIRCUIT = 10 ** 9
    stats = batch.run_batch(urls, fresh, human_mode=False, stop_on_traffic=False, shift_quota=0)
    _log("=" * 60)
    _log(f"RESULT: ok={stats.get('ok')} fail={stats.get('fail')} "
         f"blocked_90309999={stats.get('blocked_90309999')} "
         f"traffic={stats.get('traffic_errors')} captcha={stats.get('captcha_hits')} "
         f"stop={stats.get('stopped_reason')} by_method={stats.get('by_method')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
