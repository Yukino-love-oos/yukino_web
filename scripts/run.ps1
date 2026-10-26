<#
.SYNOPSIS
    一键启动雪之下雪乃（自动拉起语音服务）。

.DESCRIPTION
    两种模式：
      Web  （默认）流式网页界面，python -m streamlit run app/ui/streamlit_app.py
      Cli           终端对话，python -m app.cli

    会先检测 TTS 服务是否在监听，没有的话在新窗口拉起 scripts/start_tts.ps1。

.PARAMETER Web
    启动网页版（默认）。

.PARAMETER Cli
    启动终端版。

.PARAMETER Mock
    使用离线假模型（不需要 DEEPSEEK_API_KEY，不消耗额度）。

.PARAMETER NoTts
    不启动语音服务，纯文字模式。

.PARAMETER Port
    TTS 端口，默认 9880。

.EXAMPLE
    .\scripts\run.ps1
    .\scripts\run.ps1 -Mock
    .\scripts\run.ps1 -Cli -Mock
    .\scripts\run.ps1 -NoTts
#>
[CmdletBinding(DefaultParameterSetName = 'Web')]
param(
    [Parameter(ParameterSetName = 'Web')][switch]$Web,
    [Parameter(ParameterSetName = 'Cli')][switch]$Cli,
    [switch]$Mock,
    [switch]$NoTts,
    [int]$Port = 9880
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if ($Mock) { $env:YUKINO_MOCK_LLM = '1' }

# ---------- 选择 Python ----------
$Python = $null
foreach ($cand in @(
    (Join-Path $Root '.venv\Scripts\python.exe'),
    'python'
)) {
    try {
        $ver = & $cand -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver) {
            if ([version]$ver -ge [version]'3.10') { $Python = $cand; break }
        }
    } catch { }
}
if (-not $Python) { throw "找不到可用的 Python(>=3.10)，请先安装或创建 .venv" }

Write-Host "Python : $Python" -ForegroundColor DarkGray

# ---------- TTS ----------
$ttsUp = $false
try {
    $null = Invoke-WebRequest "http://127.0.0.1:$Port/docs" -TimeoutSec 2 -UseBasicParsing
    $ttsUp = $true
} catch { $ttsUp = $false }

if ($NoTts) {
    Write-Host "语音   : 已跳过（-NoTts）" -ForegroundColor Yellow
}
elseif ($ttsUp) {
    Write-Host "语音   : 已在运行 (端口 $Port)" -ForegroundColor Green
}
else {
    Write-Host "语音   : 未运行，正在新窗口启动…" -ForegroundColor Yellow
    Start-Process -FilePath 'powershell' -ArgumentList @(
        '-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $Root 'scripts\start_tts.ps1'),
        '-Port', $Port
    ) | Out-Null
    Write-Host "         首次加载约 15~40 秒，可先进入对话（会自动降级为文字）" -ForegroundColor DarkGray
}

# ---------- 检查 streamlit（只有 Streamlit 版网页需要）----------
$useCli = $Cli.IsPresent
$hasStreamlit = $false
if (-not $useCli) {
    try {
        $null = & $Python -c "import streamlit" 2>$null
        $hasStreamlit = ($LASTEXITCODE -eq 0)
    } catch { $hasStreamlit = $false }

    if (-not $hasStreamlit) {
        # 没装 streamlit 也不影响网页版：退回零依赖的 app/server.py
        Write-Host ""
        Write-Host "未安装 streamlit，改用零依赖网页版（标准库 http.server）。" -ForegroundColor Yellow
        Write-Host "想要 Streamlit 版： .\\scripts\\setup.ps1" -ForegroundColor DarkGray
        Write-Host ""
    }
}

# ---------- 启动 ----------
if ($useCli) {
    & $Python -m app.cli
}
elseif (-not $hasStreamlit) {
    & $Python -m app.server
}
else {
    & $Python -m streamlit run (Join-Path $Root 'app\ui\streamlit_app.py')
}
