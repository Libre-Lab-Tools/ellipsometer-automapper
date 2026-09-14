@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo AutoMapper has not been set up on this computer.
    echo.
    echo Please run "Setup AutoMapper.bat" first.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo AutoMapper closed because of an error.
    echo The error message should be visible above.
    echo.
    pause
)
