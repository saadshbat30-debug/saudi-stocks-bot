@echo off
cd /d "%~dp0"

set PY=python
where py >nul 2>nul && set PY=py
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then run start.bat again.
  pause
  exit /b 1
)

if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt

if not exist .env (
  copy .env.example .env >nul
  echo Put your SAHMK_API_KEY in the .env file, save it, then run start.bat again.
  notepad .env
  exit /b 0
)

echo Opening http://localhost:5000  - keep this window open while using the page.
start "" http://localhost:5000
python app.py
pause
