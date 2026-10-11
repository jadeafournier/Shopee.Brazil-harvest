# -*- coding: utf-8 -*-
import subprocess
import urllib.request
from pathlib import Path

paths = [
    Path(r"D:\Luxury\Great Scraper2\Great Scraper\runtime\vmos_cloud\status_server.py"),
    Path(r"C:\Users\Administrator\Music\Great Scraper\client 4 api integration\..\runtime\vmos_cloud\status_server.py"),
    Path(r"C:\Users\Administrator\Music\Great Scraper\runtime\vmos_cloud\status_server.py"),
]
for p in paths:
    if p.is_file():
        t = p.read_text(encoding="utf-8", errors="replace")
        print("FILE", p, "pool-v2", "list-pool-v2" in t, "bytes", len(t))

out = subprocess.check_output(
    [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "ForEach-Object { $_.ProcessId.ToString() + '|' + ($_.CommandLine -replace '\\s+',' ') }",
    ],
    text=True,
    errors="replace",
)
for line in out.splitlines():
    low = line.lower()
    if "status_server" in low or ":8766" in low or " 8766" in low:
        print("PROC", line[:400])

for port in list(range(8755, 8775)) + [8080, 8888]:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"Cache-Control": "no-cache"})
        html = urllib.request.urlopen(req, timeout=1.5).read().decode("utf-8", "replace")
    except Exception:
        continue
    if "서버 목록 잔량" not in html and "Collection" not in html:
        continue
    i_ca = html.find("collect-actions")
    i_wl = html.find("wl-picks")
    order = i_wl >= 0 and i_ca > i_wl
    print(
        f"HTTP {port} len={len(html)} pool-v2={'list-pool-v2' in html} "
        f"미할당={'미할당' in html} order_ok={order}"
    )
