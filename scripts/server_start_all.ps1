<#
.SYNOPSIS
    云服务器上一键启动：GPT-SoVITS(CPU) + 雪乃 Web 服务。

.DESCRIPTION
    适用于「Windows Server + 4C8G + 无 GPU」的 ECS 部署形态（见 docs/ECS部署-Windows.md）。

    做三件事：
      1. 用整合包自带的 runtime\python.exe 在 CPU 模式下启动 GPT-SoVITS（api_v2.py）
      2. 等它就绪（轮询 /docs）
      3. 用同一个 python 启动 app.server（零依赖，不需要另装 Python 3.11）

    日志写到 logs\ 下，方便出问题排查。

.PARAMETER TtsDir
    整合包解压目录（含 runtime\python.exe 与 api_v2.py），默认 C:\yukino_tts

.PARAMETER AppDir
    本项目仓库目录（含 app\、configs\），默认本脚本的上级目录

.PARAMETER TtsPort
    TTS 端口，默认 9881（CPU 模式用 9881，避免和本地 9880 混淆）

.PARAMETER AppPort
    Web 端口，默认 8501

.PARAMETER Token
    访问令牌；建议设置。也可用环境变量 YUKINO_TOKEN

.EXAMPLE
    .\scripts\server_start_all.ps1 -TtsDir C:\yukino_tts -Token 你的令牌
#>
[CmdletBinding()]
param(
    [string]$TtsDir = 'C:\yukino_tts',
    [string]$AppDir = '',
    [int]$TtsPort = 9881,
    [int]$AppPort = 8501,
    [string]$Token = ''
)

$ErrorActionPreference = 'Stop'
if (-not $AppDir) { $AppDir = Split-Path -Parent $PSScriptRoot }

$TtsPy = Join-Path $TtsDir 'runtime\python.exe'
$TtsApi = Join-Path $TtsDir 'api_v2.py'
$TtsCfg = Join-Path $AppDir 'configs\tts_infer_cpu_server.yaml'
$LogDir = Join-Path $AppDir 'logs'

foreach ($p in @($TtsPy, $TtsApi, $TtsCfg)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "缺少文件：$p" }
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
# numba 缓存必须可写，否则 import librosa 会静默卡死
$env:NUMBA_CACHE_DIR = Join-Path $AppDir 'data\numba_cache'
New-Item -ItemType Directory -Force -Path $env:NUMBA_CACHE_DIR | Out-Null
if ($Token) { $env:YUKINO_TOKEN = $Token }

Write-Host "=== 1/2 启动 GPT-SoVITS（CPU 模式，端口 $TtsPort）===" -ForegroundColor Cyan
$ttsLog = Join-Path $LogDir 'tts.log'
$ttsErr = Join-Path $LogDir 'tts.err.log'
$tts = Start-Process -FilePath $TtsPy `
    -ArgumentList @('-u', $TtsApi, '-a', '127.0.0.1', '-p', "$TtsPort", '-c', $TtsCfg) `
    -WorkingDirectory $TtsDir `
    -RedirectStandardOutput $ttsLog -RedirectStandardError $ttsErr `
    -WindowStyle Hidden -PassThru
Write-Host ("  进程 PID {0}，日志 {1}" -f $tts.Id, $ttsLog) -ForegroundColor DarkGray

Write-Host "  等待模型加载（CPU 模式约 20~60 秒）…" -ForegroundColor Yellow
$ready = $false
for ($i = 1; $i -le 60; $i++) {
    Start-Sleep -Seconds 3
    try {
        $null = Invoke-WebRequest "http://127.0.0.1:$TtsPort/docs" -TimeoutSec 3 -UseBasicParsing
        $ready = $true
        Write-Host ("  TTS 就绪（约 {0} 秒）" -f ($i * 3)) -ForegroundColor Green
        break
    }
    catch {
        if ($tts.HasExited) {
            Write-Host "  TTS 进程已退出，查看日志：" -ForegroundColor Red
            Get-Content $ttsErr -Tail 20 -ErrorAction SilentlyContinue
            throw "TTS 启动失败"
        }
    }
}
if (-not $ready) { throw "TTS 等待超时，请查看 $ttsLog" }

Write-Host "`n=== 2/2 启动雪乃 Web 服务（端口 $AppPort）===" -ForegroundColor Cyan
$appArgs = @('-m', 'app.server', '--host', '0.0.0.0', '--port', "$AppPort")
if ($Token) { $appArgs += @('--token', $Token) }

Push-Location $AppDir
try {
    & $TtsPy @appArgs
}
finally {
    Pop-Location
    if (-not $tts.HasExited) {
        Write-Host "`n停止 TTS 进程 $($tts.Id)…" -ForegroundColor DarkGray
        Stop-Process -Id $tts.Id -Force -ErrorAction SilentlyContinue
    }
}
