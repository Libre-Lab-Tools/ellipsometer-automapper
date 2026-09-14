@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo AutoMapper has not been set up on this computer.
    echo.
    echo Please run "Setup AutoMapper.bat" first.
    echo.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" main.py
exit
