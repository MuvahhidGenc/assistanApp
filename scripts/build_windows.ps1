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

Write-Host "Build complete: $exe"
