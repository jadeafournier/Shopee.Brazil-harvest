# 보관 세션 복원 가이드 (운영)

Shopee BR 앱 **로그인 상태 스냅샷**(`sessions/{userid}.tgz`)을 VMOS 폰에 다시 올리는 방법과, 복원 후 **로그인 화면**이 뜨는 흔한 원인을 정리한다.

관련 문서:

- 세션 보관(신규 계정 넣기): `ARCHITECTURE_SESSION_KO.md`, `README.md` Sessions 절
- 수집 중 세션 교체·10h 잠재움: `DEFAULT_HARVEST_PRODUCTION_KO.md`, `session_use.py` (`SLEEP_S`)
- 출구·traffic·세션 보호: `EGRESS_SESSION_OPERATIONS_KO.md`

**상품 JSON**(`br_deliverable_received/active/`)과 **로그인 세션**(`runtime/vmos_cloud/sessions/`)은 별개다. 이 문서는 **세션(.tgz)** 만 다룬다.

---

## 1. 용어

| 용어 | 위치 | 의미 |
|------|------|------|
| 보관 세션 | `runtime/vmos_cloud/sessions/{userid}.tgz` | PC에 저장한 앱 데이터 일부(주로 `shared_prefs` 등). **비밀번호는 저장하지 않음** |
| 메타 | `sessions/{userid}.json` | userid, 크기, `saved_at` |
| 잠재움 | `sessions_quarantine/sleeping/` | 소진·차단 후 **10시간** (`SLEEP_S`) 동안 수집 풀에서 제외 |
| `--prime` | `_run_list.py` | 폰 shard마다 **할당된 목록**에서 **가장 최근** userid 1개를 복원 |
| shard 분배 | `sessions_for(shard)` | userid 정렬 후 `ids[shard :: 3]` — 0번 폰 1,4,7… / 1번 2,5,8… / 2번 3,6,9… |

한 폰·한 Shopee 앱 = **동시에 하나의 userid** 로그인 상태.

---

## 2. 복원 전 체크리스트

1. **세션 보관 루프**(`--sessions-start` / `_store_sessions.py`)가 **돌고 있지 않을 것**  
   - 수집과 **같은 폰에서 동시에** 하면 안 됨.  
   - 보관 중이면: `--sessions-stop` 후 복원·수집.

2. VMOS 해당 폰이 **ADB 연결됨** (`phones.json`의 `adb_port`, `connect_phone` / 콘솔 요약 표).

3. 복원할 `.tgz`가 **`sessions/`** 에 있을 것  
   - **10h 잠재움** 중이면 `sleeping/`에만 있을 수 있음 → 아래 §4 참고.

4. **수동 로그인·세션 보관** 할 때는 원칙적으로 **mitm 끔**, `http_proxy` **`:0`** (로그인 UI 깨짐 방지).  
   **수집 중**에는 mitm·프록시가 켜진 상태가 정상이다.

---

## 3. 3대 한 번에 복원 (`--prime`)

### 3.1 PowerShell

```powershell
cd "D:\Xiao Kim\Great Scraper"
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe runtime\vmos_cloud\_run_list.py --prime
```

### 3.2 운영 콘솔

- URL: `http://127.0.0.1:8799` (RDP로 접속한 **그 PC**에서만 `127.0.0.1` 동작)
- **세션** 관련 탭 → **「세션 복원(prime)」**

### 3.3 `--prime` 내부 동작 (요약)

폰마다:

1. `sessions_for(shard)` 로 이 폰이 쓸 수 있는 userid 목록  
2. 그중 **가장 최근** `.tgz` 1개 선택 (`newest_session`)  
3. `restore_session(serial, userid)`  
   - 앱 force-stop  
   - `.tgz` → `/sdcard` push  
   - `/data/data/com.shopee.br/` 지정 폴더 삭제 후 tar 풀기, `chown` / SELinux  
   - Shopee **홈** 기동  
4. 기기에서 userid 읽어 **일치 여부** 로그: `세션 복원 결과 userid=…`

성공 시 userid가 **요청한 값과 같음**. 다르거나 `없음`이면 복원 실패로 간주.

### 3.4 복원 후 수집까지 (일반 순서)

```text
(돌고 있었다면) --sessions-stop
    → --prime
    → 허브 ①②③ 또는 목록 수집 launch
```

로그: `runtime/vmos_cloud/list_{shard}.log` 에 `세션 복원 시작` / `세션 복원 결과`.

---

## 4. 잠든 세션(`sleeping/`) 다시 쓰기

소진·차단 시 `mark_depleted` → `park()` 로 `.tgz`가 **`sessions_quarantine/sleeping/`** 으로 이동한다.

| 상황 | 조치 |
|------|------|
| **`wake_at` 이후** | `wake_due()`(inventory·감독 루프 등에서 호출)가 자동으로 `sessions/` 로 되돌림 → **`--prime`** |
| **`wake_at` 이전** | 의도된 10h 휴식. `sleeping/`에만 있으면 **복원·수집 allow 목록에 안 잡힘** |
| 수동 확인 | `sleeping/{userid}.sleep.json` 의 `wake_at` UTC 시각 확인 |

운영 정책상 **10h 전에 sleeping에서 sessions로 옮기지 않는 것**을 권장한다. (프로덕션 sleeping 세션 일괄 wake 금지 규칙과 동일 개념.)

