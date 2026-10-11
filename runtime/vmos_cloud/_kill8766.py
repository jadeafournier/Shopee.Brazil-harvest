import subprocess

out = subprocess.check_output(
    [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -match 'status_server.py' -and $_.CommandLine -match '8766' } | "
        "Select-Object -ExpandProperty ProcessId",
    ],
    text=True,
    errors="replace",
)
for line in out.splitlines():
    pid = line.strip()
    if pid.isdigit():
        subprocess.call(["taskkill", "/PID", pid, "/F"])
