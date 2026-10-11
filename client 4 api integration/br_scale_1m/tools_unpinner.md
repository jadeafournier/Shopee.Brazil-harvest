# Emulator SSL unpin tools (no physical device)

## Vendored
- `br_emulator/vendor/android-certificate-unpinning.js` — [HTTP Toolkit](https://github.com/httptoolkit/frida-interception-and-unpinning)
- `br_emulator/vendor/android-certificate-unpinning-fallback.js`

## Recommended flow (rooted emu OR gadget)
```powershell
# Terminal A: mitm
..\.venv\Scripts\mitmdump.exe -s br_emulator\mitm_capture.py --listen-port 8080

# Terminal B: Frida unpin + signer observe (package must be BR when available)
frida -U -f com.shopee.id `
  -l br_emulator\vendor\android-certificate-unpinning.js `
  -l br_emulator\vendor\android-certificate-unpinning-fallback.js `
  -l br_emulator\frida_signer.js
```

## Optional: mitmproxy/android-unpinner (no root)
```powershell
pip install android-unpinner
# android-unpinner PATH\to\shopee.br.apk
# then adb install-multiple the unpinned splits
```

## Goal
Capture live `x-sap-ri` / `af-ac-enc-dat` → fill `SIGN_SPEC` in `frida_signer.js` →
`signer_service.py --backend frida` → `worker_http.py` at HTTP scale.
