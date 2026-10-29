<#
.SYNOPSIS
    为 ECS 部署打包「最小可用集」——把 22 GB 整合包精简到约 9 GB。

.DESCRIPTION
    GPT-SoVITS 整合包 22 GB 里有大量本项目用不到的东西，而云服务器系统盘通常只有 40 GB。
    本脚本的策略是「整目录复制 + 只删明确无用的大块」，而不是白名单挑选
    —— 白名单漏一个模块就会在服务器上启动失败（实测踩过 eres2net / tools.audio_sr 两个坑）。

    保留：runtime、GPT_SoVITS(代码+必需模型)、tools(除 asr/uvr5)、雪乃权重、参考音频
    剔除：tools/asr 1.1G、tools/uvr5 0.7G、v3/v4/v2Pro 预训练底模约 3.0G、BigVGAN HF 模型 215M

    另外会**重写 runtime/Lib/site-packages/users.pth**：
    原文件写死了 6 条 D:\Yukino\... 绝对路径，换机器必然失效；
    改成相对路径后可在任意目录部署。

.PARAMETER OutDir
    输出目录，默认 D:\Yukino\_pack_ecs

.EXAMPLE
    .\scripts\pack_for_ecs.ps1
#>
[CmdletBinding()]
param(
    [string]$OutDir = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $Root '_pack_ecs' }

$Bundle = Join-Path $Root 'yukino_voice\GPT-SoVITS-v2pro-20250604'
$Dest   = Join-Path $OutDir 'yukino_tts'

