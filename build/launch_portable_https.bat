@echo off
setlocal
REM ASCII-only: Cyrillic in .bat breaks cmd.exe on some locales.
REM PyInstaller may place this file in _internal; exe lives one level up.
set "HERE=%~dp0"
if exist "%HERE%AOI-Web-Portable-HTTPS.exe" (
  cd /d "%HERE%"
) else if exist "%HERE%..\AOI-Web-Portable-HTTPS.exe" (
  cd /d "%HERE%..\"
) else (
  echo ERROR: AOI-Web-Portable-HTTPS.exe not found next to this script or in parent folder.
  pause
  exit /b 1
)
"AOI-Web-Portable-HTTPS.exe" %*
set EX=%ERRORLEVEL%
if not %EX%==0 (
  echo.
  echo Server exited with code %EX%.
  pause
)
endlocal
exit /b %EX%
