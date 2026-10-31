@echo off
rem Start the reverse SSH tunnel that lets the Aliyun server reach local NapCat.
rem Keep the window open while you want the alt QQ account to stay online.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_napcat_tunnel.ps1"
pause
