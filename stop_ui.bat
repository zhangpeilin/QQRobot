@echo off
title Stop UI Server
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8899" ^| findstr "LISTENING"') do (
    echo Stopping UI server PID %%a ...
    taskkill /F /PID %%a >nul 2>&1
)
echo UI server stopped.
pause
