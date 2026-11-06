$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::ASCII

# ============================================================
#  Yukino Android shell - manual APK build (no Gradle / Android Studio)
#  aapt2 compile -> aapt2 link -> javac -> d8 -> jar -> zipalign -> apksigner
#  NOTE: pure ASCII on purpose (PowerShell 5.1 reads .ps1 as ANSI without BOM).
# ============================================================

$SDK       = 'D:\Yukino\_android\sdk'
$BT        = Join-Path $SDK 'build-tools\34.0.0'
$PLATFORM  = Join-Path $SDK 'platforms\android-34\android.jar'
$PROJ      = 'D:\Yukino\_android\app'
$OUT       = 'D:\Yukino\_android\build'
$KS        = 'D:\Yukino\_android\yukino.jks'
$KS_PASS   = 'yukino2026'
$KS_ALIAS  = 'yukino'
$VER_CODE  = 1
$VER_NAME  = '1.0'
$PKG_NAME  = 'yukino-' + $VER_NAME + '.apk'

$JAVA_HOME = 'C:\Program Files\Eclipse Adoptium\jdk-17.0.18.8-hotspot'
$javac     = Join-Path $JAVA_HOME 'bin\javac.exe'
$jar       = Join-Path $JAVA_HOME 'bin\jar.exe'
$keytool   = Join-Path $JAVA_HOME 'bin\keytool.exe'

Write-Output "=== 0) toolchain check ==="
foreach ($p in @($PLATFORM, (Join-Path $BT 'aapt2.exe'), (Join-Path $BT 'd8.bat'),
                 (Join-Path $BT 'zipalign.exe'), (Join-Path $BT 'apksigner.bat'), $javac)) {
    if (-not (Test-Path $p)) { throw "missing tool: $p" }
}
Write-Output "  ok"

Write-Output "=== 1) signing key ==="
if (-not (Test-Path $KS)) {
    & $keytool -genkeypair -v -keystore $KS -alias $KS_ALIAS -keyalg RSA -keysize 2048 `
        -validity 10950 -storepass $KS_PASS -keypass $KS_PASS `
        -dname "CN=Yukino, OU=Yukino, O=Yukino, L=Guiyang, ST=Guizhou, C=CN" 2>&1 |
        Select-Object -Last 2 | ForEach-Object { Write-Output ("  " + $_) }
} else {
    Write-Output "  reuse existing keystore (needed for in-place upgrade)"
}

Write-Output "=== 2) clean ==="
if (Test-Path $OUT) { Remove-Item $OUT -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $OUT 'compiled') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OUT 'gen') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OUT 'classes') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OUT 'dex') | Out-Null

Write-Output "=== 3) aapt2 compile (resources) ==="
& (Join-Path $BT 'aapt2.exe') compile --dir (Join-Path $PROJ 'res') -o (Join-Path $OUT 'compiled.zip') 2>&1 |
    Select-Object -Last 3 | ForEach-Object { Write-Output ("  " + $_) }
if ($LASTEXITCODE -ne 0) { throw "aapt2 compile failed" }

Write-Output "=== 4) aapt2 link (base.apk + R.java) ==="
& (Join-Path $BT 'aapt2.exe') link -o (Join-Path $OUT 'base.apk') `
    -I $PLATFORM `
    --manifest (Join-Path $PROJ 'AndroidManifest.xml') `
    -R (Join-Path $OUT 'compiled.zip') `
    --java (Join-Path $OUT 'gen') `
    --min-sdk-version 26 --target-sdk-version 34 `
    --version-code $VER_CODE --version-name $VER_NAME `
    --auto-add-overlay 2>&1 | Select-Object -Last 5 | ForEach-Object { Write-Output ("  " + $_) }
if ($LASTEXITCODE -ne 0) { throw "aapt2 link failed" }
Write-Output ("  base.apk = {0:N1} KB" -f ((Get-Item (Join-Path $OUT 'base.apk')).Length / 1KB))

Write-Output "=== 5) javac ==="
$srcs = @(Get-ChildItem (Join-Path $PROJ 'java') -Recurse -Filter '*.java' | ForEach-Object { $_.FullName })
$srcs += @(Get-ChildItem (Join-Path $OUT 'gen') -Recurse -Filter '*.java' | ForEach-Object { $_.FullName })
Write-Output ("  sources = " + $srcs.Count)
& $javac -encoding UTF-8 -source 11 -target 11 -nowarn `
    -classpath $PLATFORM -d (Join-Path $OUT 'classes') $srcs 2>&1 |
    Select-Object -Last 12 | ForEach-Object { Write-Output ("  " + $_) }
if ($LASTEXITCODE -ne 0) { throw "javac failed" }

Write-Output "=== 6) d8 (classes.dex) ==="
$classes = @(Get-ChildItem (Join-Path $OUT 'classes') -Recurse -Filter '*.class' | ForEach-Object { $_.FullName })
Write-Output ("  class files = " + $classes.Count)
& (Join-Path $BT 'd8.bat') --lib $PLATFORM --min-api 26 --output (Join-Path $OUT 'dex') $classes 2>&1 |
    Select-Object -Last 8 | ForEach-Object { Write-Output ("  " + $_) }
if ($LASTEXITCODE -ne 0) { throw "d8 failed" }
if (-not (Test-Path (Join-Path $OUT 'dex\classes.dex'))) { throw "classes.dex not produced" }
Write-Output ("  classes.dex = {0:N1} KB" -f ((Get-Item (Join-Path $OUT 'dex\classes.dex')).Length / 1KB))

Write-Output "=== 7) add dex into apk ==="
Push-Location (Join-Path $OUT 'dex')
& $jar --update --file (Join-Path $OUT 'base.apk') classes.dex 2>&1 | Select-Object -Last 3 | ForEach-Object { Write-Output ("  " + $_) }
Pop-Location
if ($LASTEXITCODE -ne 0) { throw "jar update failed" }

Write-Output "=== 8) zipalign ==="
& (Join-Path $BT 'zipalign.exe') -f -p 4 (Join-Path $OUT 'base.apk') (Join-Path $OUT 'aligned.apk') 2>&1 |
    Select-Object -Last 3 | ForEach-Object { Write-Output ("  " + $_) }
if ($LASTEXITCODE -ne 0) { throw "zipalign failed" }

Write-Output "=== 9) apksigner ==="
& (Join-Path $BT 'apksigner.bat') sign --ks $KS --ks-key-alias $KS_ALIAS `
    --ks-pass "pass:$KS_PASS" --key-pass "pass:$KS_PASS" `
    --out (Join-Path $OUT $PKG_NAME) (Join-Path $OUT 'aligned.apk') 2>&1 |
    Select-Object -Last 5 | ForEach-Object { Write-Output ("  " + $_) }
if ($LASTEXITCODE -ne 0) { throw "apksigner failed" }

Write-Output "=== 10) verify ==="
& (Join-Path $BT 'apksigner.bat') verify --print-certs (Join-Path $OUT $PKG_NAME) 2>&1 |
    Select-Object -First 6 | ForEach-Object { Write-Output ("  " + $_) }

$apk = Join-Path $OUT $PKG_NAME
Write-Output "=== DONE ==="
Write-Output ("  {0}  {1:N2} MB" -f $apk, ((Get-Item $apk).Length / 1MB))
