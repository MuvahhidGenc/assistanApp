$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

Write-Host "=== [1/6] Kill existing hermes-client processes ==="
Get-Process hermes-client -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

$ExePath = Join-Path (Split-Path -Parent $PSScriptRoot) "dist\hermes-client.exe"
$HermesRoot = Join-Path $env:LOCALAPPDATA "HermesClient"
$CfgPath = Join-Path $HermesRoot "config\default.yaml"
$LogPath = Join-Path $HermesRoot "logs\app.log"
$LogDir  = Split-Path -Parent $LogPath
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Host "=== [2/6] Clear old log ==="
if (Test-Path $LogPath) { Remove-Item $LogPath -Force -ErrorAction SilentlyContinue }
$PreCfgExists = Test-Path $CfgPath
if ($PreCfgExists) { $PreCfgMtime = (Get-Item $CfgPath).LastWriteTimeUtc.ToString("o") }

Write-Host "=== [3/6] Start EXE, hold 5s ==="
Write-Host "  EXE: $ExePath"
if (-not (Test-Path $ExePath)) { throw "EXE missing: $ExePath" }

$proc = Start-Process -FilePath $ExePath -PassThru -ErrorAction Stop
Write-Host "  PID=$($proc.Id)"
Start-Sleep -Seconds 5
$StillRunning = -not $proc.HasExited
Write-Host "  Still running after 5s: $StillRunning"

if ($StillRunning) {
  Write-Host "  -> Graceful CloseMainWindow"
  try { $proc.CloseMainWindow() | Out-Null } catch {}
  Start-Sleep -Milliseconds 600
  if (-not $proc.HasExited) {
    Write-Host "  -> Force kill"
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 400
  }
}

try { $ExitCode = $proc.ExitCode } catch { $ExitCode = "(still running)" }
Write-Host "  ExitCode = $ExitCode"

Write-Host "=== [4/6] Config file state ==="
if (Test-Path $CfgPath) {
  $fi = Get-Item $CfgPath
  Write-Host "  EXISTS     : YES"
  Write-Host "  PATH       : $($fi.FullName)"
  Write-Host "  SIZE       : $($fi.Length) bytes"
  Write-Host "  LastWrite  : $($fi.LastWriteTimeUtc.ToString('o'))"
  if ($PreCfgExists) { Write-Host "  Pre-existed mtime: $PreCfgMtime" }
  Write-Host "  IsReadOnly : $($fi.IsReadOnly)"
  $head = Get-Content $CfgPath -First 20
  Write-Host "  FIRST 20 lines of default.yaml:"
  $head | ForEach-Object { Write-Host "    | $_" }
} else {
  Write-Host "  EXISTS: NO (bootstrap failed to create it)"
}

Write-Host "=== [5/6] HermesClient user directory contents ==="
if (Test-Path $HermesRoot) {
  Get-ChildItem -Path $HermesRoot -Recurse -Depth 2 -ErrorAction SilentlyContinue |
    ForEach-Object {
      $tag = if ($_.PSIsContainer) { "DIR " } else { "FILE" }
      Write-Host ("  [{0}] {1}  [{2}b]" -f $tag, $_.FullName, ($_.Length -as [int]))
    }
} else {
  Write-Host "  (HermesClient dir missing)"
}

Write-Host "=== [6/6] app.log analysis ==="
if (Test-Path $LogPath) {
  $all = Get-Content $LogPath
  Write-Host "  LOG LINES = $($all.Count)"
  Write-Host "  -------- HEAD (first 60 lines) --------"
  $all | Select-Object -First 60 | ForEach-Object { Write-Host "    $_" }
  if ($all.Count -gt 60) {
    Write-Host "  -------- TAIL (last 80 lines) --------"
    $all | Select-Object -Last 80 | ForEach-Object { Write-Host "    $_" }
  }
  Write-Host "  ---- ERROR SCAN ----"
  $bad = Select-String -Path $LogPath -Pattern "PermissionError|Errno.13|Traceback|Exception|FATAL" -AllMatches -ErrorAction SilentlyContinue
  if ($bad) {
    $bad | ForEach-Object { Write-Host "    [BAD] L$($_.LineNumber): $($_.Line)" }
  } else {
    Write-Host "    [CLEAN] PermissionError / Errno 13 / Traceback / Exception / FATAL YOK"
  }
  Write-Host "  ---- BOOTSTRAP MARKERS ----"
  $markers = Select-String -Path $LogPath -Pattern "bootstrap|user.config|settings.store|config_client|ensure_client|run_tray|tray|v4.home|V4AppShell|app.init|server.url" -AllMatches -ErrorAction SilentlyContinue
  if ($markers) {
    $markers | ForEach-Object { Write-Host "    [OK] L$($_.LineNumber): $($_.Line)" }
  } else {
    Write-Host "    (no specific bootstrap markers found in log lines)"
  }
} else {
  Write-Host "  [MISSING] app.log bulunamadi -> setup_app_logging FAIL veya loglama EXE icinde calismadi."
}

Write-Host "=============== SMOKE SUMMARY ==============="
Write-Host "  StillRunning(5s) : $StillRunning"
Write-Host "  ExitCode         : $ExitCode"
Write-Host "  Config exists    : $(Test-Path $CfgPath)"
Write-Host "  Log exists       : $(Test-Path $LogPath)"
$pass = $true
if (-not (Test-Path $CfgPath)) { $pass = $false }
if ($StillRunning -eq $false -and $ExitCode -ne 0 -and $null -ne $ExitCode) {
  # Crashed before 5s -> fail
  $pass = $false
}
if (Test-Path $LogPath) {
  $bad2 = Select-String -Path $LogPath -Pattern "PermissionError|Errno.13|Traceback" -Quiet -ErrorAction SilentlyContinue
  if ($bad2) { $pass = $false }
}
Write-Host "  SMOKE TEST PASS  : $pass"
if ($pass) { Write-Host "RESULT: PASS"; exit 0 } else { Write-Host "RESULT: FAIL"; exit 1 }
