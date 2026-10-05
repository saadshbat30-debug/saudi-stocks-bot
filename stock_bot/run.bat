@echo off
REM تشغيل بوت الأسهم السعودية بنقرة مزدوجة
chcp 65001 >nul
cd /d "%~dp0"
python bot.py
pause
