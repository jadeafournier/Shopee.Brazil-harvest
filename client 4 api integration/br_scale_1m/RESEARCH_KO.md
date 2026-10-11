# 일 100만 건 · 전체 get_pc · 실기기/로그인 쿠키 없이 — 연구 결론

최종 갱신: 2026-07-23  
대상: 브라질 Shopee (`shopee.com.br`) Client 4 전체 `get_pc` 봉투

---

## 1. 요구사항 vs 현실

| 요구 | 현실 (2025–2026 오픈소스·업계 검증) |
|------|--------------------------------------|
| 전체 JSON (`data`≈56키, `item`≈103키) | 브라우저 SPA 인터셉트 또는 **앱 서명 HTTP**만 가능 |
| 실기기 제공 불가 | **에뮬레이터 플릿**으로 대체 가능 (이미 ADB 존재) |
| 로그인 쿠키 제공 불가 | 웹 로그인은 필수 아님. **게스트 모바일/웹 + 서명**이 핵심 |
| 일 ≥ 1,000,000 | 브라우저만으로는 **불가능에 가깝다**. **서명된 HTTP 플릿**만 가능 |

### 업계·오픈소스 합의 (근거)

1. **[tail-fin-shopee](https://docs.rs/crate/tail-fin-shopee/latest) (2026-04)**  
   Chrome TLS 위장(`wreq`), stealth Chromium headed/headless 전부  
   `{error: 90309999}` — **쿠키는 통과, 서명 부재로 차단**.  
   결론: “Mode A = 실제 Chrome 탭 attach” 또는 서명 확보.

2. **[Kameleo Shopee Toolkit 2025](https://kameleo.io/blog/shopee-scraper-toolkit)**  
   모바일 비공식 API도 인증/서명 없으면 실패. “단일 요청 우회” 재현 불가.

3. **[Bluetick](https://www.bluetickconsultants.com/how-to-scrape-shopee-at-scale-advanced-anti-bot-bypass-guide/)**  
   스케일 = CDP 인터셉트 **또는** 네이티브 앱 API 인터셉트(리버스).

4. **중국/동남아 리버스 자료 (CSDN, yxfzedu, xsblog)**  
   핵심은 `libshpssdk.so` / `com.shopee.shpssdk` → `x-sap-ri`, `x-sap-sec`, `af-ac-enc-dat`.  
   **Unidbg / Frida**로 서명 함수를 PC·에뮬에서 호출하는 것이 표준 해법.

5. **TLS 위장만으로는 부족**  
   `curl_cffi`, `wreq`/`rnet`은 JA3/JA4를 맞추지만 Shopee는 **앱/웹 서명 레이어**가 별도.

---

## 2. 일 100만 건 산수

```
1,000,000 / 86,400 ≈ 11.6 req/s 지속
여유·재시도 포함 ≈ 20–30 signed HTTP/s 목표
```

| 방식 | 대략 처리량 | 1M/일 필요 규모 |
|------|-------------|-----------------|
| Patchright/Camoufox 브라우저 PDP | 0.02–0.05/s/브라우저 | **수백~수천 브라우저** (비현실·고비용) |
| 에뮬 mitm + adb 딥링크 | 0.1–0.2/s/에뮬 | **수십~수백 에뮬** |
| **앱 서명 HTTP 플릿** (서명 서비스 + curl_cffi) | 50–500/s/노드 | **수 대 워커 + 서명기 풀** ✅ |

→ **유일한 현실적 1M/일 경로 = 서명 마이크로서비스 + HTTP 워커 수평 확장.**

---

## 3. 도입할 오픈소스 스택

| 역할 | 오픈소스 | 본 저장소 위치 |
|------|----------|----------------|
| TLS/JA3 HTTP | [curl_cffi](https://github.com/lexiforest/curl_cffi) | `br_scale_1m/worker_http.py` |
| SSL unpin (에뮬 MITM) | [httptoolkit/frida-interception-and-unpinning](https://github.com/httptoolkit/frida-interception-and-unpinning) | `br_emulator/vendor/` |
| APK unpin (루트 불필요 옵션) | [mitmproxy/android-unpinner](https://github.com/mitmproxy/android-unpinner) | `br_scale_1m/tools_unpinner.md` |
| 서명 관찰/후킹 | Frida + 강화된 `frida_signer.js` (SHPSSDK) | `br_emulator/frida_signer.js` |
| Unidbg 서명기 | 기존 `D:\emu\shopee_signer` | `br_scale_1m/signer_service.py` 연동 |
| 안티디텍트 브라우저 (보조) | [Camoufox](https://github.com/daijro/camoufox), Patchright(기존) | `br_scale_1m/probe_stealth.py` |
| 큐/스케일 | Redis 프론티어 패턴 (scrapy-redis 계열) | `br_scale_1m/queue_redis.py` |

---

## 4. 목표 아키텍처 (도입됨)

```
 URL 큐 (Redis) 
      │
      ▼
 ┌────────────┐     RPC/HTTP      ┌─────────────────────────┐
 │ HTTP Worker│ ───────────────► │ Signer Service            │
 │ curl_cffi  │   sign(url,body) │  Frida(에뮬 앱) 또는 Unidbg │
 │ × N 노드   │ ◄─────────────── │  libshpssdk.so              │
 └─────┬──────┘                   └─────────────────────────┘
       │ signed GET get_pc
       ▼
 br_deliverable_getpc/output_{shop}_{item}.json
```

**게스트/무로그인:** 워커는 `SPC_U` 없이 동작. 서명이 신뢰의 본체.  
**실기기 불필요:** 서명기는 **에뮬 플릿**(이미 `adb` 존재) 또는 Unidbg.

---

## 5. 차단 조건 (정직하게)

지금 당장 1M/일을 “스위치 켜듯” 달성하려면 아래가 **반드시** 완성되어야 한다.

1. 에뮬에 **Shopee BR(`com.shopee.br`)** 또는 동일 SDK의 APK + (게스트로 PDP 열림)  
2. Frida SSL-unpin + mitm **또는** Unidbg에서 `libshpssdk` 서명 **호출 성공**  
3. `SIGN_SPEC` / Unidbg API가 `error=0` get_pc를 반복 재현  
4. Redis + 워커 N대 + 주거형 프록시 대역

웹 지문·CapSolver·쿠키만으로는 **업계 검증상 1M full get_pc 불가**.

---

## 6. 실행 순서 (도입 코드)

```powershell
cd "D:\Great Scraper\Great Scraper\client 4 api integration"

# 1) TLS만으로 되는지 과학 검증 (기대: 90309999)
..\.venv\Scripts\python.exe -u br_scale_1m\probe_curl_cffi.py

# 2) 서명 서비스 (에뮬 Frida 또는 Unidbg 백엔드)
..\.venv\Scripts\python.exe -u br_scale_1m\signer_service.py --backend stub

# 3) Redis 있으면 큐 적재 + 워커 (서명 준비 후)
# redis-server 후:
..\.venv\Scripts\python.exe -u br_scale_1m\queue_redis.py seed --limit 10000
..\.venv\Scripts\python.exe -u br_scale_1m\worker_http.py --workers 8
```

에뮬 경로(서명 확보용):

```powershell
# CA unpin 스크립트는 br_emulator/vendor 에 vendoring 됨
# frida -U -f com.shopee.br -l br_emulator/vendor/android-certificate-unpinning.js -l br_emulator/frida_signer.js
```
