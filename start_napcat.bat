@echo off
chcp 65001 >/dev/null
title NapCatQQ - QQ 媒体归档服务
color 0A

echo ========================================
echo   NapCatQQ 协议层启动器
echo ========================================
echo.

:: 检查管理员权限
net session >/dev/null 2>&1
if %ERRORLEVEL% neq 0 (
    echo [!] 需要管理员权限，正在请求提权...
    powershell -Command "Start-Process '%~f0' -Verb runAs"
    exit /b
)

:: 设置 NapCat 环境变量
set "NAPCAT_DIR=%~dp0NapCat"
set NAPCAT_PATCH_PACKAGE=%NAPCAT_DIR%\qqnt.json
set NAPCAT_LOAD_PATH=%NAPCAT_DIR%\loadNapCat.js
set NAPCAT_INJECT_PATH=%NAPCAT_DIR%\NapCatWinBootHook.dll
set NAPCAT_LAUNCHER_PATH=%NAPCAT_DIR%\NapCatWinBootMain.exe
set NAPCAT_MAIN_PATH=%NAPCAT_DIR%\napcat.mjs

:: QQ 路径
set "QQPath=C:\Program Files\Tencent\QQNT\QQ.exe"

if not exist "%QQPath%" (
    echo [ERROR] 找不到 QQ.exe: %QQPath%
    pause
    exit /b 1
)

:: 生成加载脚本
set "NAPCAT_MAIN_UNIX=%NAPCAT_MAIN_PATH:\=/%"
echo (async () =^> {await import("file:///%NAPCAT_MAIN_UNIX%")})() > "%NAPCAT_LOAD_PATH%"

echo [INFO] QQ 路径: %QQPath%
echo [INFO] NapCat 路径: %NAPCAT_DIR%
echo.
echo [INFO] 正在启动 NapCatQQ...
echo [INFO] 启动后请在 QQ 窗口扫码登录小号
echo [INFO] 登录成功后 WebUI 地址和密钥会显示在下方
echo.
echo ----------------------------------------
echo.

:: 启动 NapCat（如果传入了 QQ 号则自动登录）
if "%~1" neq "" (
    "%NAPCAT_LAUNCHER_PATH%" "%QQPath%" "%NAPCAT_INJECT_PATH%" %1
) else (
    "%NAPCAT_LAUNCHER_PATH%" "%QQPath%" "%NAPCAT_INJECT_PATH%"
)

pause
