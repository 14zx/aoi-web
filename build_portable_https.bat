@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM Build AOI-Web-Portable-HTTPS (PyInstaller) + smoke-run exe (~45 s).
REM ASCII-only: Cyrillic in .bat breaks cmd.exe on some locales.
REM
REM Usage (from repo root, double-click or cmd):
REM   build_portable_https.bat
REM   build_portable_https.bat my_dist
REM   build_portable_https.bat my_dist /SkipMigrate
REM   build_portable_https.bat portable_dist_smoke /IncludeDevData
REM
REM Options:
REM   DistName        folder under build\ (default: portable_dist_smoke)
REM   /SkipMigrate    do not stash/restore aoi.db, storage, logs, models, .env
REM   /IncludeDevData also merge data from repo root if newer

cd /d "%~dp0"
set "REPO_ROOT=%CD%"

set "DIST_NAME=portable_dist_smoke"
set "SKIP_MIGRATE=0"
set "INCLUDE_DEV=0"

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="/SkipMigrate" set "SKIP_MIGRATE=1" & shift & goto parse_args
if /i "%~1"=="--skip-migrate" set "SKIP_MIGRATE=1" & shift & goto parse_args
if /i "%~1"=="/IncludeDevData" set "INCLUDE_DEV=1" & shift & goto parse_args
if /i "%~1"=="--include-dev-data" set "INCLUDE_DEV=1" & shift & goto parse_args
if /i "%~1"=="/?" goto show_help
if /i "%~1"=="--help" goto show_help
if /i "%~1"=="-h" goto show_help
set "DIST_NAME=%~1"
shift
goto parse_args

:show_help
echo.
echo Usage: build_portable_https.bat [DistName] [/SkipMigrate] [/IncludeDevData]
echo   DistName        output folder under build\ ^(default: portable_dist_smoke^)
echo   /SkipMigrate    skip db/storage/logs/models/.env migration
echo   /IncludeDevData merge newer data from repo root into bundle
echo.
exit /b 0

:args_done
set "PY=%REPO_ROOT%\.venv\Scripts\python.exe"
set "PYINSTALLER=%REPO_ROOT%\.venv\Scripts\pyinstaller.exe"
set "SPEC=%REPO_ROOT%\build\AOI-Web-Portable-HTTPS.spec"
set "WORKPATH=%REPO_ROOT%\build\pyinstaller_smoke"
set "STASH=%REPO_ROOT%\build\_portable_migrate_stash"
set "BUNDLE=%REPO_ROOT%\build\%DIST_NAME%\AOI-Web-Portable-HTTPS"
set "INTERNAL=%BUNDLE%\_internal"
set "EXE=%BUNDLE%\AOI-Web-Portable-HTTPS.exe"

echo.
echo == AOI-Web portable HTTPS build ==
echo Repo:  %REPO_ROOT%
echo Dist:  build\%DIST_NAME%
echo.

if not exist "%PY%" (
  echo ERROR: .venv not found: %PY%
  echo Create venv and install requirements first.
  goto :fail_pause
)
if not exist "%PYINSTALLER%" (
  echo ERROR: pyinstaller not found: %PYINSTALLER%
  echo Run: pip install pyinstaller
  goto :fail_pause
)
if not exist "%SPEC%" (
  echo ERROR: spec not found: %SPEC%
  goto :fail_pause
)

