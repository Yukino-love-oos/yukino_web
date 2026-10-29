<#
.SYNOPSIS
    校验上传到服务器的部署包是否完整（配合 pack_for_ecs.ps1 生成的 MANIFEST.sha256）。

.DESCRIPTION
    热点/移动网络传 8.86 GB 很容易因为断线导致文件截断，而 GPT-SoVITS 缺一个
    DLL 或权重截断后的表现往往是「启动卡住」或「推理报奇怪的错」，很难排查。
    所以传完先跑这个脚本，把问题挡在启动之前。

    校验内容（按 MANIFEST.sha256）：
      · 缺文件        -> 必须补传
      · 大小不符      -> 文件损坏/截断，必须重传
      · SHA256 不符   -> 大文件内容损坏，必须重传（小文件只校验大小）

.PARAMETER Root
    服务器上的整合包根目录，例如 C:\yukino_tts

.PARAMETER Manifest
    MANIFEST.sha256 的路径

.PARAMETER Quick
    只查「缺失 + 大小」，不算 SHA256（几十秒出结果，用于快速判断是否传完）

.EXAMPLE
    .\scripts\verify_pack.ps1 -Root C:\yukino_tts -Manifest C:\MANIFEST.sha256
    .\scripts\verify_pack.ps1 -Root C:\yukino_tts -Manifest C:\MANIFEST.sha256 -Quick
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Manifest,
    [switch]$Quick
)

$ErrorActionPreference = 'Stop'

foreach ($p in @($Root, $Manifest)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "路径不存在：$p" }
}

$rootFull = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
$rows = @(Get-Content -LiteralPath $Manifest -Encoding UTF8 | Where-Object { $_.Trim() })

$mode = '完整校验（大小 + SHA256）'
if ($Quick) { $mode = '快速校验（只查存在与大小）' }

Write-Host '=== 部署包完整性校验 ===' -ForegroundColor Cyan
Write-Host ("  目标目录 : {0}" -f $rootFull)
Write-Host ("  清单条目 : {0}" -f $rows.Count)
Write-Host ("  模式     : {0}" -f $mode)
Write-Host ''

$missing = New-Object System.Collections.Generic.List[string]
$sizeBad = New-Object System.Collections.Generic.List[string]
$hashBad = New-Object System.Collections.Generic.List[string]
$okCount = 0
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$idx = 0

foreach ($row in $rows) {
    $idx++
    if (($idx % 5000) -eq 0) {
        Write-Host ("  ...已检查 {0}/{1}（{2:N0}s）" -f $idx, $rows.Count, $sw.Elapsed.TotalSeconds) -ForegroundColor DarkGray
    }

    # 格式：<sha256|->  <size>  <relative/path>
    $parts = $row -split '\s{2,}', 3
    if ($parts.Count -lt 3) { continue }

    $expectHash = $parts[0].Trim()
    $expectSize = [long]0
    $sizeText = $parts[1].Trim()
    if (-not [long]::TryParse($sizeText, [ref]$expectSize)) { continue }

    $rel = $parts[2].Trim().Replace('/', '\')
    $full = Join-Path $rootFull $rel

    if (-not (Test-Path -LiteralPath $full)) {
        [void]$missing.Add($rel)
        continue
    }

    $item = Get-Item -LiteralPath $full
    if ($item.Length -ne $expectSize) {
        [void]$sizeBad.Add(("{0}  期望 {1} 实际 {2}" -f $rel, $expectSize, $item.Length))
        continue
    }

    if ((-not $Quick) -and ($expectHash -ne '-')) {
        $actual = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $expectHash) {
            [void]$hashBad.Add($rel)
            continue
        }
    }
    $okCount++
}

Write-Host ''
Write-Host '=== 结果 ===' -ForegroundColor Cyan
Write-Host ("  通过        : {0}" -f $okCount) -ForegroundColor Green
if ($missing.Count -gt 0) { Write-Host ("  缺失        : {0}" -f $missing.Count) -ForegroundColor Red }
else { Write-Host '  缺失        : 0' -ForegroundColor Green }
if ($sizeBad.Count -gt 0) { Write-Host ("  大小不符    : {0}" -f $sizeBad.Count) -ForegroundColor Red }
else { Write-Host '  大小不符    : 0' -ForegroundColor Green }
if (-not $Quick) {
    if ($hashBad.Count -gt 0) { Write-Host ("  SHA256 不符 : {0}" -f $hashBad.Count) -ForegroundColor Red }
    else { Write-Host '  SHA256 不符 : 0' -ForegroundColor Green }
}
Write-Host ("  耗时        : {0:N1} 秒" -f $sw.Elapsed.TotalSeconds) -ForegroundColor DarkGray

if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Host '--- 缺失文件（最多列 30 个）---' -ForegroundColor Red
    $missing | Select-Object -First 30 | ForEach-Object { Write-Host "  $_" }
}
if ($sizeBad.Count -gt 0) {
    Write-Host ''
    Write-Host '--- 大小不符（截断，必须重传）---' -ForegroundColor Red
    $sizeBad | Select-Object -First 20 | ForEach-Object { Write-Host "  $_" }
}
if ($hashBad.Count -gt 0) {
    Write-Host ''
    Write-Host '--- SHA256 不符（内容损坏，必须重传）---' -ForegroundColor Red
    $hashBad | Select-Object -First 20 | ForEach-Object { Write-Host "  $_" }
}

$bad = $missing.Count + $sizeBad.Count
if (-not $Quick) { $bad = $bad + $hashBad.Count }

if ($bad -eq 0) {
    Write-Host ''
    Write-Host '校验通过，可以启动服务了。' -ForegroundColor Green
    exit 0
}
Write-Host ''
Write-Host ("有 {0} 个文件有问题，请按上面的清单重传后再启动。" -f $bad) -ForegroundColor Red
exit 1
