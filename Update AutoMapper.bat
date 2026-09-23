@echo off
cd /d "%~dp0"

echo Updating Ellipsometer AutoMapper...
echo.
git pull origin main

echo.
if %ERRORLEVEL% EQU 0 (
    echo Update complete.
) else (
    echo Update failed. Review the message above.
)
pause
