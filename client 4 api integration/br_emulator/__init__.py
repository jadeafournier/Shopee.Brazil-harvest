# -*- coding: utf-8 -*-
"""Shopee BR 에뮬레이터 수집 패키지 (실기기 없이 앱 서명 확보).

구성:
  _save.py         공용 저장(Client 1 봉투) / URL 로딩 / dedup
  mitm_capture.py  mitmproxy 애드온 — 앱이 스스로 서명해 쏘는 get_pc/item_get/search 응답 캡처
  adb_driver.py    URL 목록을 앱 딥링크 인텐트로 순회(사람형 페이싱)
  frida_signer.js  Shopee 앱 서명 함수 후킹(Frida)
  signer_bridge.py Frida ↔ Python 서명 브리지 → ios_client signer 슬롯 주입
  emulator_fleet.py 쿠키풀 + 에뮬 서명 + 프록시풀 결합 대량 수집
  vmos_cloud.py    VMOS Cloud Phone ADB 연결
  vmos_app_capture.py  VMOS 앱 전용 get_pc (웹 없음, mitm+adb reverse)
  run_emulator.py  통합 CLI (check / capture / fleet / status)
"""
