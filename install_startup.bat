@echo off
REM Adds the platform to Windows startup and puts a shortcut on the desktop.
set "TARGET=%~dp0start_platform.bat"
set "WORKDIR=%~dp0"

powershell -NoProfile -Command ^
  "$w = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @([Environment]::GetFolderPath('Startup'), [Environment]::GetFolderPath('Desktop'))) {" ^
  "  $s = $w.CreateShortcut((Join-Path $dir 'Stocks Platform.lnk'));" ^
  "  $s.TargetPath = '%TARGET%'; $s.WorkingDirectory = '%WORKDIR%'; $s.WindowStyle = 7;" ^
  "  $s.IconLocation = 'shell32.dll,13'; $s.Save() }"

echo.
echo Done! The platform will open automatically when Windows starts.
echo A "Stocks Platform" shortcut was also added to your desktop.
echo.
pause
