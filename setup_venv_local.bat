@echo off
setlocal EnableExtensions
REM Create .venv_local and install requirements_local.txt
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "VENV=.venv_local"
set "PYEXE=%VENV%\Scripts\python.exe"
set "PYTHONUNBUFFERED=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_DEFAULT_TIMEOUT=120"

set "PYLAUNCH="
where py >nul 2>&1
if %errorlevel%==0 (
  py -3.12 -c "pass" >nul 2>&1 && set "PYLAUNCH=py -3.12"
  if not defined PYLAUNCH py -3.11 -c "pass" >nul 2>&1 && set "PYLAUNCH=py -3.11"
  if not defined PYLAUNCH set "PYLAUNCH=py -3"
) else (
  set "PYLAUNCH=python"
)

echo == AOI-Web: setup %VENV% ==
echo Using: %PYLAUNCH%
echo TIP: project on Google Drive may hang pip for minutes — copy folder to C:\dev\diplome if stuck.
echo.

if not exist "%PYEXE%" (
  echo Creating virtual environment...
  %PYLAUNCH% -m venv "%VENV%"
  if errorlevel 1 goto :fail
) else (
  "%PYEXE%" -c "pass" >nul 2>&1
  if errorlevel 1 (
    echo Broken %VENV%, recreating...
    rd /s /q "%VENV%"
    %PYLAUNCH% -m venv "%VENV%"
    if errorlevel 1 goto :fail
  ) else (
    echo Virtual environment OK: %VENV%
  )
)

echo.
"%PYEXE%" --version
echo.
echo [%time%] Checking pip...
"%PYEXE%" -u -m pip --version
if errorlevel 1 goto :fail

echo [%time%] Checking PyPI ^(15 s timeout^)...
"%PYEXE%" -u -c "import urllib.request; urllib.request.urlopen('https://pypi.org/simple/pip/', timeout=15); print('PyPI OK')"
if errorlevel 1 (
  echo WARNING: no PyPI — fix Wi-Fi/VPN/proxy, then retry.
  echo.
)

if /i "%UPGRADE_PIP%"=="1" (
  echo [%time%] Upgrading pip ^(verbose^)...
  "%PYEXE%" -u -m pip install --upgrade pip -v
  if errorlevel 1 goto :fail
  echo [%time%] pip upgrade done.
) else (
  echo [%time%] Skipping pip upgrade ^(fast^). To force: set UPGRADE_PIP=1 and run again.
)

echo.
echo [%time%] Installing packages — verbose log, 5-20 min...
"%PYEXE%" -u -m pip install -r requirements_local.txt -v
if errorlevel 1 goto :fail

echo.
echo [%time%] SUCCESS.
echo   run_local.bat  /  run_local_https.bat
echo Optional: "%PYEXE%" -m scripts.init_db
echo.
pause
exit /b 0

:fail
echo.
echo [%time%] FAILED. Try in cmd:
echo   cd /d "%~dp0"
echo   "%PYEXE%" -u -m pip install -r requirements_local.txt -v
echo.
pause
exit /b 1
