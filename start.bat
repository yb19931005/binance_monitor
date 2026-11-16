@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found: venv\Scripts\python.exe
    echo Create it first:
    echo   python -m venv venv
    echo   venv\Scripts\pip.exe install -r requirements.txt
    pause
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] .env not found in:
    echo   %CD%
    echo Copy .env.example to .env and fill configs.
    pause
    exit /b 1
)

echo Starting binance_monitor ...
echo Dashboard: http://localhost:8080
echo Press Ctrl+C to stop.
echo.

"venv\Scripts\python.exe" -u "main.py"
set ERR=%ERRORLEVEL%
echo.
echo Exited with code: %ERR%
pause
endlocal
