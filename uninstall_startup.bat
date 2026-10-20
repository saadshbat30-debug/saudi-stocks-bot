@echo off
REM Removes the platform from Windows startup (the desktop shortcut stays).
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Startup')) 'Stocks Platform.lnk')"
echo.
echo Removed from Windows startup.
echo.
pause