if "%SKIP_MIGRATE%"=="0" (
  echo == Pre: stash portable data ^(db, storage, logs, models^) ==
  if exist "%STASH%" rd /s /q "%STASH%"
  mkdir "%STASH%" 2>nul

  if exist "%INTERNAL%" (
    echo   from: %INTERNAL%
    call :merge_portable_data "%INTERNAL%" "%STASH%"
  )
  if exist "%BUNDLE%" (
    echo   from: %BUNDLE%
    call :merge_portable_data "%BUNDLE%" "%STASH%"
  )

  for /d %%D in ("%REPO_ROOT%\build\portable_dist_*") do (
    if exist "%%D\AOI-Web-Portable-HTTPS\_internal" (
      echo   from: %%D\AOI-Web-Portable-HTTPS\_internal
      call :merge_portable_data "%%D\AOI-Web-Portable-HTTPS\_internal" "%STASH%"
    )
    if exist "%%D\AOI-Web-Portable-HTTPS" (
      echo   from: %%D\AOI-Web-Portable-HTTPS
      call :merge_portable_data "%%D\AOI-Web-Portable-HTTPS" "%STASH%"
    )
  )

  if "%INCLUDE_DEV%"=="1" (
    echo   from: %REPO_ROOT% ^(dev data^)
    call :merge_portable_data "%REPO_ROOT%" "%STASH%"
  )

  set "STASH_PARTS="
  if exist "%STASH%\aoi.db" set "STASH_PARTS=!STASH_PARTS! aoi.db"
  if exist "%STASH%\storage" set "STASH_PARTS=!STASH_PARTS! storage"
  if exist "%STASH%\models" set "STASH_PARTS=!STASH_PARTS! models"
  if exist "%STASH%\logs" set "STASH_PARTS=!STASH_PARTS! logs"
  if exist "%STASH%\.env" set "STASH_PARTS=!STASH_PARTS! .env"
  if defined STASH_PARTS (
    echo   stashed:!STASH_PARTS!
  ) else (
    echo   ^(no previous portable bundles to migrate^)
  )
  echo.
)

echo == Pre: compile check ^(aoi_https_frozen^) ==
"%PY%" -m py_compile "%REPO_ROOT%\scripts\aoi_https_frozen.py"
if errorlevel 1 goto :fail_pause

echo == Build PyInstaller ^(may take several minutes: PyTorch/YOLO^) ==
pushd "%REPO_ROOT%\build"
"%PYINSTALLER%" --noconfirm --workpath "%WORKPATH%" --distpath "%DIST_NAME%" "AOI-Web-Portable-HTTPS.spec"
set "PI_ERR=!ERRORLEVEL!"
popd
if not "!PI_ERR!"=="0" (
  echo ERROR: pyinstaller exit !PI_ERR!
  goto :fail_pause
)

if not exist "%EXE%" (
  echo ERROR: exe missing: %EXE%
  goto :fail_pause
)

if exist "%REPO_ROOT%\build\launch_portable_https.bat" (
  copy /Y "%REPO_ROOT%\build\launch_portable_https.bat" "%BUNDLE%\launch_portable_https.bat" >nul
)

if "%SKIP_MIGRATE%"=="0" (
  if exist "%STASH%" (
    echo == Post: restore data into new bundle ==
    if not exist "%INTERNAL%" mkdir "%INTERNAL%"
    echo   to: %INTERNAL%
    call :merge_portable_data "%STASH%" "%INTERNAL%"
    echo.
  )
)

echo == Post: smoke exe ^(45 s max^) ==
"%PY%" "%REPO_ROOT%\scripts\smoke_portable_startup.py" "%EXE%" "%BUNDLE%"
if errorlevel 1 goto :fail_pause

echo.
echo OK: portable bundle is under
echo   %BUNDLE%
if "%SKIP_MIGRATE%"=="0" if exist "%STASH%" (
  echo Data restored from portable_dist_* ^(stash: %STASH%^)
)
echo.
pause
exit /b 0

:merge_portable_data
set "SRC_ROOT=%~1"
set "DST_ROOT=%~2"
if not exist "%SRC_ROOT%" exit /b 0
if not exist "%DST_ROOT%" mkdir "%DST_ROOT%"

if exist "%SRC_ROOT%\aoi.db" copy /Y "%SRC_ROOT%\aoi.db" "%DST_ROOT%\aoi.db" >nul 2>&1
if exist "%SRC_ROOT%\.env" copy /Y "%SRC_ROOT%\.env" "%DST_ROOT%\.env" >nul 2>&1

for %%I in (storage logs models) do (
  if exist "%SRC_ROOT%\%%I" (
    if not exist "%DST_ROOT%\%%I" mkdir "%DST_ROOT%\%%I"
    robocopy "%SRC_ROOT%\%%I" "%DST_ROOT%\%%I" /E /XO /R:1 /W:1 /NFL /NDL /NJH /NJS /nc /ns /np >nul
  )
)
exit /b 0

:fail_pause
echo.
echo BUILD FAILED.
pause
exit /b 1
