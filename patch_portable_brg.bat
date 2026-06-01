@echo off
setlocal
REM Patch portable AOI-Web: web_static only (Python stays in .exe).
REM Enables client-side BRG color fix via localStorage.
chcp 65001 >nul 2>&1

set "SRC=%~dp0app\static"
set "DST=C:\Users\Neizy\Desktop\AOI-Web-Portable-HTTPS\_internal\web_static"

if not exist "%DST%\app.js" (
  echo ERROR: Portable not found: %DST%
  echo Edit DST= in this .bat to your AOI-Web-Portable-HTTPS\_internal\web_static
  pause
  exit /b 1
)

echo Backup...
copy /Y "%DST%\app.js" "%DST%\app.js.bak" >nul
if exist "%DST%\index.html" copy /Y "%DST%\index.html" "%DST%\index.html.bak" >nul

echo Copy app.js + index.html from repo...
copy /Y "%SRC%\app.js" "%DST%\app.js"
if exist "%SRC%\index.html" copy /Y "%SRC%\index.html" "%DST%\index.html"
echo.> "%DST%\aoi_brg_fix.on"

echo.
echo Patched: app.js + marker aoi_brg_fix.on ^(auto BRG fix^).
echo.
echo Restart AOI-Web-Portable-HTTPS.exe and hard-refresh ^(Ctrl+F5^).
pause
endlocal
