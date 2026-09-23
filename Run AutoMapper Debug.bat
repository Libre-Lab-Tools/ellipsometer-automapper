@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo AutoMapper has not been set up on this computer.
    echo.
    echo Please run "Setup AutoMapper.bat" first.
    echo.
    pause
    exit /b 1
)

echo Starting Ellipsometer AutoMapper in DEBUG mode...
echo.
".venv\Scripts\python.exe" main.py

echo.
echo AutoMapper has closed.
echo If an error occurred, review the messages above.
echo.
pause
