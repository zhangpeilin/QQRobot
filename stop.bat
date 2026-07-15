@echo off
echo 正在停止 QQ 媒体归档服务...
taskkill /F /FI "WINDOWTITLE eq QQ*" /FI "IMAGENAME eq python.exe" 2>/dev/null
echo 已停止。
pause
