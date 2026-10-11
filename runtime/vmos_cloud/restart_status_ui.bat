@echo off
set PORT=8766
set SCRIPT=%~dp0status_server.py
C:\Python314\python.exe "%~dp0_kill_status_ui.py"
ping 127.0.0.1 -n 3 >nul
start "status UI %PORT%" C:\Python314\python.exe -u "%SCRIPT%" %PORT%
ping 127.0.0.1 -n 3 >nul
C:\Python314\python.exe -c "import urllib.request; h=urllib.request.urlopen('http://127.0.0.1:%PORT%/').read().decode('utf-8','replace'); print('collect-session-stop', 'collect-session-stop' in h); print('ui-build', 'ui-build' in h)"
echo Open http://127.0.0.1:%PORT%/  — Ctrl+F5
