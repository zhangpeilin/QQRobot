@echo off
chcp 65001 >nul
title NapCatQQ - Desktop Mode
color 0A

echo ========================================
echo   NapCatQQ Desktop Mode Launcher
echo ========================================
echo.

:: Check admin privileges
net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [WARN] Need administrator privileges, elevating...
    powershell -Command "Start-Process '%~f0' -Verb runAs"
    exit /b
)

:: NapCat environment variables
set "NAPCAT_DIR=%~dp0NapCat"
set "NAPCAT_PATCH_PACKAGE=%NAPCAT_DIR%\qqnt.json"
set "NAPCAT_LOAD_PATH=%NAPCAT_DIR%\loadNapCat.js"
set "NAPCAT_INJECT_PATH=%NAPCAT_DIR%\NapCatWinBootHook.dll"
set "NAPCAT_LAUNCHER_PATH=%NAPCAT_DIR%\NapCatWinBootMain.exe"
set "NAPCAT_MAIN_PATH=%NAPCAT_DIR%\napcat.mjs"

:: QQ path
set "QQPath=C:\Program Files\Tencent\QQNT\QQ.exe"

if not exist "%QQPath%" (
    echo [ERROR] QQ.exe not found: %QQPath%
    pause
    exit /b 1
)

:: Generate loader script
set "NAPCAT_MAIN_UNIX=%NAPCAT_MAIN_PATH:\=/%"
echo (async () =^> {await import("file:///%NAPCAT_MAIN_UNIX%")})() > "%NAPCAT_LOAD_PATH%"

echo [INFO] QQ path: %QQPath%
echo [INFO] NapCat path: %NAPCAT_DIR%
echo.
echo [INFO] Starting NapCatQQ desktop mode...
echo [INFO] Please scan QR code with your QQ account in the QQ window.
echo.
echo ----------------------------------------
echo.

:: Launch NapCat (with optional auto-login QQ number)
if "%~1" neq "" (
    "%NAPCAT_LAUNCHER_PATH%" "%QQPath%" "%NAPCAT_INJECT_PATH%" %1
) else (
    "%NAPCAT_LAUNCHER_PATH%" "%QQPath%" "%NAPCAT_INJECT_PATH%"
)

pause
