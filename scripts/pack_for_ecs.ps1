<#
.SYNOPSIS
    为 ECS 部署打包「最小可用集」——把 22 GB 整合包精简到约 8 GB。

.DESCRIPTION
    GPT-SoVITS 整合包 22 GB 里有大量本项目用不到的东西：
      · tools\asr      1.1 GB  （ASR 走云 API / faster-whisper，不用它）
      · tools\uvr5     0.7 GB  （人声分离，训练时才用）
      · 预训练底模 v3/v4/v2Pro 约 3.0 GB（我们用自己的雪乃权重）
      · BigVGAN HuggingFace 模型 215 MB（v3/v4 才用）
    精简后只保留「v2 推理 + 你的权重」所需文件。

    同时因为云服务器系统盘只有 40 GB（Windows 自身约占 20 GB），
    精简是必须的，不是优化。

.PARAMETER OutDir
    输出目录，默认 D:\Yukino\_pack_ecs

.EXAMPLE
    .\scripts\pack_for_ecs.ps1
    # 之后把 D:\Yukino\_pack_ecs\ 整个压缩上传到服务器
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

function Copy-ItemSafe([string]$from, [string]$to) {
    if (-not (Test-Path -LiteralPath $from)) {
        Write-Host "  跳过（不存在）：$from" -ForegroundColor DarkYellow
        return
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
    Copy-Item -LiteralPath $from -Destination $to -Recurse -Force
}

# ---------------- 1) 运行时 ----------------
Write-Host "=== 1/4 复制 Python 运行时（约 6.5 GB，含 CUDA 版 torch，无 GPU 时会自动走 CPU）===" -ForegroundColor Cyan
Copy-ItemSafe (Join-Path $Bundle 'runtime') (Join-Path $Dest 'runtime')

# ---------------- 2) 启动脚本与配置 ----------------
Write-Host "=== 2/4 复制服务脚本 ===" -ForegroundColor Cyan
foreach ($f in @('api_v2.py', 'config.py', 'weight.json', 'requirements.txt', 'extra-req.txt', 'LICENSE')) {
    Copy-ItemSafe (Join-Path $Bundle $f) (Join-Path $Dest $f)
}
# i18n 是 api_v2.py 的必需依赖（tools 下只有它是必需的）
Copy-ItemSafe (Join-Path $Bundle 'tools\i18n') (Join-Path $Dest 'tools\i18n')

# ---------------- 3) GPT_SoVITS：代码 + 必需模型 ----------------
Write-Host "=== 3/4 复制 GPT_SoVITS 代码与必需模型 ===" -ForegroundColor Cyan
$gsvSrc = Join-Path $Bundle 'GPT_SoVITS'
$gsvDst = Join-Path $Dest 'GPT_SoVITS'
New-Item -ItemType Directory -Force -Path $gsvDst | Out-Null

# 顶层 .py 代码
Get-ChildItem $gsvSrc -File -Filter *.py -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $gsvDst $_.Name) -Force
}
# 子目录：text(清洗/分词) / BigVGAN(代码) / AR / module / feature_extractor / TTS_infer_pack 等
foreach ($d in 'text', 'BigVGAN', 'AR', 'module', 'feature_extractor', 'TTS_infer_pack', 'configs', 'pretrained_models') {
    $src = Join-Path $gsvSrc $d
    if (Test-Path $src) { Copy-ItemSafe $src (Join-Path $gsvDst $d) }
}

# 只保留 v2 推理真正需要的预训练件
$keepModels = @(
    'chinese-roberta-wwm-ext-large',       # BERT（多语言文本特征）
    'chinese-hubert-base',                 # CNHuBERT（音色/语义特征）
    'fast_langdetect'                      # 语种检测
)
Write-Host "  -> 清理不需要的预训练模型（v3/v4/v2Pro、BigVGAN HF）" -ForegroundColor DarkGray
$pmDir = Join-Path $gsvDst 'pretrained_models'
if (Test-Path $pmDir) {
    Get-ChildItem $pmDir -Force | ForEach-Object {
        $name = $_.Name
        $isKeep = ($keepModels -contains $name)
        $isDot  = $name -like '.*'
        if (-not $isKeep -and -not $isDot) {
            Write-Host "     移除 $name" -ForegroundColor DarkGray
            Remove-Item $_.FullName -Recurse -Force
        }
    }
}

# ---------------- 4) 你的雪乃权重 ----------------
Write-Host "=== 4/4 复制雪乃权重与参考音频 ===" -ForegroundColor Cyan
$yk = Join-Path $Root 'yukino\Yukino'
Copy-ItemSafe (Join-Path $yk 'GPT_weights\default.ckpt')          (Join-Path $Dest 'GPT_weights\default.ckpt')
Copy-ItemSafe (Join-Path $yk 'GPT_weights\Yukino_Weak-e25.ckpt')  (Join-Path $Dest 'GPT_weights\Yukino_Weak-e25.ckpt')
foreach ($p in 'Yukino_Strong.pth', 'Yukino_Weak_01.pth', 'Yukino_Weak_02.pth') {
    Copy-ItemSafe (Join-Path $yk "SoVITS_weights\$p") (Join-Path $Dest "SoVITS_weights\$p")
}
Get-ChildItem (Join-Path $yk '参考音频') -File -Filter '*.wav' -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $Dest "参考音频\$($_.Name)") -Force
}

# ---------------- 报告 ----------------
Write-Host ""
Write-Host "=== 打包完成 ===" -ForegroundColor Green
$s = Get-ChildItem $Dest -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum
Write-Host ("  输出目录 : {0}" -f $Dest)
Write-Host ("  文件数   : {0}" -f $s.Count)
Write-Host ("  总大小   : {0:N2} GB" -f ($s.Sum / 1GB))
Write-Host ""
Write-Host "  各子目录：" -ForegroundColor Cyan
foreach ($d in Get-ChildItem $Dest -Directory) {
    $ds = Get-ChildItem $d.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum
    Write-Host ("    {0,-18} {1,8:N2} GB" -f $d.Name, ($ds.Sum / 1GB))
}
Write-Host ""
Write-Host "  下一步：" -ForegroundColor Yellow
Write-Host "    1) 压缩： Compress-Archive -Path '$Dest' -DestinationPath 'D:\Yukino\_pack_ecs\yukino_tts.zip'"
Write-Host "    2) 上传到服务器（见 docs/ECS部署-Windows.md）"
