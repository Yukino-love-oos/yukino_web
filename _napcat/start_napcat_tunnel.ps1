# Keep a reverse SSH tunnel open so the ECS can reach NapCat running on THIS PC.
#
# Why: NapCat listens on 127.0.0.1:3000 (OneBot HTTP) and 127.0.0.1:3001 (WS events)
# on this computer. The Yukino bot runs on the Aliyun server, so we forward those
# two ports onto the server's loopback. Nothing is exposed to the public internet:
# -R binds on the server's 127.0.0.1 only.
#
# Usage: right-click -> Run with PowerShell, or double-click start_tunnel.bat.
# Keep this window open while you want the NapCat QQ account to be online.
#
# NOTE: pure ASCII on purpose (PowerShell 5.1 parses .ps1 as ANSI without a BOM).
$ErrorActionPreference = 'Continue'
$ssh = 'C:\Windows\System32\OpenSSH\ssh.exe'
$key = 'D:\Yukino\data\ecs_deploy_key'
$target = 'Administrator@42.120.61.220'

if (-not (Test-Path $ssh)) { Write-Host "[ERR] ssh.exe not found: $ssh"; exit 1 }
if (-not (Test-Path $key)) { Write-Host "[ERR] key not found: $key"; exit 1 }

Write-Host "=== reverse tunnel to $target ==="
Write-Host "  local 3000 (OneBot HTTP) -> server 127.0.0.1:3000"
Write-Host "  local 3001 (OneBot WS)   -> server 127.0.0.1:3001"
Write-Host "  keep this window open; Ctrl+C to stop"

while ($true) {
    $ts = (Get-Date).ToString('HH:mm:ss')
    Write-Host "[$ts] connecting..."
    & $ssh -i $key -N `
        -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL `
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ConnectTimeout=15 `
        -R 127.0.0.1:3000:127.0.0.1:3000 `
        -R 127.0.0.1:3001:127.0.0.1:3001 `
        $target
    $ts = (Get-Date).ToString('HH:mm:ss')
    Write-Host "[$ts] tunnel dropped, retrying in 5s (exit=$LASTEXITCODE)"
    Start-Sleep -Seconds 5
}
