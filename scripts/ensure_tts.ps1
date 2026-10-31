# Yukino TTS watchdog: check port 9881 every 2 minutes and restart if down.
#
# NOTE: this file is intentionally pure ASCII.
# PowerShell 5.1 on a Chinese Windows parses .ps1 as ANSI unless the file has a
# UTF-8 BOM. Chinese comments here break parsing and the scheduled task then
# fails silently -- which is exactly what happened once (watchdog.log stayed
# empty while TTS was down for two hours).
$ErrorActionPreference = 'Continue'
$log = 'C:\upload\watchdog.log'
$ts = (Get-Date).ToString('MM-dd HH:mm:ss')

$listening = (Get-NetTCPConnection -State Listen -LocalPort 9881 -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0

if ($listening) {
    exit 0
}

Add-Content -Path $log -Value ("[$ts] TTS 9881 not listening -> restarting")

# Kill stale api_v2 processes only (never touch app.server / app.qq)
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
