<#
.SYNOPSIS
    找出并处理 D:\Yukino 下的重复大文件（同一文件的多份拷贝）。

.DESCRIPTION
    默认只做「报告」：按 大小 → MD5 分组，列出每组浪费的空间，不做任何修改。

    加 -Apply 才会动手，两种处理方式：
        -Mode HardLink （默认）删除多余副本，改为指向保留副本的硬链接
                        同盘、零额外空间、路径照旧可用（GPT-SoVITS 按路径加载不受影响）
        -Mode Delete            直接删除多余副本

    保留策略（KeepPriority）：优先保留 yukino\Yukino\ 下的规范副本，
    其次是 YUK4th95% 原始包，最后才是整合包目录里的拷贝。

    已被哈希验证的重复（本机实测）：
        · default.pth / YUK95.pth     517 MB × 3  → 省 1.03 GB
        · default.ckpt                148 MB × 3  → 省 296 MB
        · y.wav                       0.6 MB × 6
        · 参考音频 001~003             3 份
    另有 2 GB 级的「无效模型」，见 -ShowObsolete：
        517 MB 的 default.pth / YUK95.pth 是 so-vits-svc 模型，
        GPT-SoVITS 无法加载（这也是 weight.json 里 "SoVITS" 空着的原因）

.PARAMETER Apply
    真正执行（默认只报告）。

.PARAMETER Mode
    HardLink 或 Delete。

.PARAMETER MinMB
    只处理大于该体积的文件，默认 1 MB。

.PARAMETER ShowObsolete
    只列出疑似无效的 so-vits-svc 模型，供人工决定是否删除。

.EXAMPLE
    .\scripts\dedupe.ps1                  # 只报告
    .\scripts\dedupe.ps1 -ShowObsolete    # 看看哪些模型是没用的
    .\scripts\dedupe.ps1 -Apply           # 用硬链接去重
    .\scripts\dedupe.ps1 -Apply -Mode Delete
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [ValidateSet('HardLink', 'Delete')][string]$Mode = 'HardLink',
    [int]$MinMB = 1,
    [switch]$ShowObsolete
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

$exts = @('.pth', '.ckpt', '.pt', '.wav', '.zip', '.onnx', '.safetensors')
$skipDirs = @(
    (Join-Path $Root '.git'),
    (Join-Path $Root 'yukino_voice\GPT-SoVITS-v2pro-20250604\runtime'),
    (Join-Path $Root 'yukino_voice\GPT-SoVITS-v2pro-20250604\tools'),
    (Join-Path $Root 'data\tts_cache')
)

function Test-Skip([string]$path) {
    foreach ($d in $skipDirs) { if ($path.StartsWith($d, 'OrdinalIgnoreCase')) { return $true } }
    return $false
}

# 保留优先级：数字越小越优先保留
function Get-Priority([string]$rel) {
    if ($rel -like 'yukino\Yukino\GPT_weights\*')    { return 1 }
    if ($rel -like 'yukino\Yukino\SoVITS_weights\*') { return 2 }
    if ($rel -like 'yukino\Yukino\参考音频\*')        { return 3 }
    if ($rel -like 'YUK4th95%\*')                    { return 4 }
    if ($rel -like 'yukino_voice\*')                 { return 5 }
    return 6
}

$minBytes = $MinMB * 1MB
Write-Host "扫描 $Root …" -ForegroundColor Cyan

$files = Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object {
        $exts -contains $_.Extension.ToLower() -and
        $_.Length -ge $minBytes -and
        -not (Test-Skip $_.FullName)
    }

Write-Host ("候选文件 {0} 个，共 {1:N2} GB" -f $files.Count, (($files | Measure-Object Length -Sum).Sum / 1GB))
Write-Host "计算哈希…" -ForegroundColor Cyan

$records = foreach ($f in $files) {
    [pscustomobject]@{
        Full  = $f.FullName
        Rel   = $f.FullName.Substring($Root.Length + 1)
        Size  = $f.Length
        Prio  = Get-Priority $f.FullName.Substring($Root.Length + 1)
        Hash  = (Get-FileHash -LiteralPath $f.FullName -Algorithm MD5).Hash
    }
}

$groups = $records | Group-Object Hash | Where-Object { $_.Count -gt 1 }

if (-not $groups) {
    Write-Host "没有发现重复文件。" -ForegroundColor Green
    return
}

