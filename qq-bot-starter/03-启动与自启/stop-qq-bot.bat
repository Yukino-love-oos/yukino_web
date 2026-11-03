@echo off
chcp 65001 >nul
title QQ Bot Stop

echo 这个脚本会：关闭 NapCat 启动窗口和它拉起的 QQ 进程。
echo 注意：如果这台电脑上还开着你自己日常用的 QQ，也会一起被关掉！
echo.
set /p OK=继续吗？输入 y 回车：
if /I not "%OK%"=="y" (
  echo 已取消。
  pause
  exit /b 0
)

echo [1/2] 关闭 NapCat 窗口...
taskkill /FI "WINDOWTITLE eq NapCat*" /T /F >nul 2>&1

echo [2/2] 结束 QQ 进程...
taskkill /IM QQ.exe /T /F >nul 2>&1

echo.
echo 已停止。DSH 不会自动关闭，需要的话手动关 DSH 窗口，或执行：
echo   taskkill /IM "DeepSeek Harness.exe" /F
echo.
pause
