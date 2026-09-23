@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo   Ellipsometer AutoMapper - Setup
echo ==========================================
echo.

if exist ".venv\Scripts\python.exe" (
    echo Existing local Python environment found.
    goto INSTALL_REQUIREMENTS
)

echo Creating local Python environment in .venv...
echo.

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -3 -m venv .venv
    goto CHECK_VENV
)

where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    python -m venv .venv
    goto CHECK_VENV
)

echo ERROR: Python was not found on this computer.
echo.
echo Install 64-bit Python 3.12 or newer, then run this file again.
echo.
pause
exit /b 1

:CHECK_VENV
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: The Python virtual environment could not be created.
    pause
    exit /b 1
)

:INSTALL_REQUIREMENTS
echo.
echo Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if %ERRORLEVEL% NEQ 0 goto ERROR_INSTALL

echo.
echo Installing AutoMapper requirements...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 goto ERROR_INSTALL

echo.
echo ==========================================
echo   Setup completed successfully.
echo ==========================================
echo.
pause
exit /b 0

:ERROR_INSTALL
echo.
echo ERROR: AutoMapper setup did not complete successfully.
pause
exit /b 1
