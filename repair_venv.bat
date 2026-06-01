@echo off
setlocal EnableExtensions
REM Recreate .venv for THIS PC (fixes "No Python at ... Python311").
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "VENV=.venv"
set "PYEXE=%VENV%\Scripts\python.exe"
set "PYLAUNCH="

where py >nul 2>&1
if %errorlevel%==0 (
  py -3.12 -c "pass" >nul 2>&1 && set "PYLAUNCH=py -3.12"
  if not defined PYLAUNCH py -3.11 -c "pass" >nul 2>&1 && set "PYLAUNCH=py -3.11"
  if not defined PYLAUNCH set "PYLAUNCH=py -3"
) else (
  set "PYLAUNCH=python"
)

echo == AOI-Web: repair %VENV% ==
echo Using: %PYLAUNCH%
%PYLAUNCH% --version
echo.

if exist "%PYEXE%" (
  "%PYEXE%" -c "pass" >nul 2>&1
  if not errorlevel 1 (
    echo %VENV% already works. Reinstall packages only? Press Ctrl+C to cancel, any key to continue...
    pause >nul
    goto :install
  )
  echo Old %VENV% points to missing Python ^(another PC^). Removing...
  rd /s /q "%VENV%"
)

echo Creating %VENV% ...
%PYLAUNCH% -m venv "%VENV%"
if errorlevel 1 (
  echo ERROR: could not create venv.
  pause
  exit /b 1
)

:install
echo.
echo [%time%] Python in venv:
"%PYEXE%" --version
echo.
set "PYTHONUNBUFFERED=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_DEFAULT_TIMEOUT=120"

echo [%time%] pip version:
"%PYEXE%" -u -m pip --version
if errorlevel 1 goto :fail

if /i "%UPGRADE_PIP%"=="1" (
  echo [%time%] Upgrading pip -v ...
  "%PYEXE%" -u -m pip install --upgrade pip -v
  if errorlevel 1 goto :fail
) else (
  echo [%time%] Skipping pip upgrade ^(set UPGRADE_PIP=1 to upgrade^).
)

echo.
echo [%time%] Installing requirements.txt -v ^(5-20 min^)...
"%PYEXE%" -u -m pip install -r requirements.txt -v
if errorlevel 1 (
  echo.
  echo requirements.txt failed ^(often Python 3.13 or psycopg2^). Trying requirements_local.txt ...
  "%PYEXE%" -u -m pip install -r requirements_local.txt -v
  if errorlevel 1 goto :fail
)

echo.
echo [%time%] Done.
echo   run.bat          - HTTP
echo   run_https.bat    - HTTPS
echo Optional: "%PYEXE%" -m scripts.init_db
echo.
pause
exit /b 0

:fail
echo.
echo ERROR: install failed. See messages above.
pause
exit /b 1
