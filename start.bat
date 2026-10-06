@echo off
cd /d "%~dp0"
title Saudi Stocks Radar

set PY=python
where py >nul 2>nul && set PY=py
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then run start.bat again.
  pause
  exit /b 1
)

if not exist .venv (
  echo [1/3] Creating environment...
  %PY% -m venv .venv
)
call .venv\Scripts\activate.bat

echo [2/3] Installing libraries - the first time takes a few minutes, please wait...
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installing libraries failed. Take a screenshot of this window and send it.
  pause
  exit /b 1
)

if not exist .env (
  copy .env.example .env >nul
  echo.
  echo Put your SAHMK_API_KEY in the .env file, save it, then run start.bat again.
  notepad .env
  exit /b 0
)

echo [3/3] Starting... the page opens by itself. Keep this window open.
set OPEN_BROWSER=1
python app.py
echo.
echo The app stopped. Take a screenshot of this window and send it.
pause
