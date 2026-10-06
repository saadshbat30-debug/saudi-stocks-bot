@echo off
cd /d "%~dp0"
title Download company data from SAHMK
if not exist .venv (
  echo Run start.bat once first, then run this file again.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python fetch_fundamentals.py
echo.
pause
