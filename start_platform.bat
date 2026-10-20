@echo off
REM Stocks Platform: starts the server (if not already running) and opens the browser.
cd /d "%~dp0"

REM Is the platform already running on port 5000?
netstat -ano | findstr /r /c:":5000 .*LISTENING" >nul
if errorlevel 1 (
    start "Stocks Platform - close this window to stop" /min cmd /k "py app.py"
    REM Wait until the server answers (up to 30 seconds)
    powershell -NoProfile -Command "for ($i=0; $i -lt 30; $i++) { try { Invoke-WebRequest -UseBasicParsing http://localhost:5000/ -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 1 } }"
)

start "" http://localhost:5000
