@echo off
setlocal
REM ASCII-only: Cyrillic/UTF-8 in .bat confuses cmd.exe block parsing on Windows.
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
  echo   Run repair_venv.bat   OR   run_local.bat ^(.venv_local^)
  pause
  exit /b 1
)

for /f "usebackq delims=" %%A in (`".venv\Scripts\python.exe" -m scripts.ensure_public_base_url --scheme http --port 8000`) do set "PUBLIC_BASE_URL=%%A"

echo HTTP: http://localhost:8000/
if not "%PUBLIC_BASE_URL%"=="" echo Phone/meta: %PUBLIC_BASE_URL%/
echo Stop: keyboard interrupt ^(Ctrl+C^).
echo.
".venv\Scripts\python.exe" -m scripts.dev_server --port 8000

pause
endlocal
