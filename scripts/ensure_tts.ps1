# Yukino TTS watchdog: 每 2 分钟检查一次 9881 端口，挂了就拉起来。
#
# 为什么要它：CPU 推理在长文本上会吃满内存，TTS 进程偶尔被系统杀掉，
# 表现是「机器人还在回文字，但语音一直没反应」。没有看门狗就得手动重启。
$ErrorActionPreference = 'Continue'
$log = 'C:\upload\watchdog.log'
$ts = (Get-Date).ToString('MM-dd HH:mm:ss')

$listening = (Get-NetTCPConnection -State Listen -LocalPort 9881 -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0

if ($listening) {
    # 正常时不刷日志，避免文件膨胀
    exit 0
}

Add-Content -Path $log -Value ("[$ts] TTS 9881 not listening -> restarting")

# 先把可能存在的僵尸 python 清掉（只清 api_v2，不动 app.server / app.qq）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.CommandLine -and $_.CommandLine -match 'api_v2') {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch { }
    }
}
Start-Sleep -Seconds 2

$cmd = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\upload\run_tts.ps1'
try {
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
    Add-Content -Path $log -Value ("[$ts] spawned pid=" + $r.ProcessId)
}
catch {
    Add-Content -Path $log -Value ("[$ts] spawn failed: " + $_.Exception.Message)
}
