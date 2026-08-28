$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$releaseRoot = Join-Path (Get-Location) "release\HermesClient"
$zipPath = Join-Path (Get-Location) "release\HermesClient.zip"
$distExe = Join-Path (Get-Location) "dist\hermes-client.exe"
$sourceConfig = Join-Path (Get-Location) "config\default.yaml"

Write-Host "==> Building Windows executable"
& (Join-Path $PSScriptRoot "build_windows.ps1")

if (-not (Test-Path $distExe)) {
    throw "Build failed: dist\hermes-client.exe not found"
}

Write-Host "==> Preparing release folder: $releaseRoot"
if (Test-Path $releaseRoot) {
    Remove-Item -Recurse -Force $releaseRoot
}
New-Item -ItemType Directory -Path $releaseRoot | Out-Null
New-Item -ItemType Directory -Path (Join-Path $releaseRoot "config") | Out-Null

Copy-Item $distExe (Join-Path $releaseRoot "hermes-client.exe")
Copy-Item $sourceConfig (Join-Path $releaseRoot "config\default.yaml")

$readme = @"
# HERMES Windows Client

Hermes AI asistan istemcisini Windows'ta sistem tepsisinde calistirir.

## Hizli baslangic

1. ``HermesClient.zip`` dosyasini acin.
2. ``install.bat`` dosyasina cift tiklayin (Windows baslangicina ekler).
3. ``hermes-client.exe`` calistirin veya oturum acinca tepsi simgesinden acin.

## Ilk kurulum

1. Tepsi simgesine sag tiklayin veya chat penceresinde **Settings**'e basin.
2. **Hermes API URL** ve **API Token** girin.
3. **Save** ile kaydedin. Ayarlar ``%LOCALAPPDATA%\HermesClient\config\default.yaml`` dosyasina yazilir.

## Loglar

Settings veya tepsi menusunden **View Logs** ile son 200 log satirini gorebilirsiniz.
**Open log file** ile ``%LOCALAPPDATA%\HermesClient\logs\app.log`` dosyasini acabilirsiniz.

## Kaldirma

``uninstall.bat`` Windows baslangic kaydini kaldirir. Klasoru silmek istege baglidir.

## Gelistirici komutlari

```powershell
# Test
.venv\Scripts\python.exe -m pytest

# Exe derleme
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1

# Release paketi (exe + zip)
powershell -ExecutionPolicy Bypass -File scripts\package_windows.ps1
```
"@
Set-Content -Path (Join-Path $releaseRoot "README.md") -Value $readme -Encoding UTF8

$installBat = @"
@echo off
cd /d "%~dp0"
echo HERMES Client baslangica ekleniyor...
hermes-client.exe install-startup
if errorlevel 1 (
  echo Kurulum basarisiz oldu.
  pause
  exit /b 1
)
echo Kurulum tamam. hermes-client.exe calistirilabilir.
pause
"@
Set-Content -Path (Join-Path $releaseRoot "install.bat") -Value $installBat -Encoding ASCII

$uninstallBat = @"
@echo off
cd /d "%~dp0"
echo HERMES Client baslangictan kaldiriliyor...
hermes-client.exe uninstall-startup
if errorlevel 1 (
  echo Kaldirma basarisiz oldu.
  pause
  exit /b 1
)
echo Baslangic kaydi kaldirildi.
pause
"@
Set-Content -Path (Join-Path $releaseRoot "uninstall.bat") -Value $uninstallBat -Encoding ASCII

Write-Host "==> Creating zip archive"
if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}
Compress-Archive -Path $releaseRoot -DestinationPath $zipPath -Force

Write-Host "Release package ready:"
Write-Host "  Folder: $releaseRoot"
Write-Host "  Zip:    $zipPath"
