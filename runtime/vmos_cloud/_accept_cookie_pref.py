# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
sys.path.insert(0, str(ROOT / "client 4 api integration"))
os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"

from br_emulator.vmos_app_capture import (
    SHOPEE_PKG,
    _log,
    _vu,
    ensure_device,
    launch_shopee,
    _sh,
)

XML = r"""<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="COOKIE_PREFERENCE_RESPONSE_KEY">{&quot;data&quot;:{&quot;preferences&quot;:[{&quot;category&quot;:1,&quot;status&quot;:1},{&quot;category&quot;:2,&quot;status&quot;:1},{&quot;category&quot;:3,&quot;status&quot;:1},{&quot;category&quot;:4,&quot;status&quot;:1},{&quot;category&quot;:5,&quot;status&quot;:1}],&quot;need_consent&quot;:false,&quot;allowed_fe_set_cookies&quot;:[&quot;UA&quot;,&quot;SPC_F&quot;,&quot;SPC_R_T_ID&quot;,&quot;SPC_R_T_IV&quot;,&quot;csrftoken&quot;,&quot;SPC_DID&quot;,&quot;SPC_RNBV&quot;,&quot;language&quot;,&quot;shopee_tob_token&quot;,&quot;shopee_token&quot;,&quot;SPC_CLIENTID&quot;,&quot;root_csrftoken&quot;,&quot;SPC_CDS&quot;,&quot;HAS_BEEN_REDIRECTED&quot;,&quot;AC_CERT_D&quot;,&quot;shopee_webUnique_ccd&quot;,&quot;shopee_app_version&quot;,&quot;shopee_rn_bundle_version&quot;,&quot;shopee_rn_version&quot;,&quot;G_AUTHUSER_H&quot;,&quot;G_ENABLED_IDPS&quot;,&quot;credit_token&quot;,&quot;target_rn_version&quot;,&quot;ssr-tz&quot;,&quot;sense_sa_r&quot;]},&quot;error&quot;:0}</string>
</map>
"""


def main() -> int:
    serial = ensure_device()["serial"]
    path = "/data/data/com.shopee.br/shared_prefs/COOKIE_MANAGER_PREF.xml"
    tmp = "/data/local/tmp/COOKIE_MANAGER_PREF.xml"
    Path(r"D:\Luxury\Great Scraper2\Great Scraper\runtime\vmos_cloud\_cookie_pref.xml").write_text(
        XML, encoding="utf-8"
    )
    from br_emulator import vmos_cloud
    adb = vmos_cloud.adb_bin()
    import subprocess
    p = subprocess.run(
        [adb, "-s", serial, "push",
         r"D:\Luxury\Great Scraper2\Great Scraper\runtime\vmos_cloud\_cookie_pref.xml", tmp],
        capture_output=True, text=True,
    )
    _log((p.stdout or "") + (p.stderr or ""))
    _sh(serial, f"am force-stop {SHOPEE_PKG}")
    out = _vu(
        serial,
        f"cp {tmp} {path} && chmod 660 {path} && chown $(stat -c %u:%g /data/data/com.shopee.br) {path}",
        timeout=15,
    )
    _log(out or "pref 기록")
    launch_shopee(serial)
    _log("쿠키 동의 로컬 수락 후 홈 재기동")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
