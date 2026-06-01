# Упаковка portable-сборки в ZIP для GitHub Release.
# Usage:
#   .\scripts\package_release.ps1
#   .\scripts\package_release.ps1 -DistName portable_dist_release -Version 1.0.0

param(
    [string]$DistName = "portable_dist_release",
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Bundle = Join-Path $RepoRoot "build\$DistName\AOI-Web-Portable-HTTPS"
$Exe = Join-Path $Bundle "AOI-Web-Portable-HTTPS.exe"

if (-not (Test-Path $Exe)) {
    Write-Error "Не найден $Exe. Сначала выполните: .\build_portable_https.bat $DistName /SkipMigrate"
}

$OutDir = Join-Path $RepoRoot "dist"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$ZipName = "AOI-Web-Portable-HTTPS-$Version-win64.zip"
$ZipPath = Join-Path $OutDir $ZipName

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }

Write-Host "Архивирование: $Bundle"
Compress-Archive -Path $Bundle -DestinationPath $ZipPath -CompressionLevel Optimal

$sizeMb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host "OK: $ZipPath ($sizeMb MB)"
Write-Host "Для релиза: gh release create v$Version --title ""АОИ-Web $Version"" ""$ZipPath"""
