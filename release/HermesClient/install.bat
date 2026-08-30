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
