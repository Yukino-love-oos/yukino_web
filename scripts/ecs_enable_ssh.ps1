<#
.SYNOPSIS
    在你的 ECS 上一键开通 SSH（供远程自动化部署使用）。

.DESCRIPTION
    需要**管理员权限**运行。做四件事：
      1. 安装 OpenSSH Server 功能
      2. 启动 sshd 并设为开机自启
      3. 放行 22 端口（可限制来源 IP）
      4. 写入授权公钥（特别处理 administrators_authorized_keys 的 ACL，
         这是 Windows OpenSSH 最常见的坑：文件权限过松会直接拒绝登录）

.PARAMETER PublicKey
    要授权的公钥（一整行 ssh-ed25519 ...）。不填则只装服务不写公钥。

.PARAMETER Port
    SSH 端口，默认 22。改成非标准端口可降低被扫描概率。

.PARAMETER SourceAddress
    允许访问的来源地址，默认 Any。生产环境建议改成固定 IP，
    例如 -SourceAddress 1.2.3.4/32

.EXAMPLE
    # 在服务器上以管理员 PowerShell 运行
    .\ecs_enable_ssh.ps1 -PublicKey 'ssh-ed25519 AAAAC3... yukino-ecs-deploy'
    .\ecs_enable_ssh.ps1 -PublicKey 'ssh-ed25519 ...' -Port 2222 -SourceAddress 1.2.3.4/32
#>
[CmdletBinding()]
param(
    [string]$PublicKey = '',
    [int]$Port = 22,
    [string]$SourceAddress = 'Any'
)

$ErrorActionPreference = 'Stop'

# ---------- 管理员检查 ----------
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "请以【管理员】身份运行 PowerShell 再执行本脚本"
}

Write-Host "=== 1/4 安装 OpenSSH Server ===" -ForegroundColor Cyan
$cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' -ErrorAction SilentlyContinue
if ($cap -and $cap.State -eq 'Installed') {
    Write-Host "  已安装" -ForegroundColor Green
}
else {
    Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
    Write-Host "  安装完成" -ForegroundColor Green
}

Write-Host "=== 2/4 启动并设为自启 ===" -ForegroundColor Cyan
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
$svc = Get-Service sshd
Write-Host ("  sshd 状态: {0}" -f $svc.Status) -ForegroundColor Green

Write-Host "=== 3/4 防火墙放行 ===" -ForegroundColor Cyan
$ruleName = "OpenSSH-Server-$Port"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) { Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue }
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -RemoteAddress $SourceAddress -Profile Any | Out-Null
Write-Host ("  已放行 TCP {0}，来源: {1}" -f $Port, $SourceAddress) -ForegroundColor Green

if ($Port -ne 22) {
    # 改端口需要同时改 sshd_config
    $cfg = "$env:ProgramData\ssh\sshd_config"
    if (Test-Path $cfg) {
        $txt = Get-Content $cfg -Raw
        if ($txt -match '(?m)^\s*Port\s+') {
            $txt = $txt -replace '(?m)^\s*Port\s+\d+', "Port $Port"
        }
        else {
            $txt = "Port $Port`r`n" + $txt
        }
        Set-Content -Path $cfg -Value $txt -Encoding utf8
        Restart-Service sshd
        Write-Host ("  sshd_config 已改为 Port {0} 并重启" -f $Port) -ForegroundColor Green
    }
}

Write-Host "=== 4/4 授权公钥 ===" -ForegroundColor Cyan
if (-not $PublicKey) {
    Write-Host "  未提供 -PublicKey，跳过" -ForegroundColor Yellow
}
else {
    $keyFile = "$env:ProgramData\ssh\administrators_authorized_keys"
    if (-not (Test-Path $keyFile)) { New-Item -ItemType File -Path $keyFile -Force | Out-Null }
    $cur = Get-Content $keyFile -Raw -ErrorAction SilentlyContinue
    if ($cur -and $cur.Contains($PublicKey.Trim())) {
        Write-Host "  该公钥已存在，跳过" -ForegroundColor Green
    }
    else {
        Add-Content -Path $keyFile -Value $PublicKey.Trim() -Encoding ascii
        Write-Host "  已写入 administrators_authorized_keys" -ForegroundColor Green
    }

    # 关键：这个文件的 ACL 必须只保留 Administrators + SYSTEM，否则 sshd 会拒绝
    icacls $keyFile /inheritance:r | Out-Null
    icacls $keyFile /grant 'Administrators:F' | Out-Null
    icacls $keyFile /grant 'SYSTEM:F' | Out-Null
    Write-Host "  ACL 已收紧（Administrators + SYSTEM）" -ForegroundColor Green

    # 确保 sshd_config 允许公钥登录
    $cfg = "$env:ProgramData\ssh\sshd_config"
    $txt = Get-Content $cfg -Raw
    if ($txt -notmatch '(?m)^\s*PubkeyAuthentication\s+yes') {
        $txt = $txt -replace '(?m)^#?\s*PubkeyAuthentication.*$', 'PubkeyAuthentication yes'
        if ($txt -notmatch '(?m)^\s*PubkeyAuthentication') {
            $txt += "`r`nPubkeyAuthentication yes`r`n"
        }
        Set-Content -Path $cfg -Value $txt -Encoding utf8
        Restart-Service sshd
        Write-Host "  sshd_config 已启用 PubkeyAuthentication 并重启" -ForegroundColor Green
    }
}

# ---------- 汇总 ----------
Write-Host ""
Write-Host "=== 完成 ===" -ForegroundColor Green
$listen = (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host ("  监听 {0} 端口: {1}" -f $Port, ($listen -gt 0)) -ForegroundColor Green
Write-Host ""
Write-Host "  下一步（在你本机 / 我这边执行）：" -ForegroundColor Yellow
Write-Host "    ssh -i <私钥> -p $Port Administrator@<这台服务器的公网IP>"
Write-Host ""
Write-Host "  ⚠ 安全提醒：部署完成后建议" -ForegroundColor Yellow
Write-Host "      1) 安全组里把 $Port 端口的来源收紧到固定 IP"
Write-Host "      2) 或在 C:\ProgramData\ssh\administrators_authorized_keys 里删掉该公钥"
Write-Host "      3) 或 Stop-Service sshd; Set-Service sshd -StartupType Disabled"
