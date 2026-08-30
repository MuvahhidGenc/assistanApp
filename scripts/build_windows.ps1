$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "==> Ensuring virtual environment"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

$python = ".venv\Scripts\python.exe"
$pip = ".venv\Scripts\pip.exe"
$pyinstaller = ".venv\Scripts\pyinstaller.exe"

Write-Host "==> Installing build dependencies"
& $pip install pyinstaller pillow | Out-Null

$env:PYTHONPATH = Join-Path (Get-Location) "src"
Write-Host "==> Generating tray icon assets"
& $python scripts\generate_icon.py

Write-Host "==> Building hermes-client.exe (GUI / no console)"
& $pyinstaller hermes-client.spec --noconfirm --clean

$exe = Join-Path (Get-Location) "dist\hermes-client.exe"
if (-not (Test-Path $exe)) {
    throw "Build failed: dist\hermes-client.exe not found"
}

$releaseDir = Join-Path (Get-Location) "release\HermesClient"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
$releaseExe = Join-Path $releaseDir "hermes-client.exe"
Copy-Item $exe $releaseExe -Force

$installDir = Join-Path $env:LOCALAPPDATA "HermesClient"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$target = Join-Path $installDir "hermes-client.exe"
try {
    Copy-Item $exe $target -Force
    $installed = $target
} catch {
    Write-Warning "LocalAppData copy skipped (exe may be running): $target"
    Write-Warning $_.Exception.Message
    $installed = "(skipped - close Hermes and rebuild)"
}

Write-Host "Build complete."
Write-Host "  Build output : $exe"
Write-Host "  Release pkg  : $releaseExe"
Write-Host "  Installed to : $installed"
