@echo off
setlocal
REM HTTPS dev server using .venv_local ^(see setup_venv_local.bat^).
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "PYEXE=.venv_local\Scripts\python.exe"

if not exist "%PYEXE%" (
  echo ERROR: .venv_local not found.
  echo   Run setup_venv_local.bat first.
  pause
  exit /b 1
)
"%PYEXE%" -c "pass" >nul 2>&1
if errorlevel 1 (
  echo ERROR: .venv_local broken. Run setup_venv_local.bat again.
  pause
  exit /b 1
)

if not exist "certs\cert.pem" (
  echo Generating dev TLS certificates...
  "%PYEXE%" -m scripts.generate_dev_https_certs
  if errorlevel 1 (
    pause
    exit /b 1
  )
  echo.
)

for /f "usebackq delims=" %%A in (`"%PYEXE%" -m scripts.ensure_public_base_url --scheme https --port 8000`) do set "PUBLIC_BASE_URL=%%A"

echo HTTPS: https://localhost:8000/
if not "%PUBLIC_BASE_URL%"=="" echo Phone/meta: %PUBLIC_BASE_URL%/
echo Python: %PYEXE%
echo Stop: Ctrl+C
echo.
"%PYEXE%" -m scripts.dev_server --https --port 8000

pause
endlocal
