# BR 데이터 추출 — iOS 기기 없이

실제 iOS 기기 / Frida 서명기가 없을 때 **가능한 모든 방법**만 사용합니다.

## 왜 모바일 API는 안 되나?

프로브 결과: 쿠키만으로 `item/get` · `get_rw` · `get_pc` → **`90309999` / `need_app_sign`**.  
앱 서명(`x-sap-sec` 등)은 **기기에서만** 만들 수 있고, 위조·CapSolver는 사용하지 않습니다.

## 구현된 무기기 경로

| 우선순위 | 방법 | 산출물 | 스크립트 |
|---------|------|--------|----------|
| 1 | URL 수확 (search/shop 인터셉트) | 상품 URL | `_br_url_harvest_run.py` |
| 2 | 리스팅 카드 (제목·가격·판매·평점) | `br_listing_cards.jsonl` | `_br_listing_cards.py` |
| 3 | 브라우저 PDP `get_pc` | 풀 Client-1 JSON | `_br_long_crawl.py` / `_br_batch_crawl.py` |
| 4 | 브라우저 PDP `get_rw` / `item/get` | SPA가 쏘면 자동 저장 | (배치에 통합) |
| 5 | HTML OG / JSON-LD | 부분 필드 | (배치에 통합) |

## 한 줄 실행

```powershell
cd "D:\Great Scraper\Great Scraper"

# 현황
.\.venv\Scripts\python.exe "client 4 api integration\_br_no_device_run.py" --status

# 전체 파이프라인 (URL → 카드 → PDP 시프트)
.\.venv\Scripts\python.exe "client 4 api integration\_br_no_device_run.py" --all

# 단계별
.\.venv\Scripts\python.exe "client 4 api integration\_br_no_device_run.py" --phase cards --card-target 50000
.\.venv\Scripts\python.exe "client 4 api integration\_br_no_device_run.py" --phase pdp --shift-quota 40
```

## 풀 PDP만 (계정 보호)

```powershell
.\.venv\Scripts\python.exe "client 4 api integration\_br_long_crawl.py" `
  --run-remaining --primary-only `
  --urls "client 4 api integration\br_active_product_urls_100k.txt" `
  --out "client 4 api integration\br_batch_output_100k" `
  --shift-quota 40
```

쿠키가 `traffic_error` / captcha 쿨다운이면 `--status`로 대기 후 재개하세요.

## 기대치 (현실)

| 목표 | 무기기에서의 현실 |
|------|------------------|
| URL 10만 | 가능 (브라우저 리스팅) |
| 카드 5만 (가격·제목) | 가능 (리스팅 API) |
| 풀 get_pc 10만/일 | **계정·브라우저 한도** — iOS 서명기 없이는 Client 1의 ~100만/일 불가 |
| 풀 get_pc / 시프트 | ~40건/계정 권장 (쿠키 보호) |

서명기(Frida/기기 팜)를 나중에 받으면 `br_ios_fleet` 의 `signer=` 슬롯에 연결하면 됩니다.
