# VMOS BR 수집 — 출구(Egress)·세션·운영 정리 (2026-10-03)

이 문서는 Shopee Brazil **클라우드폰 + mitm** 수집 파이프라인에서, 대화·검증·코드 변경을 통해 확정한 내용을 **운영·개발 참고용**으로 모았습니다.  
RAR 백업 안의 **웹 크롤러(`shopee_getpc_crawler` + `proxy_pool`)** 와 **VMOS 앱 캡처(`vmos_app_capture` + 기본방식)** 는 **별개**입니다. 기본방식은 **외부 BR 프록시 풀을 기본으로 쓰지 않습니다.**

---

## 1. 전체 데이터 경로

```text
VMOS 폰 Shopee BR 앱 (요청 서명)
    → adb reverse: 폰 127.0.0.1:<mitm_port> → PC mitmdump
    → settings global http_proxy = 127.0.0.1:<mitm_port>
    → (선택) mitm upstream → 출구 A/B/C 에 따라 PC / 폰 gost / 외부 프록시
    → mitm_capture.py 가 pdp/get 등 응답 JSON 저장
    → br_deliverable_received/active/{shop_id}_{item_id}.json
```

- PC는 **서명을 만들지 않음**. 앱이 낸 HTTPS를 mitm이 복호화·캡처합니다.
- 로그인·세션 **보관** 시에는 README대로 **mitm 끔 + http_proxy `:0`** (로그인 구간 CA/프록시 간섭 방지).

관련 코드: `client 4 api integration/br_emulator/vmos_app_capture.py`, 런처 `runtime/vmos_cloud/_run_list.py`.

---

## 2. 기본방식(이전 개발자) 요약

| 항목 | 값 |
| --- | --- |
| `--method` | `기본방식` |
| 상류 프록시 | `no_upstream=True` → **1024proxy/풀 자동 미사용** (`--no-proxy`) |
| human 모드 | off, `bounce_every=4`, `gap_s=0.4`, `wait_s=10` 등 |
| 세션 | `sessions/{userid}.tgz` 로테이션, `ROTATE_EVERY=300` 저장 시 전환 |
| 418 | 캡cha 수동 대기(앱 재기동 없음), traffic error 시 새 로그인/세션 |

**오해 정리:** RAR의 `proxy(BR shopee).txt`, `shopee_getpc_crawler/proxy_pool.py`는 **PC 웹 크롤러**용입니다. VMOS **기본방식**과 혼동하지 않습니다.

---

## 3. 출구(Egress) 모드 A / B / C / AUTO

출구는 **Shopee가 보는 공인 IP**입니다. `--egress` 또는 `phones.json`의 `"egress"`, 환경변수 `SHOPEE_EGRESS`로 선택합니다.

### A — PC 회선 (기존·검증됨)

- mitm **upstream 없음** → 폰 앱 트래픽이 PC mitm을 거친 뒤 **이 PC의 공인 IP**로 나감.
- **실측:** 폰 자체 IP(예: BR 98.98.31.57)와 mitm `--no-proxy` egress(예: 핀란드 데이터센터 95.216.240.227)가 **다름** → A는 “폰 회선 직접”이 아님.
- 장점: 설정 단순, 프록시 비용 없음. PC IP가 깨끗할 때 유리.

### B — 폰 BR 회선 (on-phone gost)

- 경로: `폰 app → PC mitm(캡처) → upstream http://127.0.0.1:<fwd> → adb forward → 폰 gost :1080 → 폰 BR 회선`.
- 바이너리: `runtime/vmos_cloud/bin/gost` (go-gost v3.3.0 android_arm64).
- 설정: `runtime/vmos_cloud/bin/gost_phone.yaml` (리졸버 8.8.8.8 / 1.1.1.1 — Android에 `/etc/resolv.conf` 없어 gost 단독 DNS 실패 방지).
- **실측:** ipinfo egress = 폰 BR IP(98.98.31.57, São Paulo).
- **Windows:** Defender가 gost를 `HackTool:Linux/Gost`로 격리할 수 있음 → `runtime/vmos_cloud/bin` **Defender 제외** 필요. adb push 시 경로 공백(`Xiao Kim`) 이슈 주의.

### C — 외부 BR 주거용 프록시

- mitm `--mode upstream:http://user:pass@host:port`.
- 목록: 프로젝트 루트 `br_proxy.txt` (기본 1000줄, `host:port:user:pass`, proxy001 `zone_BR` + `ssid_*` 회전).
- 출구 검사: `_proxy_is_br()` — ipinfo로 **BR 여부만** 확인 (앱 서명 없는 raw pdp는 90309999가 나와 IP 품질 판별에 부적합).
- **C 모드 + 세션 전환:** `_relogin` / `ROTATE_EVERY` 전환 시 **`_rotate_c_proxy()`** 로 ssid(출구 IP)도 함께 회전.

