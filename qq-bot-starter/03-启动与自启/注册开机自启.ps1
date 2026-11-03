# 注册（或移除）QQ 机器人开机自启
# 用法：
#   powershell -ExecutionPolicy Bypass -File "D:\Yukino\qq-bot-starter\03-启动与自启\注册开机自启.ps1"
#   powershell -ExecutionPolicy Bypass -File "...\注册开机自启.ps1" -Remove
#   powershell -ExecutionPolicy Bypass -File "...\注册开机自启.ps1" -Highest   # 需要管理员

[CmdletBinding()]
param(
  [string]$TaskName = 'Yukino-QQ-Bot',
  [string]$Bat,
  [switch]$Highest,
  [switch]$Remove
)

$ErrorActionPreference = 'Stop'

if (-not $Bat) {
  # 默认：本脚本上一级目录里的 start-qq-bot.bat
  $root = Split-Path $PSScriptRoot -Parent
  $Bat  = Join-Path $root '03-启动与自启\start-qq-bot.bat'
}

# 已存在就先删掉，保证幂等
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "已删除旧任务：$TaskName"
}

if ($Remove) {
  Write-Host "已移除开机自启。"
  return
}

if (-not (Test-Path -LiteralPath $Bat)) {
  throw "找不到启动脚本：$Bat`n请先用 -Bat 参数指定正确路径。"
}

$user    = "$env:USERDOMAIN\$env:USERNAME"
$action  = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$Bat`" /auto"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero)

$params = @{
  TaskName    = $TaskName
  Action      = $action
  Trigger     = $trigger
  Settings    = $settings
  Description = '登录时启动 NapCat + DSH，恢复 QQ 机器人'
}

if ($Highest) {
  $params.RunLevel = 'Highest'
  $params.User     = $user
}

Register-ScheduledTask @params | Out-Null

Write-Host ""
Write-Host "已注册计划任务：$TaskName"
Write-Host "  触发：用户 $user 登录时"
Write-Host "  执行：cmd /c `"$Bat`" /auto"
Write-Host ""
Write-Host "验证方法："
Write-Host "  1) 任务计划程序里找到 $TaskName，右键「运行」"
Write-Host "  2) 手动重启电脑，登录后不做任何操作，用手机 QQ 私聊机器人"
Write-Host ""
Write-Host "卸载：本脚本加 -Remove 参数再跑一次。"
