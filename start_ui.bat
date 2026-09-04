@echo off
title QQ Archive Control Panel
cd /d "%~dp0"

echo ========================================
echo   QQ Archive Control Panel Launcher
echo ========================================
echo.

netstat -ano | findstr ":8899" | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL%==0 (
    echo [INFO] UI server already running, opening browser...
    start http://127.0.0.1:8899/
    exit /b 0
)

echo [INFO] Starting UI server (background)...
start "" "venv\Scripts\pythonw.exe" ui_server.py

echo [INFO] Waiting for port 8899...
for /l %%i in (1,1,15) do (
    netstat -ano | findstr ":8899" | findstr "LISTENING" >nul 2>&1 && goto :opened
    timeout /t 1 /nobreak >nul
)
echo [WARN] Port 8899 not listening yet, still opening browser...
:opened
echo [INFO] Opening browser...
start http://127.0.0.1:8899/

echo.
echo [OK] Control panel: http://127.0.0.1:8899/
echo      Use stop_ui.bat to stop the UI server.
echo.
pause
