# Anonymous full raw `get_pc` — deep research (2026-07-26)

## Goal

Obtain **entire raw** `/api/v4/pdp/get_pc` payloads (login-quality JSON) **without** providing buyer login cookies (`SPC_U` / `SPC_EC` / `SPC_ST`).

## Public consensus (2025–2026)

| Source | Finding |
|--------|---------|
| browse.sh MY skill (2026-05-20) | All unauth value APIs → `90309999`; only login cookie context or Open Platform partner |
| Kameleo toolkit (2025) | Must be logged in; no single-request workaround reproduced |
| StackOverflow #78054805 | Needs dynamic `Af-Ac-Enc-*` / `X-Sap-*` + session; plain requests dead |
| tail-fin-shopee 0.8 | Even `is_login:true` cookies fail without page-native signatures; Mode A = attach real Chrome |
| Bluetick / RE blogs | Theoretical path = Frida/APK interception for app-sign microservice |

Partner Open API SDKs are **seller** APIs — not buyer `get_pc`.

## Local empirical probes (this repo)

### A) Login cookie + headed SPA intercept — WORKS

`_br_human_raw_100.py` with BR TEST cookie:

- **116** files in `br_human_raw_getpc/`
- **~104** full raw `browser_get_pc` (≈56 data / 103 item keys)
- Captcha/traffic: 0 during successful run

### B) Anonymous browser (strip `SPC_U`/`SPC_EC`/`SPC_ST`) — FAILS

`_br_anon_raw_probe.py` (2026-07-26 live):

- No `AC_CERT_D` reminted in 60s
- PDP → `get_pc error=90309999`
- Redirected to `/verify/traffic/error?...&is_logged_in=false&type=4`
- Result: `br_anon_raw_probe_result.json`

### C) Unidbg / app-sign HTTP — FAILS (current stack)

`br_unidbg_live_out.txt` / `_br_unidbg_probe_out.txt`:

- Signer produces `x-sap-ri` etc. but wrong region APK (`com.shopee.id`) / incomplete protocol
- BR/ID get_pc all `403/418` + `90309999`
- Not a production anonymous unlock today

### D) TW loginless SSR+BigGo — NOT raw get_pc

`tw_deliverable_getpc/`: structure rebuild, `live_get_pc_api=false`. Useful without cookies, **not** cookie-quality raw API.

## What “anonymous” would require (engineering)

Only plausible path to **raw** get_pc without buyer login:

1. **BR/TW APK** reverse + Frida/unidbg **region-correct** signer microservice  
2. Device fingerprint / soft-token binding that SGW accepts as non-login app client  
3. Residential sticky IP + rate limits  

This is **not** implemented/working in-repo. Public OSS has no reliable generator.

## Verdict

| Need | Anonymous today? |
|------|------------------|
| Full raw `get_pc` JSON | **No** — login session (+ browser SPA sign) required |
| Full schema approx + prices | **Partial** — TW SSR+BigGo only |
| Scale anonymous raw | **Blocked** until app-sign RE completes |

## Practical recommendation

1. **For entire raw data now:** keep using login cookies via `_br_human_raw_100.py` (proven).  
2. **For anonymous:** treat as R&D track (correct BR APK + Frida signer); do not promise delivery.  
3. Do not burn time on guest curl / TLS impersonation / `af-ac-enc-dat=null` — empirically dead.
