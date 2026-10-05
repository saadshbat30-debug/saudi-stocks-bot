@echo off
REM تشغيل بوت الأسهم السعودية بنقرة مزدوجة
chcp 65001 >nul
cd /d "%~dp0"

REM استخدام py إن وجد (الأضمن على Windows) وإلا python
set PY=python
where py >nul 2>nul && set PY=py

echo Installing required libraries (first time may take a few minutes)...
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo.
  echo [ERROR] Library installation failed - check your internet connection
  pause
  exit /b 1
)

%PY% bot.py
pause
