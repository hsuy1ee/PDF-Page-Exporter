@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Register-EdgeExtension.ps1"
if errorlevel 1 (
    echo Registration failed. See the error above.
    pause
    exit /b 1
)
pause
