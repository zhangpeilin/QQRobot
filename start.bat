@echo off
chcp 65001 >/dev/null
echo ========================================
echo   QQ 群媒体自动归档系统
echo ========================================
echo.

:: 激活虚拟环境
call "%~dp0venv\Scripts\activate.bat"

:: 启动 Bot
echo [INFO] 正在启动...
python bot.py

:: 保持窗口
pause
