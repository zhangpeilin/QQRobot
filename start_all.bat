@echo off
chcp 65001 >/dev/null
title QQ 媒体归档系统 - 全部启动

echo ========================================
echo   QQ 群媒体自动归档系统 - 一键启动
echo ========================================
echo.

:: 检查管理员权限（NapCat 需要）
net session >/dev/null 2>&1
if %ERRORLEVEL% neq 0 (
    echo [!] 需要管理员权限（NapCat 注入需要），正在请求提权...
    powershell -Command "Start-Process '%~f0' -Verb runAs"
    exit /b
)

:: 第一步：启动 NapCat
echo [1/2] 正在启动 NapCatQQ 协议层...
start "NapCatQQ" cmd /c "cd /d "%~dp0" && start_napcat.bat %*"

:: 等待 NapCat 就绪
echo [INFO] 等待 NapCat 启动...
echo [INFO] 请在弹出的 QQ 窗口中扫码登录小号
echo.

:: 循环检测 WebSocket 端口
set "READY=0"
for /l %%i in (1,1,60) do (
    if "!READY!"=="0" (
        powershell -Command "try { $ws = New-Object System.Net.Sockets.TcpClient; $ws.Connect('127.0.0.1', 3001); $ws.Close(); exit 0 } catch { exit 1 }" >/dev/null 2>&1
        if !ERRORLEVEL! equ 0 (
            set "READY=1"
            echo [OK] NapCat WebSocket 端口 3001 已就绪
        ) else (
            timeout /t 2 /nobreak >/dev/null
        )
    )
)

if "!READY!"=="0" (
    echo [WARN] 等待超时，仍尝试启动 Bot...
)

echo.
echo [2/2] 正在启动归档 Bot...
echo ----------------------------------------
echo.

:: 第二步：启动 Bot
call "%~dp0venv\Scripts\activate.bat"
cd /d "%~dp0"
python bot.py

pause
