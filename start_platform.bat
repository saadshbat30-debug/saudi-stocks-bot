@echo off
REM Stocks Platform launcher - everything is automatic:
REM   1. if the platform is already running: just open the browser
REM   2. otherwise: update from GitHub, install libraries, add to Windows startup,
REM      start the server minimized and open the browser
cd /d "%~dp0"

REM The Saudi platform (Abu Talal / tasi-intel) starts automatically from app.py in the same window.

REM Already running on port 5000? Just open the browser.
netstat -ano | findstr /r /c:":5000 .*LISTENING" >nul
if not errorlevel 1 goto open

REM After updating, restart this script from the new version (the file may have changed).
if "%~1"=="updated" goto run
echo Updating the platform...
set GIT_ASK_YESNO=false
git -c gc.auto=0 pull --ff-only -q
"%~f0" updated

:run
echo Checking libraries...
py -m pip install -q -r requirements.txt --disable-pip-version-check

REM Add to Windows startup + desktop shortcut the first time
if not exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Stocks Platform.lnk" call "%~dp0install_startup.bat" silent

echo Starting the platform...
REM UTF-8 so Arabic messages never crash Python in a cmd window
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
start "Stocks Platform - close this window to stop" /min cmd /k "py app.py"
REM Wait until the server answers (up to 60 seconds)
powershell -NoProfile -Command "for ($i=0; $i -lt 60; $i++) { try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5000/ -TimeoutSec 2 | Out-Null; exit 0 } catch { Start-Sleep 1 } }; exit 1"
if errorlevel 1 (
    echo.
    echo The platform did not start. Open the "Stocks Platform" window in the taskbar
    echo to see the error, and send it to Claude.
    echo.
    pause
    exit /b 1
)

:open
start "" http://127.0.0.1:5000
exit /b 0