Write-Host ""
Write-Host "=== 重复分组 ===" -ForegroundColor Cyan
$totalWaste = 0
$plan = @()

foreach ($g in $groups) {
    $sorted = $g.Group | Sort-Object Prio, Rel
    $keep = $sorted[0]
    $others = $sorted | Select-Object -Skip 1
    $waste = ($g.Group.Count - 1) * $keep.Size
    $totalWaste += $waste

    Write-Host ("`n[{0:N1} MB × {1}]  可省 {2:N1} MB" -f ($keep.Size / 1MB), $g.Count, ($waste / 1MB)) -ForegroundColor Yellow
    Write-Host ("  保留: {0}" -f $keep.Rel) -ForegroundColor Green
    foreach ($o in $others) {
        Write-Host ("  冗余: {0}" -f $o.Rel) -ForegroundColor DarkGray
        $plan += [pscustomobject]@{ Keep = $keep; Dup = $o }
    }
}

Write-Host ""
Write-Host ("=== 合计可省 {0:N2} GB ===" -f ($totalWaste / 1GB)) -ForegroundColor Cyan

if ($ShowObsolete) {
    Write-Host ""
    Write-Host "=== 疑似无效模型（so-vits-svc，GPT-SoVITS 加载不了）===" -ForegroundColor Magenta
    foreach ($p in @(
        'yukino\Yukino\default.pth',
        'yukino\Yukino\default.zip',
        'yukino_voice\GPT-SoVITS-v2pro-20250604\SoVITS_weights\default.pth',
        'YUK4th95%\YUK95.pth',
        'YUK4th95%\kmeans_10000YUK95.pt'
    )) {
        $full = Join-Path $Root $p
        if (Test-Path -LiteralPath $full) {
            $mb = [math]::Round((Get-Item -LiteralPath $full).Length / 1MB, 1)
            Write-Host ("  {0,8} MB  {1}" -f $mb, $p) -ForegroundColor DarkMagenta
        }
    }
    Write-Host "  提示：这些是早期 so-vits-svc 路线的产物，删掉不影响 GPT-SoVITS 出声。" -ForegroundColor DarkGray
    Write-Host "        确认前建议先 sha256 存档： Get-FileHash <文件> -Algorithm SHA256" -ForegroundColor DarkGray
}

if (-not $Apply) {
    Write-Host ""
    Write-Host "以上仅为报告，未修改任何文件。" -ForegroundColor Green
    Write-Host "要真正处理请加 -Apply（默认硬链接）：" -ForegroundColor Yellow
    Write-Host "    .\scripts\dedupe.ps1 -Apply" -ForegroundColor Yellow
    Write-Host "    .\scripts\dedupe.ps1 -Apply -Mode Delete" -ForegroundColor Yellow
    return
}

# ---------------- 执行 ----------------
Write-Host ""
Write-Host "开始处理（$Mode）…" -ForegroundColor Cyan
$done = 0; $failed = 0
foreach ($item in $plan) {
    $dup = $item.Dup.Full
    $keep = $item.Keep.Full
    try {
        if ($Mode -eq 'Delete') {
            Remove-Item -LiteralPath $dup -Force
        }
        else {
            Remove-Item -LiteralPath $dup -Force
            New-Item -ItemType HardLink -Path $dup -Target $keep | Out-Null
            # 校验：硬链接内容必须与保留副本一致
            $h1 = (Get-FileHash -LiteralPath $dup  -Algorithm MD5).Hash
            $h2 = (Get-FileHash -LiteralPath $keep -Algorithm MD5).Hash
            if ($h1 -ne $h2) { throw "硬链接校验失败" }
        }
        Write-Host ("  OK   {0}" -f $item.Dup.Rel) -ForegroundColor Green
        $done++
    }
    catch {
        Write-Host ("  FAIL {0}  {1}" -f $item.Dup.Rel, $_.Exception.Message) -ForegroundColor Red
        $failed++
    }
}
Write-Host ""
Write-Host ("处理完成：成功 {0}，失败 {1}" -f $done, $failed) -ForegroundColor Cyan
if ($Mode -eq 'HardLink') {
    Write-Host "硬链接删除任意一份不影响另一份内容；想彻底删掉需删除所有链接。" -ForegroundColor DarkGray
}
