@echo off
cd /d "%~dp0"

echo Updating Ellipsometer AutoMapper...
echo.

git pull origin main

echo.
echo Update complete.
pause