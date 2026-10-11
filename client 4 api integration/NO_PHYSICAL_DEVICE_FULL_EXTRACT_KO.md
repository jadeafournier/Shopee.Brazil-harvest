# 실기기 없이 쇼피 전체 구조 추출 — 가능 경로 지도

조건: **물리 폰/iPhone 제공 불가**. 목표: 가능한 한 **전체 구조**(이상적으로는 raw `get_pc`).

---

## 한 줄 판정

| 목표 | 실기기 없이 가능? | 현재 상태 |
|------|-------------------|-----------|
| **100% raw `get_pc` (error=0)** | **가능 후보만 있음** (에뮬=가상 기기) | Frida 서명·헤더 관찰까지; PDP 캡처/`error=0` 미완 |
| **전체 스키마 근사 (가격 포함 가능)** | 부분 가능 (TW SSR+BigGo 등) | 납품 가능하나 raw 아님 |
| **20만/일 + 100% raw** | 에뮬 서명기 완성 후에만 | 게이트 = 서명 재현 `error=0` 1건 |

실기기가 없어도 **안드로이드 에뮬레이터는 “가상 기기”**이므로 앱 서명 경로의 정식 대체재입니다.

---

## 경로 A — 에뮬레이터 앱 + 응답 캡처 (mitm / Frida) ★최우선

**원리:** 에뮬 안 진짜 앱이 스스로 서명해 호출 → 응답을 가로채 저장. 서명 위조 불필요.

