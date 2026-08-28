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
