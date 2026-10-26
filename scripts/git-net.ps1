<#
.SYNOPSIS
    让本机的 git / pip 能在这台机器上正常访问 GitHub 与 PyPI。

.DESCRIPTION
    背景：本机装了「GitHub 加速」类工具（Watt Toolkit / SteamTools / BeyondDimension），
    它用自签根证书对 github.com 做 TLS 中间人代理。后果：
      · 浏览器和 schannel 程序正常（它们信任 Windows 证书库）
      · git 默认用 schannel 后端 + 自带 CA 包 → 报 SEC_E_NO_CREDENTIALS 或
        "unable to get local issuer certificate"
      · pip 用 certifi 的 CA 包 → 同样报证书错误

    这个脚本做两件事，都是可逆的：
      1. 把 Windows 证书库（Root + CA + AuthRoot）导出成 PEM，
         供 git / pip 当作信任链使用（**不是关闭 TLS 校验**）
      2. 把 git 切到 OpenSSL 后端并指向这个 PEM

    生成的文件是本机环境相关的，已在 .gitignore 里排除，不会进仓库。

.PARAMETER OnlyExport
    只导出证书，不改 git 配置。

.EXAMPLE
    .\scripts\git-net.ps1
    # 之后： pip install -r requirements.txt   （脚本会提示怎么设 PIP_CERT）
#>
[CmdletBinding()]
param(
    [switch]$OnlyExport
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$pem = Join-Path $Root 'configs\windows-roots.pem'

Write-Host "=== 1) 导出 Windows 证书库 -> $pem ===" -ForegroundColor Cyan
$sb = New-Object System.Text.StringBuilder
$count = 0
foreach ($storeName in 'Root', 'CA', 'AuthRoot') {
    try {
        $store = New-Object System.Security.Cryptography.X509Certificates.X509Store($storeName, 'LocalMachine')
        $store.Open('ReadOnly')
        foreach ($cert in $store.Certificates) {
            [void]$sb.AppendLine('-----BEGIN CERTIFICATE-----')
            [void]$sb.AppendLine([Convert]::ToBase64String($cert.RawData, 'InsertLineBreaks'))
            [void]$sb.AppendLine('-----END CERTIFICATE-----')
            $count++
        }
        $store.Close()
    }
    catch {
        Write-Host "  跳过 $storeName : $($_.Exception.Message)" -ForegroundColor Yellow
    }
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pem) | Out-Null
[System.IO.File]::WriteAllText($pem, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))
Write-Host ("  导出 {0} 张证书，{1:N0} KB" -f $count, ((Get-Item $pem).Length / 1KB)) -ForegroundColor Green

if (-not $OnlyExport) {
    Write-Host "`n=== 2) 配置 git 使用 OpenSSL 后端 + 该证书包 ===" -ForegroundColor Cyan
    Push-Location $Root
    try {
        git config http.sslBackend openssl
        git config http.sslCAInfo $pem
        Write-Host ("  http.sslBackend = " + (git config --get http.sslBackend)) -ForegroundColor Green
        Write-Host ("  http.sslCAInfo  = " + (git config --get http.sslCAInfo)) -ForegroundColor Green
    }
    finally {
        Pop-Location
    }

    Write-Host "`n=== 3) 验证 ===" -ForegroundColor Cyan
    Push-Location $Root
    try {
        $env:GIT_TERMINAL_PROMPT = '0'
        git ls-remote --heads origin 2>&1 | Select-Object -First 3
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  git 访问 GitHub 正常 ✅" -ForegroundColor Green
        }
        else {
            Write-Host "  git 仍失败，请检查上面的错误" -ForegroundColor Yellow
        }
    }
    finally {
        Pop-Location
    }
}

Write-Host "`n=== 之后给 pip 用 ===" -ForegroundColor Cyan
Write-Host "  `$env:PIP_CERT='$pem'" -ForegroundColor Yellow
Write-Host "  # 或写进用户配置： python -m pip config set global.cert `"$pem`"" -ForegroundColor DarkGray
