# Package models/ into ZIP for GitHub Release (weights stay out of Git).
# Usage: .\scripts\package_models_release.ps1 [-Version 1.0.1]

param(
    [string]$Version = "1.0.1"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Models = Join-Path $RepoRoot "models"
$OutDir = Join-Path $RepoRoot "dist"
$ZipName = "AOI-Web-models-$Version.zip"
$ZipPath = Join-Path $OutDir $ZipName
$Staging = Join-Path $env:TEMP "aoi-models-pack-$(Get-Random)"

if (-not (Test-Path $Models)) {
    Write-Error "Missing models directory: $Models"
}

$ptFiles = Get-ChildItem -Path $Models -Recurse -File -Filter "*.pt" | Where-Object { $_.Length -gt 0 }
if (-not $ptFiles) {
    Write-Error "No .pt files under models/. Add weights or run download scripts."
}

function Copy-RelToStaging($SourceFile, $ModelsRoot, $DestRoot) {
    $rel = $SourceFile.FullName.Substring($ModelsRoot.Length).TrimStart("\", "/")
    $target = Join-Path $DestRoot $rel
    $parent = Split-Path $target -Parent
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -Path $SourceFile.FullName -Destination $target -Force
}

New-Item -ItemType Directory -Force -Path $Staging, $OutDir | Out-Null
$destModels = Join-Path $Staging "models"
New-Item -ItemType Directory -Force -Path $destModels | Out-Null

foreach ($name in @("README.md", "manifest.yaml", "unified_classes.yaml")) {
    $src = Join-Path $Models $name
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination (Join-Path $destModels $name)
    }
}

foreach ($f in $ptFiles) {
    Copy-RelToStaging $f $Models $destModels
}

Get-ChildItem -Path $Models -Recurse -File -Include *.yaml, *.yml, *.json |
    Where-Object { $_.DirectoryName -notmatch '\\\.git' } |
    ForEach-Object { Copy-RelToStaging $_ $Models $destModels }

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $destModels -DestinationPath $ZipPath -CompressionLevel Optimal
Remove-Item -Recurse -Force $Staging

$mb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host "OK: $ZipPath ($mb MB)"
Write-Host "Upload to GitHub Release tag v$Version as $ZipName"
