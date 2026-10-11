# -*- coding: utf-8 -*-
import subprocess

ps = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Where-Object { $_.CommandLine -match 'status_server.py' } | "
    "Select-Object -ExpandProperty ProcessId"
)
out = subprocess.check_output(
    ["powershell", "-NoProfile", "-Command", ps],
    text=True,
    errors="replace",
)
killed = []
for line in out.splitlines():
    pid = line.strip()
    if pid.isdigit():
        subprocess.call(["taskkill", "/F", "/PID", pid])
        killed.append(pid)
print("killed:", killed or "none")
