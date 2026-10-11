# -*- coding: utf-8 -*-
"""BigGo에서 모은 쇼피 대만 5,000개. 대만 VMOS 앱으로 수집한다.

사용자가 수집 시작을 말한 뒤에만 실행한다.

수집 방식 이름은 기본방식.
  --method 기본방식 이면 이후 코드가 바뀌어도
  상류 프록시 없음, 4건마다 재기동, 미저장이면 재기동, 418은 캡차 대기로 덮어쓴다.

모드(환경변수 HARVEST_MODE):
  resume  : 현재 로그인 세션 유지 (--go)  [기본]
  relogin : 앱 로그인 초기화 후 새 계정 수동 로그인 대기 → 수집
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(r"D:\Luxury\Great Scraper2\Great Scraper")
HERE = Path(__file__).resolve().parent
LOG = HERE / "tw_biggo_active_5000.log"
URLS = HERE / "biggo_shopee_tw_active_5000.txt"
OUT = ROOT / "client 4 api integration" / "tw_deliverable_getpc"

MODE = (os.environ.get("HARVEST_MODE") or "resume").strip().lower()

os.environ["VMOS_PAD_CODE"] = "AC20250306MHUQVN"
os.environ["SHOPEE_PKG"] = "com.shopee.tw"
os.environ["SHOPEE_WEB_HOST"] = "shopee.tw"
os.environ["SHOPEE_OUT_DIR"] = str(OUT)
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HARVEST_LOG"] = str(LOG)
os.environ.pop("HARVEST_HUMAN", None)
# 달궈진(차단된) 기존 계정은 로그인으로 인정하지 않는다.
prev = os.environ.get("BLOCKED_USERIDS") or ""
# 차단(90309999) 확인된 대만 계정들은 로그인으로 인정하지 않는다.
add = "15331967808,16578905866,16111404177"
os.environ["BLOCKED_USERIDS"] = ",".join(x for x in (prev, add) if x)
OUT.mkdir(parents=True, exist_ok=True)
with LOG.open("a", encoding="utf-8") as fh:
    fh.write(f"\n---- tw biggo 5000 · mode={MODE} · TW rotate proxy ----\n")

sys.path.insert(0, str(ROOT / "client 4 api integration"))
# 1024proxy 대만 출구는 상품 API 에서 90309999 가 바로 난다.
# 폰의 Taiwan Mobile 회선은 상품 페이지가 열리므로, 그 회선으로 mitm 만 붙여 저장한다.
argv = [
    "vmos_app_capture",
    "--method", "기본방식",
    "--no-proxy",
    "--keep-proxy",
    "--urls", str(URLS),
]
if MODE == "relogin":
    # 앱 데이터 초기화 → 새 계정 수동 로그인 대기 → 수집
    argv += ["--force-relogin", "--wait-login"]
else:
    argv += ["--go"]
sys.argv = argv

from br_emulator.vmos_app_capture import main

if __name__ == "__main__":
    raise SystemExit(main())
