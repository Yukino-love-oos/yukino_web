<#
.SYNOPSIS
    启动「手机可访问」的雪乃服务：监听局域网、自动生成访问令牌、打印手机地址。

.DESCRIPTION
    默认把服务绑到 0.0.0.0（局域网内所有设备可访问），并强制要求访问令牌，
    避免同一 Wi-Fi 下的其他人白嫖你的显卡。

    脚本会：
      1. 检查 ollama 与 GPT-SoVITS 是否在跑（没跑就提示）
      2. 生成随机令牌（或使用 -Token 指定）
      3. 打印手机可直接点开的地址
      4. 可选地添加 Windows 防火墙入站规则（需要管理员权限）

.PARAMETER Port
    监听端口，默认 8501。

.PARAMETER Token
    指定访问令牌；不填则随机生成。

.PARAMETER NoToken
    不设令牌（只在完全可信的内网使用）。

.PARAMETER OpenFirewall
    尝试添加防火墙入站规则（需要管理员 PowerShell）。不加也可以，首次访问时
    Windows 会弹窗询问是否允许，选「专用网络」即可。

.EXAMPLE
    .\scripts\start_mobile.ps1
    .\scripts\start_mobile.ps1 -Port 8600 -OpenFirewall
#>
[CmdletBinding()]
param(
    [int]$Port = 8501,
    [string]$Token = '',
    [switch]$NoToken,
    [switch]$OpenFirewall
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

# ---------- 选择 Python（优先 .venv）----------
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }

# ---------- 检查依赖服务 ----------
function Test-Port([int]$p) {
    try { $null = Invoke-WebRequest "http://127.0.0.1:$p/" -TimeoutSec 2 -UseBasicParsing; return $true }
    catch { return $false }
}

Write-Host "=== 依赖服务检查 ===" -ForegroundColor Cyan
$ttsOk = $false
try { $null = Invoke-WebRequest 'http://127.0.0.1:9880/docs' -TimeoutSec 2 -UseBasicParsing; $ttsOk = $true } catch {}
if ($ttsOk) { Write-Host "  语音服务 : 运行中" -ForegroundColor Green }
else { Write-Host "  语音服务 : 未运行 → 手机端将只有文字。启动： .\scripts\start_tts.ps1" -ForegroundColor Yellow }

$llmOk = $false
try { $null = Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 -UseBasicParsing; $llmOk = $true } catch {}
if ($llmOk) { Write-Host "  本地模型 : ollama 运行中" -ForegroundColor Green }
else { Write-Host "  本地模型 : ollama 未运行 → 启动： ollama serve" -ForegroundColor Yellow }

# ---------- 令牌 ----------
if (-not $NoToken -and -not $Token) {
    $bytes = New-Object byte[] 9
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $Token = ([Convert]::ToBase64String($bytes) -replace '[+/=]', '').Substring(0, 10)
}
if (-not $NoToken) { $env:YUKINO_TOKEN = $Token }

# ---------- 局域网地址 ----------
Write-Host "`n=== 手机访问地址 ===" -ForegroundColor Cyan
$ips = @()
try {
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike '127.*' -and
            $_.IPAddress -notlike '169.254.*' -and
            ($_.IPAddress -like '192.168.*' -or $_.IPAddress -like '10.*' -or
             ($_.IPAddress -like '172.*' -and $_.IPAddress -notlike '172.17.*'))
        } | Select-Object -ExpandProperty IPAddress
} catch {
    $ips = @((Get-NetIPConfiguration -ErrorAction SilentlyContinue |
        Where-Object { $_.IPv4DefaultGateway } | ForEach-Object { $_.IPv4Address.IPAddress }))
}
if (-not $ips -or $ips.Count -eq 0) {
    Write-Host "  未探测到局域网 IP，请用 ipconfig 查看后手动拼地址" -ForegroundColor Yellow
}
foreach ($ip in ($ips | Select-Object -Unique)) {
    $suffix = if ($NoToken) { '' } else { "?t=$Token" }
    Write-Host ("  http://{0}:{1}/{2}" -f $ip, $Port, $suffix) -ForegroundColor Green
}
Write-Host "`n  提示：手机与电脑要在同一个 Wi-Fi；先让手机连上再打开上面的地址。" -ForegroundColor DarkGray
Write-Host "  想在外面也能用 → 见 docs/移动端部署.md 的 Tailscale / Cloudflare 方案。" -ForegroundColor DarkGray

# ---------- 防火墙 ----------
if ($OpenFirewall) {
    Write-Host "`n=== 添加防火墙入站规则（需要管理员）===" -ForegroundColor Cyan
    try {
        $ruleName = "Yukino Web $Port"
        $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
        if (-not $existing) {
            New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
                -Protocol TCP -LocalPort $Port -Profile Private | Out-Null
            Write-Host "  已添加：$ruleName（仅专用网络）" -ForegroundColor Green
        }
        else { Write-Host "  规则已存在：$ruleName" -ForegroundColor Green }
    }
    catch {
        Write-Host "  添加失败（可能不是管理员）：$($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "  替代做法：首次被访问时 Windows 会弹窗，选「专用网络」允许即可。" -ForegroundColor DarkGray
    }
}

# ---------- 启动 ----------
Write-Host "`n=== 启动服务 ===" -ForegroundColor Cyan
$serverArgs = @('-m', 'app.server', '--host', '0.0.0.0', '--port', $Port)
if (-not $NoToken) { $serverArgs += @('--token', $Token) }

& $Python @serverArgs
