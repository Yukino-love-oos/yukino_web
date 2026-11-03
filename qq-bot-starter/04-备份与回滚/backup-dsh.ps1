# 备份 DSH 配置 + 大肥鱼插件数据
# 用法：
#   powershell -ExecutionPolicy Bypass -File "D:\Yukino\qq-bot-starter\04-备份与回滚\backup-dsh.ps1"
#   ... -Full          连整个 profile（含 node_modules）一起打包，慢、体积大
#   ... -OutDir "E:\backup"   换输出目录
#
# 备份内容：
#   profile\             package.json / pnpm-lock.yaml / cordis*.yml 等配置
#     （-Full 时是完整 profile 目录）
#   plugin-data\         C:\Users\PC\.dsh\dsh-dafeiyu-dot\
#                        含记忆宫殿 memory-palace（.state 隐藏目录也会带上）
#   manifest.txt         版本、时间、已装插件清单

[CmdletBinding()]
param(
  [string]$OutDir = 'D:\Yukino\tmp\dsh-backups',
  [switch]$Full,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'

$DSH_HOME = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$ProfileDir = Join-Path $DSH_HOME 'profiles\desktop'
$PluginData = Join-Path $DSH_HOME 'dsh-dafeiyu-dot'

Write-Host "DSH_HOME : $DSH_HOME"
Write-Host "Profile  : $ProfileDir"
Write-Host "插件数据 : $PluginData"
Write-Host ""

if (-not (Test-Path -LiteralPath $ProfileDir)) {
  throw "找不到 profile 目录：$ProfileDir（DSH 装好了吗？）"
}

# DSH 运行时改配置有冲突风险，先拦一下
$dshProc = Get-Process -ErrorAction SilentlyContinue |
  Where-Object { $_.ProcessName -match 'DeepSeek|Harness|dsh' }
if ($dshProc -and -not $Force) {
  Write-Warning "检测到 DSH 可能正在运行：$($dshProc.ProcessName -join ', ')"
  throw "请先完全退出 DSH 再备份，或加 -Force 强制继续。"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$stamp    = Get-Date -Format 'yyyyMMdd-HHmmss'
$zipPath  = Join-Path $OutDir "dsh-backup-$stamp.zip"
$stage    = Join-Path ([System.IO.Path]::GetTempPath()) "dsh-backup-$stamp"
New-Item -ItemType Directory -Force -Path $stage | Out-Null

try {
  # 1) profile 配置
  $profileStage = Join-Path $stage 'profile'
  New-Item -ItemType Directory -Force -Path $profileStage | Out-Null

  if ($Full) {
    Write-Host "[1/3] 复制完整 profile（含 node_modules，可能较慢）..."
    Copy-Item -LiteralPath $ProfileDir -Destination $profileStage -Recurse -Force
    # 去掉运行期缓存，减小体积
    $profileInner = Join-Path $profileStage 'desktop'
    foreach ($junk in @('.dsh-market', '.plugin-manager')) {
      $p = Join-Path $profileInner $junk
      if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force }
    }
  } else {
    Write-Host "[1/3] 复制 profile 配置..."
    $files = @('package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'cordis.yml', 'cordis.patch.yml')
    foreach ($f in $files) {
      $src = Join-Path $ProfileDir $f
      if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $profileStage $f) -Force
        Write-Host "      + $f"
      }
    }
  }

  # 2) 插件数据（记忆/配置/素材）
  if (Test-Path -LiteralPath $PluginData) {
    Write-Host "[2/3] 复制插件数据（含记忆宫殿）..."
    Copy-Item -LiteralPath $PluginData -Destination (Join-Path $stage 'plugin-data') -Recurse -Force
  } else {
    Write-Host "[2/3] 未发现插件数据目录，跳过（还没装插件？）"
  }

  # 3) manifest
  Write-Host "[3/3] 生成 manifest..."
  $deps = @()
  try {
    $pkg = Get-Content -LiteralPath (Join-Path $ProfileDir 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $deps = @($pkg.dependencies.PSObject.Properties | ForEach-Object { "{0} = {1}" -f $_.Name, $_.Value })
  } catch {
    $deps = @('(读取 package.json 失败)')
  }

  $manifest = @(
    "备份时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "DSH_HOME: $DSH_HOME",
    "profile: $ProfileDir",
    "完整模式: $([bool]$Full)",
    "",
    "== 已装依赖 ==",
    ($deps -join [Environment]::NewLine)
  ) -join [Environment]::NewLine
  Set-Content -LiteralPath (Join-Path $stage 'manifest.txt') -Value $manifest -Encoding UTF8

  Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zipPath -CompressionLevel Optimal -Force

  $sizeKB = [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1KB, 0)
  Write-Host ""
  Write-Host "备份完成：$zipPath  ($sizeKB KB)" -ForegroundColor Green
  Write-Host "回滚方法：04-备份与回滚\restore-dsh.ps1 -From `"$zipPath`""
}
finally {
  if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
}
