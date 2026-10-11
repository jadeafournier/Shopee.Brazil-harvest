# Shopee iOS/모바일 플릿 (한국어 가이드)

Client 1 메시지: *「기술팀이 iOS APP 방안으로 하루 100만 건을 안정적으로 수집」*

이 폴더는 그 운영 모델을 **우리 시스템**에 이식한 것입니다.

---

## 1. Client 1 iOS APP 방안이란?

| 요소 | 내용 |
|------|------|
| 채널 | 실제 iOS Shopee 앱과 동일한 API (`X-API-SOURCE: rn`) |
| 서명 | 기기/Frida가 만든 `x-sap-sec` 등 **앱 서명** |
| 규모 | 수천 세션 × 높은 RPM → 일 100만 가능 |
| 브라우저 | Chrome 헤디드 불필요 → CPU·탐지 부담 적음 |

---

## 2. 우리 시스템에 구현된 것

| 구성 | 경로 | 역할 |
|------|------|------|
| 모바일 클라이언트 | `ios_client.py` | iOS/RN UA + item/get · get_rw · get_pc |
| 플릿 실행기 | `fleet_runner.py` | 병렬 워커, 한국어 로그, Client 1 봉투 저장 |
| 폴백 큐 | `br_ios_fallback_queue.txt` | 서명 차단 URL → 브라우저 크롤로 넘김 |

**하지 않는 것:** 앱 서명 위조, CapSolver로 BR 캡차 우회, 계정 과열 재시도

---

## 3. 전체 프로세스 (한국어)

```
① 쿠키 준비
   BR TEST.txt 에 로그인 쿠키 붙여넣기

② 모바일 프로브 (필수)
   python -m br_ios_fleet.fleet_runner --probe
   → 성공: 모바일 경로로 대량 수집 가능
   → 서명필요(90309999): Client 1식 서명기 필요 → 브라우저 폴백

③-A 모바일 플릿 (프로브 성공 시)
   python -m br_ios_fleet.fleet_runner --urls br_active_product_urls_100k.txt --workers 50

③-B 브라우저 폴백 (서명 차단 시 / iOS 기기 없음) — 검증된 무기기 경로
   python _br_no_device_run.py --all
   # 또는 단계별: URL 수확 → 리스팅 카드 → 브라우저 get_pc/get_rw/HTML
   # 상세: README_NO_DEVICE_KO.md

④ URL 수확 (상품 링크 100,000)
   python _br_url_harvest_run.py --target 100000 --workers 3 --fast
```

---

## 4. 하루 100만에 필요한 현실적 조건

모바일 API가 **서명 없이** 통과하지 않으면, 아래가 추가로 필요합니다:

1. **iOS 기기 팜** 또는 **Frida/앱 서명 서버** (Client 1 팀이 가진 핵심)
2. 계정 수천 개 + 회전 정책
3. 모바일 프록시 대량

현재 저장소에는 Frida/APK/unidbg가 **의도적으로 제거**되어 있습니다 (`DEPLOYMENT.md` 참고).  
서명기를 제공받으면 `ios_client.py`에 헤더 주입 슬롯만 연결하면 플릿이 바로 확장됩니다.

---

## 5. 명령어 치트시트

```powershell
cd "D:\Great Scraper\Great Scraper"

# 1) iOS/모바일 프로브
.\.venv\Scripts\python.exe -m br_ios_fleet.fleet_runner --probe

# 2) 소량 플릿 테스트
.\.venv\Scripts\python.exe -m br_ios_fleet.fleet_runner --urls "client 4 api integration\br_active_product_urls_1000.txt" --workers 10 --limit 30

# 3) URL 100k 수확 (진행 중)
.\.venv\Scripts\python.exe "client 4 api integration\_br_url_harvest_run.py" --target 100000 --workers 3 --fast

# 4) 브라우저 get_pc (폴백)
.\.venv\Scripts\python.exe "client 4 api integration\_br_long_crawl.py" --run-remaining --primary-only
```

모든 플릿/프로브 로그는 **한국어**로 출력됩니다.
