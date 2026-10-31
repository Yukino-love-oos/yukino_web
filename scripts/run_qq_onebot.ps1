# Yukino bot #2 - NapCat / OneBot 11 backend (personal QQ alt account).
#
# Runs on the ECS next to the official bot. Both share the same persona, memory
# and SQLite DB, so she is the same "person" on both QQ accounts.
#
# NapCat runs on the user's PC and its OneBot ports are forwarded to this server's
# loopback by scripts/start_napcat_tunnel.ps1 (ssh -R 3000/3001).
#
# NOTE: pure ASCII on purpose (PowerShell 5.1 parses .ps1 as ANSI without a BOM).
$ErrorActionPreference = 'Continue'
$env:NUMBA_CACHE_DIR = 'C:\yukino_numba'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$env:QQ_BACKEND = 'onebot'
$env:ONEBOT_HTTP = 'http://127.0.0.1:3000'
$env:ONEBOT_WS = 'ws://127.0.0.1:3001'

# Token created in NapCat WebUI (config: OneBot HTTP/WS). Kept in a separate
# file so the script does not need editing when the token changes.
$tokenFile = 'C:\upload\onebot_token.txt'
if (Test-Path $tokenFile) {
    $env:ONEBOT_TOKEN = (Get-Content $tokenFile -Raw).Trim()
}

Set-Location 'C:\yukino_app'
& 'C:\Program Files\Python39\python.exe' -u -m app.qq *> 'C:\upload\qq_onebot.log'
