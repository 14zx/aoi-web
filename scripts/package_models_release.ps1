# Упаковка каталога models/ в ZIP для GitHub Release (веса отдельно от исходников).
# Usage: .\scripts\package_models_release.ps1 [-Version 1.0.0]

param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Models = Join-Path $RepoRoot "models"
$OutDir = Join-Path $RepoRoot "dist"
$ZipName = "AOI-Web-models-$Version.zip"
$ZipPath = Join-Path $OutDir $ZipName
$Staging = Join-Path $env:TEMP "aoi-models-pack-$(Get-Random)"

if (-not (Test-Path $Models)) {
    Write-Error "Нет каталога models: $Models"
}

$ptFiles = Get-ChildItem -Path $Models -Recurse -File -Include *.pt | Where-Object { $_.Length -gt 0 }
if (-not $ptFiles) {
    Write-Error "В models/ нет .pt файлов. Сначала распакуйте веса или запустите download_pretrained."
}

New-Item -ItemType Directory -Force -Path $Staging, $OutDir | Out-Null
$destModels = Join-Path $Staging "models"
New-Item -ItemType Directory -Force -Path $destModels | Out-Null

# Конфиги + README + manifest
Copy-Item -Path (Join-Path $Models "README.md") -Destination $destModels -ErrorAction SilentlyContinue
Copy-Item -Path (Join-Path $Models "manifest.yaml") -Destination $destModels -ErrorAction SilentlyContinue
Copy-Item -Path (Join-Path $Models "unified_classes.yaml") -Destination $destModels -ErrorAction SilentlyContinue

foreach ($f in $ptFiles) {
    $rel = $f.FullName.Substring($Models.Length).TrimStart('\', '/')
    $target = Join-Path $destModels $rel
    $dir = Split-Path $target -Parent
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Copy-Item -Path $f.FullName -Destination $target
}

# yaml/json рядом с весами
Get-ChildItem -Path $Models -Recurse -File -Include *.yaml,*.yml,*.json |
    Where-Object { $_.DirectoryName -notmatch '\\\.git' } |
    ForEach-Object {
        $rel = $_.FullName.Substring($Models.Length).TrimStart('\', '/')
        $target = Join-Path $destModels $rel
        $dir = Split-Path $target -Parent
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Copy-Item -Path $_.FullName -Destination $target -Force
    }

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path (Join-Path $Staging "models") -DestinationPath $ZipPath -CompressionLevel Optimal
Remove-Item -Recurse -Force $Staging

$mb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host "OK: $ZipPath ($mb MB)"
Write-Host "GitHub: Releases -> New release -> прикрепить $ZipName (отдельно от portable)"
