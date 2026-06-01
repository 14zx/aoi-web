@echo off
setlocal
REM Use ASCII only in this file: UTF-8/Cyrillic breaks cmd.exe line parsing on some locales.
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found.
  echo   Run repair_venv.bat
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "pass" >nul 2>&1
if errorlevel 1 (
  echo ERROR: .venv was created on another PC ^(Python 3.11 missing^).
  echo   Run repair_venv.bat   OR   run_local_https.bat ^(.venv_local^)
  pause
  exit /b 1
)

if not exist "certs\cert.pem" (
  echo Generating dev TLS certificates...
  ".venv\Scripts\python.exe" -m scripts.generate_dev_https_certs
  if errorlevel 1 (
    pause
    exit /b 1
  )
  echo.
)

REM PUBLIC_BASE_URL for /api/meta and phone links: LAN IPv4 if not set in .env / environment
for /f "usebackq delims=" %%A in (`".venv\Scripts\python.exe" -m scripts.ensure_public_base_url --scheme https --port 8000`) do set "PUBLIC_BASE_URL=%%A"

echo HTTPS: https://localhost:8000/
if not "%PUBLIC_BASE_URL%"=="" echo Phone/meta: %PUBLIC_BASE_URL%/
echo Stop: keyboard interrupt ^(Ctrl+C^).
echo.
".venv\Scripts\python.exe" -m scripts.dev_server --https --port 8000

pause
endlocal
