# 从备份回滚 DSH 配置 + 大肥鱼插件数据
# 用法：
#   powershell -ExecutionPolicy Bypass -File "...\restore-dsh.ps1" -From "D:\Yukino\tmp\dsh-backups\dsh-backup-20261103-201500.zip"
#   ... -PluginOnly     只回滚插件数据（记忆/配置），不动 profile
#   ... -ProfileOnly    只回滚 profile 配置
#
# 安全策略：回滚前会先把当前状态另存一份 restore-backup-<时间>，不会直接覆盖丢东西。

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$From,
  [switch]$PluginOnly,
  [switch]$ProfileOnly,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'

$DSH_HOME = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$ProfileDir = Join-Path $DSH_HOME 'profiles\desktop'
$PluginData = Join-Path $DSH_HOME 'dsh-dafeiyu-dot'

if (-not (Test-Path -LiteralPath $From)) { throw "找不到备份文件：$From" }
if ($PluginOnly -and $ProfileOnly) { throw "PluginOnly 和 ProfileOnly 不能同时用。" }

$dshProc = Get-Process -ErrorAction SilentlyContinue |
  Where-Object { $_.ProcessName -match 'DeepSeek|Harness|dsh' }
if ($dshProc -and -not $Force) {
  throw "DSH 正在运行（$($dshProc.ProcessName -join ', ')），请先完全退出，或加 -Force。"
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stage = Join-Path ([System.IO.Path]::GetTempPath()) "dsh-restore-$stamp"
$safety = Join-Path (Split-Path $From -Parent) "restore-backup-$stamp"
New-Item -ItemType Directory -Force -Path $stage, $safety | Out-Null

try {
  Write-Host "[1/4] 解压备份..."
  Expand-Archive -LiteralPath $From -DestinationPath $stage -Force
  $srcProfile = Join-Path $stage 'profile'
  $srcPlugin  = Join-Path $stage 'plugin-data'
  $hasFullProfile = Test-Path -LiteralPath (Join-Path $srcProfile 'desktop')

  if (-not $PluginOnly) {
    Write-Host "[2/4] 回滚 profile 配置..."
    $safetyProfile = Join-Path $safety 'profile'
    New-Item -ItemType Directory -Force -Path $safetyProfile | Out-Null
    foreach ($f in @('package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'cordis.yml', 'cordis.patch.yml')) {
      $cur = Join-Path $ProfileDir $f
      if (Test-Path -LiteralPath $cur) { Copy-Item -LiteralPath $cur -Destination (Join-Path $safetyProfile $f) -Force }
    }
    if ($hasFullProfile) {
      Write-Host "      检测到完整 profile 备份，整目录覆盖（node_modules 一起）"
      Copy-Item -Path (Join-Path $srcProfile 'desktop\*') -Destination $ProfileDir -Recurse -Force
    } else {
      Copy-Item -Path (Join-Path $srcProfile '*') -Destination $ProfileDir -Recurse -Force
    }
  }

  if (-not $ProfileOnly -and (Test-Path -LiteralPath $srcPlugin)) {
    Write-Host "[3/4] 回滚插件数据（记忆/配置/素材）..."
    if (Test-Path -LiteralPath $PluginData) {
      Move-Item -LiteralPath $PluginData -Destination (Join-Path $safety 'plugin-data') -Force
    }
    Copy-Item -LiteralPath $srcPlugin -Destination $PluginData -Recurse -Force
  }

  Write-Host "[4/4] 完成。"
  Write-Host ""
  Write-Host "回滚结果已就位。当前被替换前的状态另存于：" -ForegroundColor Yellow
  Write-Host "  $safety"
  Write-Host ""
  Write-Host "下一步："
  Write-Host "  1) 启动 DSH，确认侧栏「大肥鱼」还在、插件没报错"
  Write-Host "  2) 如果界面提示缺依赖，等 DSH 自动安装；不行就在插件管理里重装一次"
  Write-Host "  3) 去插件配置页点「重新连接」，确认 QQ 状态"
}
finally {
  if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
}
