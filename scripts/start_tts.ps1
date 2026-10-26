<#
.SYNOPSIS
    启动雪之下雪乃的 GPT-SoVITS 语音服务（api_v2.py，CUDA + 雪乃音色）。

.DESCRIPTION
    不改动 yukino_voice 里的第三方整合包，只从外部传入配置与运行环境。
    服务起来后监听 http://127.0.0.1:9880 ，接口：
        GET  /tts                    合成语音
        GET  /set_gpt_weights        切换 GPT 权重
        GET  /set_sovits_weights     切换 SoVITS 权重
        GET  /control?command=restart

    实测要点（已在本机验证）：
      · 权重必须用「GPT_weights/*.ckpt + SoVITS_weights/Yukino_*.pth」这一对
      · 那个 517 MB 的 default.pth / YUK95.pth 是 so-vits-svc 模型，
        GPT-SoVITS 加载不了，别填进 vits_weights_path
      · 加载时出现 _IncompatibleKeys(missing_keys=['enc_q...']) 是正常的：
        enc_q 是训练用的后验编码器，推理用不到
      · NUMBA_CACHE_DIR 必须指到可写目录。numba 导入 librosa 时要写缓存，
        若整合包目录是只读的，import 会**卡死**（不是报错，是静默挂住）

.PARAMETER Port
    监听端口，默认 9880。

.PARAMETER Voice
    strong | weak，默认 strong。
        strong = GPT default.ckpt        + SoVITS Yukino_Strong.pth
        weak   = GPT Yukino_Weak-e25.ckpt + SoVITS Yukino_Weak_01.pth

.PARAMETER Wait
    启动后在当前窗口轮询 /docs，打印「就绪」再退出（用于脚本编排）。

.EXAMPLE
    .\scripts\start_tts.ps1
    .\scripts\start_tts.ps1 -Voice weak
#>
[CmdletBinding()]
param(
    [int]$Port = 9880,
    [ValidateSet('strong', 'weak')]
    [string]$Voice = 'strong'
)

$ErrorActionPreference = 'Stop'

$Root   = Split-Path -Parent $PSScriptRoot
$Bundle = Join-Path $Root 'yukino_voice\GPT-SoVITS-v2pro-20250604'
$Python = Join-Path $Bundle 'runtime\python.exe'
$Api    = Join-Path $Bundle 'api_v2.py'
$BaseCfg = Join-Path $Root 'configs\tts_infer_yukino.yaml'

foreach ($p in @($Bundle, $Python, $Api, $BaseCfg)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "缺少必要文件/目录：$p" }
}

# ---- 音色档位 ----
$voices = @{
    strong = @{
        gpt    = 'D:/Yukino/yukino/Yukino/GPT_weights/default.ckpt'
        sovits = 'D:/Yukino/yukino/Yukino/SoVITS_weights/Yukino_Strong.pth'
    }
    weak   = @{
        gpt    = 'D:/Yukino/yukino/Yukino/GPT_weights/Yukino_Weak-e25.ckpt'
        sovits = 'D:/Yukino/yukino/Yukino/SoVITS_weights/Yukino_Weak_01.pth'
    }
}
$v = $voices[$Voice]

# ---- 生成运行时配置（把所选音色写进 custom 段）----
$runCfg = Join-Path $Root "configs\.tts_runtime_$Voice.yaml"
$template = Get-Content -LiteralPath $BaseCfg -Raw -Encoding UTF8
$template = $template -replace '(?m)^\s*t2s_weights_path:.*$',  "  t2s_weights_path: $($v.gpt)"
$template = $template -replace '(?m)^\s*vits_weights_path:.*$', "  vits_weights_path: $($v.sovits)"
Set-Content -LiteralPath $runCfg -Value $template -Encoding UTF8

# ---- 运行环境（关键）----
# 1) numba 缓存必须可写，否则 import librosa 会卡死而不是报错
$env:NUMBA_CACHE_DIR = Join-Path $Root 'data\numba_cache'
New-Item -ItemType Directory -Force -Path $env:NUMBA_CACHE_DIR | Out-Null
# 2) Windows 控制台/管道默认 GBK，中文日志会 UnicodeEncodeError
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  雪之下雪乃 · GPT-SoVITS 语音服务" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  音色   : $Voice"
Write-Host "     GPT    : $($v.gpt)"
Write-Host "     SoVITS : $($v.sovits)"
Write-Host "  配置   : $runCfg"
Write-Host "  地址   : http://127.0.0.1:$Port"
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "首次加载约 15~40 秒（含 BERT / CNHuBERT / 两个权重）。" -ForegroundColor Yellow
Write-Host "看到 'Uvicorn running on ...' 即可使用；Ctrl+C 退出。" -ForegroundColor Yellow
Write-Host ""

Push-Location $Bundle
try {
    & $Python -u $Api -a 127.0.0.1 -p $Port -c $runCfg
}
finally {
    Pop-Location
}