if (-not (Test-Path $Bundle)) { throw "找不到整合包：$Bundle" }
if (Test-Path $Dest) {
    Write-Host "清理旧输出 $Dest ..." -ForegroundColor DarkGray
    Remove-Item $Dest -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

function Copy-Safe([string]$from, [string]$to) {
    if (-not (Test-Path -LiteralPath $from)) {
        Write-Host "  跳过（不存在）：$from" -ForegroundColor DarkYellow
        return
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
    Copy-Item -LiteralPath $from -Destination $to -Recurse -Force
}

function Get-SizeGB([string]$p) {
    $s = Get-ChildItem $p -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum
    return ($s.Sum / 1GB)
}

# ---------------- 1) 运行时 ----------------
Write-Host "=== 1/6 Python 运行时（约 6.5 GB）===" -ForegroundColor Cyan
Copy-Safe (Join-Path $Bundle 'runtime') (Join-Path $Dest 'runtime')

# ---------------- 2) GPT_SoVITS：整目录，再删无用底模 ----------------
Write-Host "=== 2/6 GPT_SoVITS（整目录复制后精简底模）===" -ForegroundColor Cyan
Copy-Safe (Join-Path $Bundle 'GPT_SoVITS') (Join-Path $Dest 'GPT_SoVITS')

# 只保留 v2 推理真正需要的四样：
#   chinese-roberta-wwm-ext-large  BERT 文本特征
#   chinese-hubert-base            CNHuBERT 音色特征
#   fast_langdetect                语种检测
#   sv                             sv.py 要加载 pretrained_eres2netv2w24s4ep4.ckpt
$keepModels = @('chinese-roberta-wwm-ext-large', 'chinese-hubert-base', 'fast_langdetect', 'sv')
$pmDir = Join-Path $Dest 'GPT_SoVITS\pretrained_models'
if (Test-Path $pmDir) {
    Get-ChildItem $pmDir -Force | ForEach-Object {
        if (($keepModels -notcontains $_.Name) -and ($_.Name -notlike '.*')) {
            Write-Host "     剔除 $($_.Name)" -ForegroundColor DarkGray
            Remove-Item $_.FullName -Recurse -Force
        }
    }
}

# ---------------- 3) tools：保留代码，删 asr / uvr5 模型 ----------------
Write-Host "=== 3/6 tools（保留 audio_sr/AP_BWE_main，删 asr/uvr5）===" -ForegroundColor Cyan
Copy-Safe (Join-Path $Bundle 'tools') (Join-Path $Dest 'tools')
foreach ($drop in 'tools\asr', 'tools\uvr5', 'tools\__pycache__') {
    $p = Join-Path $Dest $drop
    if (Test-Path $p) {
        Write-Host "     剔除 $drop" -ForegroundColor DarkGray
        Remove-Item $p -Recurse -Force
    }
}

# ---------------- 4) 服务脚本 ----------------
Write-Host "=== 4/6 服务脚本 ===" -ForegroundColor Cyan
foreach ($f in 'api_v2.py', 'config.py', 'weight.json', 'requirements.txt', 'extra-req.txt', 'LICENSE') {
    Copy-Safe (Join-Path $Bundle $f) (Join-Path $Dest $f)
}

# ---------------- 5) 雪乃权重 + 参考音频 ----------------
Write-Host "=== 5/6 雪乃权重与参考音频 ===" -ForegroundColor Cyan
$yk = Join-Path $Root 'yukino\Yukino'
Copy-Safe (Join-Path $yk 'GPT_weights\default.ckpt')         (Join-Path $Dest 'GPT_weights\default.ckpt')
Copy-Safe (Join-Path $yk 'GPT_weights\Yukino_Weak-e25.ckpt') (Join-Path $Dest 'GPT_weights\Yukino_Weak-e25.ckpt')
foreach ($p in 'Yukino_Strong.pth', 'Yukino_Weak_01.pth', 'Yukino_Weak_02.pth') {
    Copy-Safe (Join-Path $yk "SoVITS_weights\$p") (Join-Path $Dest "SoVITS_weights\$p")
}
Get-ChildItem (Join-Path $yk '参考音频') -File -Filter '*.wav' -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Safe $_.FullName (Join-Path $Dest "参考音频\$($_.Name)")
}

# ---------------- 6) 修正 users.pth（关键）----------------
Write-Host "=== 6/6 重写 users.pth 为相对路径 ===" -ForegroundColor Cyan
$pth = Join-Path $Dest 'runtime\Lib\site-packages\users.pth'
if (Test-Path (Split-Path -Parent $pth)) {
    # 相对 site-packages 往上三级 = 整合包根目录；用正斜杠，Win/Linux 都认
    $lines = @(
        '../../../',
        '../../../GPT_SoVITS/BigVGAN',
        '../../../GPT_SoVITS',
        '../../../tools'
    )
    [System.IO.File]::WriteAllLines($pth, $lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "  已写入相对路径版本：" -ForegroundColor Green
    $lines | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
}
else {
    Write-Host "  警告：找不到 site-packages，跳过 users.pth 修正" -ForegroundColor Yellow
}

# ---------------- 7) 生成校验清单（防止传坏/传漏）----------------
Write-Host "=== 7/7 生成 MANIFEST.sha256（大文件算哈希，小文件只记大小）===" -ForegroundColor Cyan
$manifestPath = Join-Path $OutDir 'MANIFEST.sha256'
$allFiles = Get-ChildItem $Dest -Recurse -File -Force -ErrorAction SilentlyContinue
$lines = New-Object System.Collections.Generic.List[string]
$hashed = 0
$t = [System.Diagnostics.Stopwatch]::StartNew()
foreach ($f in $allFiles) {
    $rel = $f.FullName.Substring($Dest.Length + 1).Replace('\', '/')
    if ($f.Length -ge 1MB) {
        $h = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLower()
        $lines.Add("$h  $($f.Length)  $rel")
        $hashed++
    }
    else {
        # 小文件只记大小，避免为 5 万个小文件算哈希拖慢打包
        $lines.Add("-                                 $($f.Length)  $rel")
    }
}
[System.IO.File]::WriteAllLines($manifestPath, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Host ("  已生成 {0}" -f $manifestPath) -ForegroundColor Green
Write-Host ("  条目 {0} 个（其中 {1} 个大文件带 SHA256），耗时 {2:N0} 秒" -f $lines.Count, $hashed, $t.Elapsed.TotalSeconds) -ForegroundColor DarkGray

# ---------------- 报告 ----------------
Write-Host ""
Write-Host "=== 打包完成 ===" -ForegroundColor Green
$s = Get-ChildItem $Dest -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum
Write-Host ("  输出目录 : {0}" -f $Dest)
Write-Host ("  校验清单 : {0}" -f $manifestPath)
Write-Host ("  文件数   : {0}" -f $s.Count)
Write-Host ("  总大小   : {0:N2} GB" -f ($s.Sum / 1GB))
Write-Host ""
Write-Host "  各子目录：" -ForegroundColor Cyan
foreach ($d in Get-ChildItem $Dest -Directory) {
    Write-Host ("    {0,-18} {1,8:N2} GB" -f $d.Name, (Get-SizeGB $d.FullName))
}
Write-Host ""
Write-Host "  接下来：" -ForegroundColor Yellow
Write-Host "    1) 上传到服务器（务必用可断点续传的方式，见 docs/ECS部署-Windows.md 步骤 3）"
Write-Host "    2) 上传完成后在服务器执行： .\scripts\verify_pack.ps1 -Root C:\yukino_tts -Manifest C:\MANIFEST.sha256"
Write-Host "    3) 冒烟测试：用 runtime 启动 api_v2，确认能出声再继续"
