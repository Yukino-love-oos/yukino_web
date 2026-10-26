<#
.SYNOPSIS
    一键准备运行环境：创建 .venv、处理本机 TLS 代理、安装依赖。

.DESCRIPTION
    解决三类在中文 Windows + GitHub 加速工具环境下常见的坑：

    1) pip 读 requirements.txt 报 UnicodeDecodeError: 'gbk' codec
       —— pip 用系统区域编码读需求文件。本项目的 requirements.txt
          因此保持纯 ASCII；README / 注释里才用中文。

    2) pip / git 报 SSL 证书错误
       —— 本机若装了 Watt Toolkit / SteamTools 之类「GitHub 加速」工具，
          它会对 HTTPS 做中间人，自签根证书只在 Windows 证书库里。
          脚本会导出系统证书并把 PIP_CERT 指过去（不是关闭校验）。

    3) 沙箱/权限不允许写用户级 site-packages
       —— 依赖一律装进项目内的 .venv，run.ps1 会自动优先用它。

.PARAMETER WithAsr
    额外安装语音输入依赖（faster-whisper / silero-vad / sounddevice / soundfile）。

.EXAMPLE
    .\scripts\setup.ps1
    .\scripts\setup.ps1 -WithAsr
#>
[CmdletBinding()]
param(
    [switch]$WithAsr
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "=== 1) 准备 TLS 证书（解决中间人代理导致的证书错误）===" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot 'git-net.ps1') -OnlyExport | Out-Host
$pem = Join-Path $Root 'configs\windows-roots.pem'
if (Test-Path $pem) {
    $env:PIP_CERT = $pem
    Write-Host "  PIP_CERT = $pem" -ForegroundColor Green
}
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

Write-Host "`n=== 2) 创建 / 复用 .venv ===" -ForegroundColor Cyan
$venvPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    $base = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $base) { throw "找不到 python，请先安装 Python 3.10+ 并加入 PATH" }
    Write-Host "  使用基础解释器: $base"
    & $base -m venv .venv
}
if (-not (Test-Path $venvPy)) { throw ".venv 创建失败" }
Write-Host ("  .venv python: " + (& $venvPy -V 2>&1)) -ForegroundColor Green

Write-Host "`n=== 3) 升级 pip ===" -ForegroundColor Cyan
& $venvPy -m pip install --quiet --upgrade pip
Write-Host ("  pip " + (& $venvPy -m pip --version)) -ForegroundColor Green

Write-Host "`n=== 4) 安装 requirements.txt ===" -ForegroundColor Cyan
& $venvPy -m pip install -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败，看上面的报错" }

Write-Host "`n=== 5) 自检 ===" -ForegroundColor Cyan
& $venvPy -c "import streamlit, sys; print('  streamlit', streamlit.__version__, '| python', sys.version.split()[0])"

if ($WithAsr) {
    Write-Host "`n=== 6) 额外安装语音输入依赖 ===" -ForegroundColor Cyan
    & $venvPy -m pip install "faster-whisper>=1.0" "silero-vad>=5.0" "sounddevice>=0.4.6" "soundfile>=0.12"
}

Write-Host "`n完成。接下来：" -ForegroundColor Green
Write-Host "  .\scripts\run.ps1 -Cli      # 终端版（最快验证）" -ForegroundColor Yellow
Write-Host "  .\scripts\run.ps1           # 网页版" -ForegroundColor Yellow
