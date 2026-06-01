# Package models for GitHub Release (weights stay out of Git).
# Usage: .\scripts\package_models_release.ps1 [-Version 1.0.2] [-DatasetId 7]

param(
    [string]$Version = "1.0.2",
    [int]$DatasetId = 7
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Models = Join-Path $RepoRoot "models"
$OutDir = Join-Path $RepoRoot "dist"
$ZipName = "AOI-Web-models-$Version.zip"
$ZipPath = Join-Path $OutDir $ZipName
$Staging = Join-Path $env:TEMP "aoi-models-pack-$(Get-Random)"

$weights = Join-Path $Models "datasets\$DatasetId\weights.pt"
if (-not (Test-Path $weights)) {
    Write-Error "Missing primary weights: $weights"
}

New-Item -ItemType Directory -Force -Path $Staging, $OutDir | Out-Null
$destModels = Join-Path $Staging "models"
$destWeights = Join-Path $destModels "datasets\$DatasetId"
New-Item -ItemType Directory -Force -Path $destWeights | Out-Null

foreach ($name in @("README.md", "manifest.yaml", "unified_classes.yaml")) {
    $src = Join-Path $Models $name
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination (Join-Path $destModels $name)
    }
}

Copy-Item -Path $weights -Destination (Join-Path $destWeights "weights.pt")

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $destModels -DestinationPath $ZipPath -CompressionLevel Optimal
Remove-Item -Recurse -Force $Staging

$mb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host "OK: $ZipPath ($mb MB) - only datasets/$DatasetId/weights.pt"