### AUTO — A → B → C 순차 승급 (자원 절약)

- 시작: **A**(무료·단순).
- 현재 등급에서 pdp/418 등 **연속 차단**(`ESCALATE_BLOCKS=2`) 시 **B → C** 승급, mitm/gost/forward 재구성.
- **C 단계:** 상품당 최대 `MAX_ROTATE_PER_ITEM=3` 회 BR IP 교체, 한계 시 상품 스킵.
- A·B·C 모두 막히면 **수집 중지**(세션 재수집 필요 — 사용자 운영 규칙).

`phones.json` 예:

```json
{
  "name": "Samsung Galaxy A53 3",
  "shard": 2,
  "port": "8090",
  "adb_port": "5073",
  "egress": "AUTO"
}
```

C/AUTO 시 `proxy_file` 생략하면 **`D:\Xiao Kim\Great Scraper\br_proxy.txt`** 자동 사용 (`_run_list.py`).

CLI 예:

```text
python -m br_emulator.vmos_app_capture --method 기본방식 --no-proxy --go \
  --egress AUTO --proxy-file "D:\Xiao Kim\Great Scraper\br_proxy.txt" \
  --port 8090 --urls list.txt
```

---

## 4. 세션(로그인) 보관·복원·병렬 분배

### 저장

- 위치: `runtime/vmos_cloud/sessions/{userid}.tgz` (+ `{userid}.json` 메타).
- 내용: `shared_prefs`, `databases`, `files/mmkv` 등 — **비밀번호 저장 없음**.
- 메타 JSON에는 **어느 폰에서 만들었는지(pad/serial) 기록하지 않음**.

### 복원

- `restore_session(serial, userid)` — **연결된 아무 폰**에 push 후 앱 데이터 복원.
- 성공 조건: 복원 후 `_account_userid_on_device(serial) == userid`.

### 병렬 수집 시

- 세션 풀은 **PC 공용**.
- `_run_list.sessions_for(shard)`: userid 정렬 후 **`ids[shard::n]`** (폰 대수 `n`으로 라운드로빈).
- 각 폰 harvest에 `SHOPEE_SESSION_ALLOW=...` 로 **담당 userid만** 로테이션.
- 동시에 같은 세션을 여러 폰이 쓰지 않도록 **분할**하는 것이지, “만든 폰 전용”은 **아님**.

세션 캡처 절차: `ARCHITECTURE_SESSION_KO.md`, `_store_sessions.py`.

---

## 5. 차단·미저장 진단 (학습한 구분)

| 현상 | mitm/로그 | 해석 |
| --- | --- | --- |
| `verify/traffic/error`, `TRAFFIC-ERROR` | traffic error URL | **출구(IP/프록시) 쪽** — 페이지 사용 불가 |
| `pdp/get st=418 err=90309999` | PDP-BLOCK | anti-crawler / 캡cha / 계정·상품 |
| `error=266900002` (HTTP 200 body) | API err | 상품/요청 문제 |
| pdp/get 미수신 | 타임아웃 | 네트워크·프록시 지연·앱 UI 등 |
| 익명 curl → 90309999 | 서명 없음 | **IP 판별용으로 curl만 쓰면 안 됨** (앱 서명 필요) |

### C 모드에서 확인된 패턴 (중요)

동일 BR 주거용 프록시 egress에서:

- `native/homepage`, `account/basic/get_account_info` → **200**
- `pdp/get` → **418 / 90309999**

→ **출구 IP는 살아 있고**, 계정·앱은 동작하지만 **pdp(상품 상세) 권한/플래그**가 소진된 상태로 해석하는 것이 타당합니다.  
반복된 A 모드(데이터센터 IP) 수집 후 **7개 세션 모두 pdp만 막힌** 사례와 일치합니다.

### 기본방식 egress 라벨 (상태 JSON)

- A: `PC 회선 직접`
- B: `폰 BR 회선(gost)`
- C: `외부 BR 프록시`
- AUTO: `AUTO→...` (현재 단계 표시)

---

## 6. 출구 차단 시 세션 로테이션 금지 (세션 소진 방지)

**목적:** 막힌 IP/프록시에서 계정을 연달아 갈아 끼우며 **세션만 소진**하는 것을 방지.

**함수:** `egress_blocked_since(offset, serial)`

**True 조건 (명확한 traffic 차단만):**

1. mitm 로그에 `verify/traffic/error` 또는 `TRAFFIC-ERROR`
2. 화면/logcat `verify/traffic/error` (`_screen_traffic_error`)

**False (정상 운영 — 세션 전환 허용):**

- pdp/get **418·90309999** 만 있는 경우
- 캡cha 대기, `ROTATE_EVERY` 성공 후 전환, 연속 미저장 시 세션 전환( traffic error 없을 때)
- silent block 등 계정/상품 이슈

