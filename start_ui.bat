@echo off
title QQ Archive Control Panel
cd /d "%~dp0"

echo ========================================
echo   QQ Archive Control Panel Launcher
echo ========================================
echo.

netstat -ano | findstr ":8090" | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL%==0 (
    echo [INFO] UI server already running, opening browser...
    start http://127.0.0.1:8090/
    exit /b 0
)

echo [INFO] Starting UI server (background)...
start "" /b "venv\Scripts\pythonw.exe" ui_server.py

timeout /t 2 /nobreak >nul

echo [INFO] Opening browser...
start http://127.0.0.1:8090/

echo.
echo [OK] Control panel: http://127.0.0.1:8090/
echo      Use stop_ui.bat to stop the UI server.
echo.
pause
