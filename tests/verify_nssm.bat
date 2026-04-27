@echo off
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify_nssm.ps1"
echo.
echo Script finished. Press any key to close.
pause >nul