| 항목 | 내용 |
|------|------|
| 정확도 | raw API와 동일 (앱이 받은 JSON) |
| 실기기 | 불필요 (AVD/LDPlayer 등) |
| 코드 | `br_emulator/mitm_capture.py`, `run_emulator.py capture`, `D:\emu\frida_agent\` |
| 현황 | ID 앱(`com.shopee.id`) 설치·홈 동작·`x-sap-ri`/`af-ac-enc-sz-token` 관찰. PDP `get_pc` URL 캡처는 아직 실패. BR APK 미설치 |
| 다음 | 딥링크/검색으로 PDP 강제 → mitm 또는 Frida peekBody로 저장 |

---

## 경로 B — 에뮬 Frida 서명기 + HTTP 워커 (스케일)

**원리:** Frida로 SHPSSDK `sign` → 헤더를 `signer_service`에 주입 → curl 워커 대량 호출.

| 항목 | 내용 |
|------|------|
| 정확도 | `error=0`이면 100% raw |
| 실기기 | 불필요 |
| 코드 | `D:\emu\frida_agent\sign_agent_compiled.js`, `br_emulator/signer_bridge.py`, `br_scale_1m/signer_service.py` |
| 현황 | `rpc.sign` 동작. 외부 HTTP 재현은 아직 `90309999` (헤더 맵/토큰 바인딩 미완) |
| 다음 | 경로 A에서 **완전한 get_pc 요청 헤더 세트** 확보 → 재현 |

---

## 경로 C — Unidbg / libshpssdk.so (기기 완전 배제)

**원리:** APK에서 뽑은 so를 Unidbg로 에뮬해 서명만 생성.

| 항목 | 내용 |
|------|------|
| 정확도 | softToken/init 완성 시 raw 가능 |
| 실기기 | 불필요 |
| 코드 | `D:\emu\shopee_signer\`, `libs_arm64/libshpssdk.so` |
| 현황 | init/crash·`90309999` — **acceptance 미달** |
| 다음 | 경로 A/B로 프로토콜 확정 후 Unidbg에 이식 (스케일용) |

---

## 경로 D — 브라우저 SPA + 로그인 쿠키 (에뮬 불필요)

**원리:** 헤드 Chrome이 페이지 네이티브 서명으로 `get_pc` intercept.

| 항목 | 내용 |
|------|------|
| 정확도 | ✅ 100% raw (증명됨, BR human raw ~104건) |
| 실기기 | 불필요 |
| 한계 | 구글/쇼피 계정·세션 — **20만/일 비현실** |
| 코드 | `_br_human_raw_100.py`, `br_human_raw_getpc\` |

---

## 경로 E — 로그인리스 SSR / BigGo (구조만)

| 항목 | 내용 |
|------|------|
| 정확도 | ❌ raw 아님 (`live_get_pc_api=false`) |
| 실기기 | 불필요 |
| 용도 | 스키마·일부 가격 근사 (TW) |
| 코드 | `tw_loginless_full_extract.py`, `tw_deliverable_getpc\` |

클라이언트가 **100% 정확 raw**를 요구하면 납품 경로로 불가.

---

## 경로 F — 리스팅 카드 / OG HTML

부분 필드만. 전체 구조·100% raw 아님.

---

## 실기기 없이 가야 할 실행 순서

```
1) 에뮬에 지역 앱 설치 (ID 있음 → BR APK 사이드로드)
2) 경로 A: PDP 열어 get_pc 응답 1건 저장  (증명)
3) 같은 요청의 전체 헤더 덤프
4) 경로 B: signer_service로 재현 → error=0
5) HTTP 워커로 20만/일 스케일
6) (선택) Unidbg로 서명기 기기 의존 제거
```

**중단 조건이 아닌 것:** 물리 폰 없음.  
**진짜 게이트:** 에뮬에서 `get_pc error=0` 1건 + 헤더 재현.

---

## 이 세션에서 돌리는 스크립트

- `D:\emu\no_device_pdp_burst.py` — 딥링크 버스트 + Frida 캡처  
- `D:\emu\frida_agent\` — Frida 17 Java bridge 컴파일 에이전트  
- `python -m br_emulator.run_emulator capture` — mitm 경로  

---

## 2026-07-26 추가 실험 결과 (실기기 없음)

| 실험 | 결과 |
|------|------|
| Frida SHPSSDK `rpc.sign` | ✅ JSON 서명 맵 생성 (`x-sap-ri` 포함) |
| 라이브 헤더 관찰 | ✅ `x-sap-ri`, `af-ac-enc-sz-token`, `af-ac-cli-id` |
| 외부 HTTP로 서명 재현 | ❌ `90309999` |
| 앱 프로세스 안 신규 OkHttpClient | ❌ `90309999` (쿠키 있어도) |
| heap OkHttp (interceptor=0) | ❌ `90309999` |
| heap OkHttp (interceptor=37) sync | ❌ `CanceledIOException` |
| UI/딥링크로 PDP | ❌ 홈에 머무르거나 블랙스크린; `get_pc` URL 미발생 |
| Unidbg shaded jar | ❌ init/softToken 미완 |
| 브라우저 쿠키 SPA | ✅ raw 증명됨 (스케일 불가) |
| TW 로그인리스 | ⚠️ 구조만, raw 아님 |

**발견:** RN PDP 모듈 존재  
`ItemDataModule.getItemData` / `PDPBusinessModule` / `halfpdp` — 다음 훅 타깃.

**실기기 없이 남는 유일한 raw 스케일 후보:** 에뮬 앱이 **스스로** 연 `get_pc` 응답 캡처(경로 A) → 헤더 고정 → 서명 서비스(경로 B).  
게이트는 여전히 **에뮬에서 PDP `get_pc` 1건**.  

---

## 저장소 전수 조사 요약 (탐색 에이전트)

| 경로 | 정확도 | 실기기 | 상태 |
|------|--------|--------|------|
| 브라우저 SPA `get_pc`+쿠키 | raw (~56/103) | 불필요 | 증명됨; 계정 상한 |
| 브라우저 익명 | 실패 | 불필요 | `90309999` |
| 에뮬 Frida | raw 목표 | 에뮬만 | 서명 OK, 재현 미완 |
| mitm 캡처 | raw 목표 | 에뮬만 | 코드 준비, PDP 저장 0 |
| Unidbg | raw 목표 | 불필요 | 헤더만, API 거부 |
| TW 로그인리스 | schema | 불필요 | ~209 구조 파일 |
| 리스팅 카드 | card | 불필요 | ~1.1k |
| ios_fleet | raw* | 서명기 필요 | 출력 0 |
| br_scale_1m | raw* | 에뮬/unidbg | 파이프라인만, 서명 미개통 |

**20만/일 갭:** 앱 서명 미개통 + BR APK 미설치 + 브라우저 쿼터.  
우선순위: BR APK 사이드로드 → PDP 강제 → 헤더 덤프 → `signer_service` → `worker_http`.

### 돌파 (2026-07-26 밤) — 와이어 헤더 캡처

`CallServerInterceptor` 훅으로 **앱이 실제로 보내는** `/api/v4/pdp/get` 최종 헤더 **19개** 확보  
(`D:\emu\pdp_wire_capture.json`).

포함: `x-sap-ri`, `af-ac-enc-sz-token`, `af-ac-cli-id`, `Cookie`, 해시 헤더명(`869b0ee`, `d593512f`, `de1287e9` 등).

응답은 에뮬 게스트에서 아직 **`error=90309999`** (헤더 세트는 맞는데 세션/기기 신뢰 부족 가능).  
다음: 로그인 쿠키를 앱에 주입하거나, 동일 헤더 맵으로 서명 RPC 재매핑 후 재시도.

