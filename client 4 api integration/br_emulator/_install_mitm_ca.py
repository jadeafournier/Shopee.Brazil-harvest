# -*- coding: utf-8 -*-
"""Install mitmproxy CA into Android system trust store (rooted device)."""
from __future__ import annotations

import hashlib
import struct
import subprocess
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

SERIAL = sys.argv[1] if len(sys.argv) > 1 else "emulator-5564"
PEM_CANDS = [
    Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem",
    Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.cer",
]


def adb(*args: str) -> str:
    r = subprocess.run(["adb", "-s", SERIAL, *args], capture_output=True, text=True)
    print(r.stdout or "", end="")
    print(r.stderr or "", end="", file=sys.stderr)
    return (r.stdout or "") + (r.stderr or "")


def main() -> int:
    pem_path = next((p for p in PEM_CANDS if p.is_file()), None)
    if not pem_path:
        print("no mitm CA found")
        return 1
    raw = pem_path.read_bytes()
    if b"BEGIN CERTIFICATE" in raw:
        cert = x509.load_pem_x509_certificate(raw, default_backend())
    else:
        cert = x509.load_der_x509_certificate(raw, default_backend())

    subject_der = cert.subject.public_bytes()
    h = hashlib.md5(subject_der).digest()
    name = f"{struct.unpack('<I', h[:4])[0]:08x}.0"
    local = Path(__file__).resolve().parent / name
    local.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"local cert {local} hash={name}")

    adb("push", str(local), f"/sdcard/{name}")
    # remount + install
    for cmd in (
        f"su 0 mount -o rw,remount /system",
        f"su 0 cp /sdcard/{name} /system/etc/security/cacerts/{name}",
        f"su 0 chmod 644 /system/etc/security/cacerts/{name}",
        f"su 0 chown root:root /system/etc/security/cacerts/{name}",
        f"su 0 ls -l /system/etc/security/cacerts/{name}",
    ):
        print(">>", cmd)
        adb("shell", cmd)
    print("DONE — reboot device recommended")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