**동작:**

- `_relogin()` / `switch_saved_session(..., "차단"|"연속 미저장"|...)` — egress 차단이면 **세션 교환 생략**, `_handle_egress_block()` 으로 AUTO 승급·C IP 회전·`rotate_region` 프록시 교체.
- 출구 교체 불가 시 **중지**, 보관 세션 파일은 **유지**.

---

## 7. 미저장·중지 운영 규칙 (대화에서 확정)

1. **7개 로그인 세션이 전부 작동하지 않으면** 수집 중지 → 사용자가 **세션 재수집**.
2. 세션 소진과 출구 문제를 구분: traffic error → 출구 먼저; 홈·계정 200 + pdp 418 → **신선한 세션** 필요.
3. 대량 수집 권장: **`egress: AUTO`** 또는 **C + br_proxy.txt**, 세션 재수집 시 **처음부터 BR 쪽 egress**에 묶기.
4. 병렬: `phones.json` shard + `list_urls_{shard}.txt`, 모니터: `_watch_harvest.py`.

---

## 8. 주요 파일·스크립트

| 경로 | 역할 |
| --- | --- |
| `client 4 api integration/br_emulator/vmos_app_capture.py` | 캡처 엔진, egress A/B/C/AUTO, 세션·418·출구 정책 |
| `client 4 api integration/br_emulator/mitm_capture.py` | mitm addon, API 로그 |
| `runtime/vmos_cloud/_run_list.py` | 폰 연결, shard, `--egress`, harvest 실행 |
| `runtime/vmos_cloud/phones.json` | 폰 shard, port, adb_port, **egress**, proxy_file |
| `runtime/vmos_cloud/sessions/*.tgz` | 보관 세션 |
| `br_proxy.txt` | C/AUTO용 BR 프록시 목록 |
| `runtime/vmos_cloud/bin/gost` | B 모드 폰 프록시 |
| `runtime/vmos_cloud/bin/gost_phone.yaml` | gost DNS 리졸버 |
| `runtime/vmos_cloud/_watch_harvest.py` | 실시간 진행·속도 |
| `runtime/vmos_cloud/list_from_rar_1000.txt` 등 | URL 목록 |

검증용(운영 필수 아님): `_test_egress_B.py`, `_test_C_live.py`, `_test_auto_live.py`, `_test_proxy_br.py`.

---

## 9. mitm·포트·정리

- 폰당 mitm 포트: `phones.json` `port` (예: 8090).
- B 모드 forward: 기본 `port + 10000` → 폰 gost 1080.
- harvest 종료 시: `clear_proxy`, mitm stop, B 사용 시 `teardown_b_egress` (gost kill, forward 제거).
- 강제 중단 후 adb/SSH 끊김 시 폰에 gost·http_proxy 잔여 가능 → **다음 `connect_phone` + harvest 시작 시 재설정** (무해).

---

## 10. 권장 운영 체크리스트

- [ ] 세션: `_store_sessions.py`로 userid별 tgz 확보 (로그인 시 mitm/프록시 off).
- [ ] `phones.json` shard·egress·(C/AUTO) proxy 경로 확인.
- [ ] Defender: `runtime/vmos_cloud/bin` 제외 (gost).
- [ ] 첫 대량 전: 소량 URL + `_watch_harvest.py` 로 저장·418·traffic 비율 확인.
- [ ] AUTO 로그에서 `★ egress 승급 A → B → C`, `출구 차단 — 세션 유지` 메시지 확인.
- [ ] 전 세션 pdp 418 지속 시 **세션 재수집** (IP만 바꿔도 pdp 200 안 나올 수 있음).

---

## 11. README·다른 문서와의 관계

- 파이프라인 개요: `runtime/vmos_cloud/README.md`
- 세션 캡처·shard 표: `ARCHITECTURE_SESSION_KO.md`
- 3폰 setup: `SETUP_3PHONES_KO.md`
- **출구·AUTO·세션 소진 정책·검증 결과:** **본 문서**

---

## 12. 변경 이력 (요지)

| 날짜 | 내용 |
| --- | --- |
| 2026-10-03 | A/B/C egress 구현·실측; B gost+리졸버; C `br_proxy.txt`; AUTO A→B→C; C 세션별 IP 회전 |
| 2026-10-03 | egress 차단 시 세션 로테이션 금지 — **traffic error만** 출구 차단으로 한정 |
| 2026-10-03 | 기본방식 vs RAR 웹 crawler proxy_pool 구분 명시; pdp vs account API mitm 분석 반영 |

---

문서·코드 불일치 시 **`vmos_app_capture.py`와 `_run_list.py`가 우선**입니다. 이 문서는 그 시점의 운영 지식 스냅샷입니다.
