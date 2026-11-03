@echo off
chcp 65001 >nul
setlocal EnableExtensions
title QQ Bot Launcher

REM ==========================================================
REM  改这 4 个变量就行（路径按你实际安装位置）
REM ==========================================================
set "NAPCAT_DIR=D:\NapCat"
set "NAPCAT_LAUNCHER=launcher.bat"
set "BOT_QQ=请填机器人QQ号"
set "DSH_EXE=E:\DSH\DeepSeek Harness.exe"
set "NAPCAT_WEBUI_PORT=6099"

REM ==========================================================
REM  /auto 参数：开机自启用，跑完不暂停
REM ==========================================================
set "AUTO=0"
if /I "%~1"=="/auto" set "AUTO=1"

echo.
echo ==========================================
echo  QQ Bot 一键启动
echo  NapCat: %NAPCAT_DIR%
echo  机器人QQ: %BOT_QQ%
echo ==========================================
echo.

if not exist "%NAPCAT_DIR%\%NAPCAT_LAUNCHER%" (
  echo [错误] 找不到 %NAPCAT_DIR%\%NAPCAT_LAUNCHER%
  echo        请检查 NAPCAT_DIR 变量，或先按 01-安装清单.md 装 NapCat。
  goto :done
)
if not exist "%DSH_EXE%" (
  echo [错误] 找不到 DSH：%DSH_EXE%
  echo        请检查 DSH_EXE 变量。
  goto :done
)

echo [1/3] 启动 NapCat（会自动拉起 QQ）...
pushd "%NAPCAT_DIR%"
start "NapCat" "%NAPCAT_DIR%\%NAPCAT_LAUNCHER%" %BOT_QQ%
popd

echo [2/3] 等待 NapCat WebUI 端口 %NAPCAT_WEBUI_PORT% 就绪...
set /a TRY=0
:waitnapcat
set /a TRY+=1
powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try{$c.Connect('127.0.0.1',%NAPCAT_WEBUI_PORT%); exit 0}catch{exit 1}finally{$c.Close()}" >nul 2>&1
if not errorlevel 1 goto :napcat_ok
if %TRY% GEQ 60 (
  echo       [警告] 等了 120 秒还没就绪。
  echo       常见原因：首次登录要扫码 / QQ 弹了更新 / 版本不匹配。
  echo       请打开 NapCat 窗口看提示，或访问 http://127.0.0.1:%NAPCAT_WEBUI_PORT%/webui 扫码。
  goto :start_dsh
)
timeout /t 2 /nobreak >nul
goto :waitnapcat
:napcat_ok
echo       NapCat 端口已就绪。

:start_dsh
echo [3/3] 启动 DSH...
start "" "%DSH_EXE%"

echo.
echo 完成。
echo   - NapCat WebUI: http://127.0.0.1:%NAPCAT_WEBUI_PORT%/webui  （掉登录时在这里扫码）
echo   - 第一次启动大肥鱼插件后，去插件配置页点「重新连接」确认 QQ 在线。
echo   - DSH 端口每次启动会变，看 DSH 窗口地址栏。

:done
if "%AUTO%"=="1" (
  timeout /t 3 /nobreak >nul
) else (
  echo.
  pause
)
endlocal
