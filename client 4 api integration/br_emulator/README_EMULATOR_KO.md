# Shopee BR 에뮬레이터 수집 (실기기 없이 앱 서명 확보)

실제 iOS/Android 기기를 제공할 수 없는 조건에서, **안드로이드 에뮬레이터에
설치한 진짜 Shopee 앱**을 "서명기(기기 대체)"로 사용해 모바일 API 벽
(`90309999 need_app_sign`)을 우회합니다. 서명을 **위조하지 않고**, 앱이 스스로
만든 정상 요청/응답을 사용합니다.

---

## 왜 에뮬레이터인가

| 벽 | 원인 | 에뮬 해법 |
|----|------|-----------|
| `90309999` | get_pc/item_get 에 앱 서명(`x-sap-sec`, `af-ac-enc-dat`) 필요 | 에뮬 안 앱이 스스로 서명 |
| curl 플릿 실패 | 서명기 부재 | 앱이 서명 → 응답 캡처(mitm) 또는 서명 함수 후킹(frida) |
| 브라우저 캡차로 막힌 쿠키 | 웹 안티봇 | 모바일 채널에서 재사용 시도(낭비 없음) |

---

## 두 가지 방법

### 방법 A — mitm 응답 캡처 + adb 딥링크 (기본 · 견고)
앱이 상품을 열 때 스스로 서명해 쏘는 `get_pc` 응답을 프록시에서 그대로 저장.
`adb`로 URL 목록을 하나씩 앱에 열게 해서 전량 순회. **서명 리버싱 불필요.**

### 방법 B — Frida 앱 서명 + 모바일 플릿 (스케일)
Frida로 앱의 서명 함수를 후킹해 임의 요청에 서명을 생성 → `ios_client` 의
`signer` 슬롯에 주입 → curl 모바일 플릿을 고속 병렬로. **서명 함수 확정 필요.**

---

## 사전 준비 (사용자 측)

1. **에뮬레이터** 설치: LDPlayer / Nox / Genymotion / Android Studio AVD 중 택1
   - 방법 B(Frida)는 **루팅 가능** 에뮬 권장(LDPlayer/Genymotion 는 루트 쉬움).
2. 에뮬에 **Shopee 앱(BR)** 설치 후 **로그인**(BR 계정).
3. 에뮬을 adb 로 연결: `adb connect 127.0.0.1:5555` (에뮬별 포트 상이).
4. (방법 A) 에뮬 **Wi-Fi 프록시** = 이 PC의 IP `:8080`, `http://mitm.it` 에서
   **mitmproxy CA 설치**. Android 7+ 는 앱이 사용자 CA 를 무시하므로
   **시스템 CA 로 승격**(루트) 또는 **frida SSL-unpinning/objection** 필요.
5. (방법 B) 에뮬에 아키텍처 맞는 **frida-server** 푸시 후 실행:
   ```
   adb push frida-server /data/local/tmp/
   adb shell "chmod 755 /data/local/tmp/frida-server && /data/local/tmp/frida-server &"
   ```
   frida-server 버전 = 이 PC frida 버전(17.x)과 일치시킬 것.

---

## 실행

모두 저장소 루트에서 (`D:\Great Scraper\Great Scraper`). 경로에 공백이 있으므로
파일 경로 방식으로 호출합니다(간결하게 쓰려면 `cd "client 4 api integration"`
후 `-m br_emulator.run_emulator ...` 도 가능):

```powershell
$PY = ".\.venv\Scripts\python.exe"
$APP = "client 4 api integration\br_emulator\run_emulator.py"

# 0) 환경 점검
& $PY $APP check

# 1) 방법 A — 캡처 순회 (mitmdump 자동 기동 + adb 순회)
& $PY $APP capture

#    옵션: 리스팅에서 URL 추가 수확 / 차단 쿠키 주입 / 상세 로그
& $PY $APP capture --harvest-urls --verbose

# 2) 방법 B — 서명 함수 탐색(먼저 후보 확인)
& $PY "client 4 api integration\br_emulator\signer_bridge.py" --spawn com.shopee.br --watch 90
#    로그의 '서명 헤더'/'후보 클래스'를 보고 frida_signer.js 의 SIGN_SPEC 확정

# 3) 방법 B — 서명 확정 후 모바일 플릿(차단쿠키 결합)
& $PY $APP fleet --workers 8

# 현황
& $PY $APP status
```

---

## 저장 형식 / 하류 통합

- 출력: `client 4 api integration/br_emulator_output/output_<shop>_<item>.json`
- 봉투: 브라우저/iOS 플릿과 동일한 Client 1 봉투(`task_link`, `data`, `source`,
  `captured_at`). `source` 는 `emulator_get_pc` / `emulator_mobile_pdp_get_pc` 등.
- 인덱스: `br_emulator_output/captured_index.jsonl`
- URL 소스: `br_active_product_urls_100k.txt` (다른 무기기 경로와 공유, 중복 방지)

---

## 차단 쿠키를 낭비 없이 쓰는 법

- **방법 A**: `--inject-cookies <jar.json>` 로 웹뷰 API 요청 `Cookie` 헤더에
  차단 쿠키를 보강(네이티브 앱 인증은 건드리지 않음).
- **방법 B**: `emulator_fleet` 이 계정 풀의 **모든 jar(차단 포함)** 를 회전
  사용(`--no-blocked` 로 제외 가능). 모바일 채널은 웹 캡차 벽과 분리돼 있어
  브라우저에서 막힌 쿠키도 재활용될 수 있습니다.

---

## 한계 / 주의

- 방법 A 처리량은 "앱이 상품을 여는 속도"에 종속(사람형 페이싱 적용). 계정/기기
  과열 방지를 위해 긴 휴식을 주기적으로 넣습니다.
- 방법 B 는 서명 함수가 앱 버전마다 달라 **탐색→SIGN_SPEC 확정**이 선행돼야
  스케일이 납니다. 확정 전에는 `need_app_sign` 이 나며 자동으로 방법 A 를
  권장합니다.
- 서명 위조·캡차 우회는 하지 않습니다(계정 보호 최우선).
```