---

## 5. 수집 중 자동 복원·교체

수집(`vmos_app_capture`, **기본방식**) 중에는:

- **`--prime` 없이** 마지막 앱 상태로 PDP를 열 수 있음 → 실패하기 쉬움. 수집 시작 전 **`--prime` 권장**.
- **300건 저장마다** 또는 **막힘** 시 `switch_saved_session` → 다음 `.tgz`로 `restore_session`.
- **Login Necessário** 등 **로그인 필요** 화면 → 캡cha가 아님 → **즉시 다음 보관 세션**, 현재 userid **소진·잠재움**.

자세한 화면별 분기: `DEFAULT_HARVEST_PRODUCTION_KO.md`.

---

## 6. 복원했는데 로그인 창이 뜨는 이유

로컬에 **과거 앱 데이터**를 넣은 것일 뿐, Shopee **서버**가 그 조합을 로그인으로 인정하지 않으면 **Login Necessário / Entrar / não está logado** 등이 뜬다. PC는 **Entrar를 자동 입력하지 않는다**(세션 보관 모드에서만 사람이 VMOS 화면에서 로그인).

### 6.1 흔한 원인

| 원인 | 설명 |
|------|------|
| **서버 세션 만료** | 보관 후 시간 경과, 418·traffic·과다 수집으로 서버가 세션 종료 |
| **복원 실패** | 로그 `세션 복원 결과 userid=없음` 또는 userid **불일치** |
| **손상·불완전 tgz** | 파일 너무 작음(운영상 ~10KB 미만 제외), `shared_prefs` 없이 보관됨 |
| **이미 소진된 계정** | `restore_fail` 등으로 **잠재움**된 userid를 다시 올린 경우 |
| **mitm·프록시와 수동 로그인 혼용** | 보관/로그인 전용일 때 mitm CA·`:808x` 프록시가 켜져 UI·인증 꼬임 |
| **앱 버전 차이** | 보관 시점과 VMOS Shopee 앱 버전이 크게 다름 |
| **잘못된 기대** | 다른 shard·다른 userid를 올렸는데 특정 계정이 나올 것으로 기대 |

### 6.2 로그인 vs 캡cha

- **로그인 필요** → 세션 풀림 → **다음 보관 세션** / **10h 잠재움**.
- **퍼즐·Deslize 등 캡cha UI** → (기본방식) 수동 넘김 대기 대상. 로그인 화면과 **혼동하지 말 것**.

### 6.3 확인 순서

1. 콘솔 요약: **보관 세션 / 잠재움 / 세션 남음**  
2. `sessions/` vs `sleeping/` 에 해당 `userid.tgz` 존재·크기  
3. `--prime` 또는 수집 로그의 **`세션 복원 결과 userid=`**  
4. VMOS 화면: **홈 + Eu(로그인됨)** vs **Login Necessário**

---

## 7. 신규 세션이 필요할 때 (복원으로 안 될 때)

복원으로 로그인이 안 되면 **그 `.tgz`는 쓸 수 없는 연료**로 보고:

1. `--sessions-stop` (수집 중이면 수집 중지)  
2. `--sessions-start` (또는 콘솔 **세션 시작**)  
3. VMOS에서 **Eu → 로그인** (비밀번호는 **폰에서만**)  
4. PC 로그 **「보관 완료 userid=…」** 확인  
5. `--sessions-stop` → `--prime` → 수집

---

## 8. 빠른 참조 — 경로·명령

| 항목 | 값 |
|------|-----|
| 세션 폴더 | `runtime/vmos_cloud/sessions/` |
| 잠재움 | `runtime/vmos_cloud/sessions_quarantine/sleeping/` |
| 복원 | `python runtime/vmos_cloud/_run_list.py --prime` |
| 세션 보관 시작/중지 | `--sessions-start` / `--sessions-stop` |
| 구현 | `client 4 api integration/br_emulator/vmos_app_capture.py` — `restore_session`, `snapshot_session` |
| shard 목록 | `runtime/vmos_cloud/_run_list.py` — `sessions_for`, `prime` |

---

## 9. FAQ

**Q. 상품 3000건·7000건을 “복원”하고 싶다.**  
A. 그건 **세션**이 아니라 **`active/` JSON**·허브 **장부**(`m04_ledger.json`) 문제다. 세션 `--prime`과 무관. 장부/UI 집계는 `member_task_watch`·콘솔 파이프라인 문서를 본다.

**Q. `--prime` 후에도 수집이 Login Necessário만 반복한다.**  
A. 해당 userid 풀 소진 가능성 큼. **잠재움·남은 세션 수** 확인, 필요 시 **세션 보관**으로 풀 보충.

**Q. 한 userid만 특정 폰에 올리고 싶다.**  
A. 운영 스크립트 기본값은 shard별 **최신 1개**만 `--prime`한다. 특정 userid 고정은 수집 allow-list(`SHOPEE_SESSION_ALLOW`)·해당 `.tgz`가 그 shard 목록에 포함되어 있어야 하며, 그 외는 **수집 루프의 `switch_saved_session`** 순서에 따른다. (별도 one-off 복원은 동일 `restore_session` 호출이지만, 일반 운영은 `--prime` / 수집 교체로 충분.)

---

*문서 버전: 2026-10-07 — 운영 `기본방식`·3 shard 기준.*
