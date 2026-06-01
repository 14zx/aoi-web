# Build AOI-Web-Portable-HTTPS (PyInstaller) and smoke-run the exe.
# Run from repo root:  powershell -ExecutionPolicy Bypass -File scripts\build_portable_https.ps1
# Optional:  -DistName my_dist  (folder under build\, default portable_dist_smoke)
#            -SkipMigrate     (не переносить aoi.db / storage / logs / models / .env)
#            -IncludeDevData   (добавить данные из корня репозитория, если новее)

param(
    [string]$DistName = "portable_dist_smoke",
    [switch]$SkipMigrate,
    [switch]$IncludeDevData
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$PyInstaller = Join-Path $RepoRoot ".venv\Scripts\pyinstaller.exe"
$Spec = Join-Path $RepoRoot "build\AOI-Web-Portable-HTTPS.spec"
$WorkPath = Join-Path $RepoRoot "build\pyinstaller_smoke"
$StashRoot = Join-Path $RepoRoot "build\_portable_migrate_stash"

# Пользовательские данные portable (каталог _internal при запуске exe).
$PortableDataItems = @(
    @{ Name = "aoi.db"; Dir = $false },
    @{ Name = "storage"; Dir = $true },
    @{ Name = "logs"; Dir = $true },
    @{ Name = "models"; Dir = $true },
    @{ Name = ".env"; Dir = $false }
)

function Get-PortableDataRoots {
    <# Каталоги, где exe мог создавать aoi.db / storage / logs (cwd и _internal). #>
    param([string]$DistFolderName)
    $bundle = Join-Path $RepoRoot "build\$DistFolderName\AOI-Web-Portable-HTTPS"
    $roots = [System.Collections.Generic.List[string]]::new()
    foreach ($sub in @("_internal", ".")) {
        $p = if ($sub -eq ".") { $bundle } else { Join-Path $bundle "_internal" }
        if (Test-Path $p) {
            $resolved = (Resolve-Path $p).Path
            if ($roots -notcontains $resolved) {
                $roots.Add($resolved) | Out-Null
            }
        }
    }
    return ,$roots.ToArray()
}

function Copy-PortableFileIfNewer {
    param(
        [string]$SourceFile,
        [string]$DestFile
    )
    if (-not (Test-Path $SourceFile)) { return }
    $destDir = Split-Path $DestFile -Parent
    if ($destDir -and -not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    $copy = $true
    if (Test-Path $DestFile) {
        $srcInfo = Get-Item $SourceFile
        $dstInfo = Get-Item $DestFile
        if ($srcInfo.LastWriteTimeUtc -le $dstInfo.LastWriteTimeUtc -and $srcInfo.Length -le $dstInfo.Length) {
            $copy = $false
        }
    }
    if ($copy) {
        Copy-Item -LiteralPath $SourceFile -Destination $DestFile -Force
    }
}

function Merge-PortableUserData {
    param(
        [string]$SourceRoot,
        [string]$DestRoot
    )
    if (-not $SourceRoot -or -not (Test-Path $SourceRoot)) { return }
    if (-not (Test-Path $DestRoot)) {
        New-Item -ItemType Directory -Path $DestRoot -Force | Out-Null
    }
    foreach ($item in $PortableDataItems) {
        $src = Join-Path $SourceRoot $item.Name
        $dst = Join-Path $DestRoot $item.Name
        if (-not (Test-Path $src)) { continue }
        if ($item.Dir) {
            if (-not (Test-Path $dst)) {
                New-Item -ItemType Directory -Path $dst -Force | Out-Null
            }
            # /E — дерево; без /PURGE — не удалять лишнее в dest; новее — перезаписать
            & robocopy $src $dst /E /XO /R:1 /W:1 /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
        } else {
            Copy-PortableFileIfNewer -SourceFile $src -DestFile $dst
        }
    }
}

function Collect-PortableMigrateSources {
    $list = [System.Collections.Generic.List[string]]::new()

    foreach ($root in (Get-PortableDataRoots -DistFolderName $DistName)) {
        if ($list -notcontains $root) { $list.Add($root) | Out-Null }
    }

    $buildDir = Join-Path $RepoRoot "build"
    if (Test-Path $buildDir) {
        Get-ChildItem $buildDir -Directory -Filter "portable_dist_*" -ErrorAction SilentlyContinue |
            ForEach-Object {
                foreach ($root in (Get-PortableDataRoots -DistFolderName $_.Name)) {
                    if ($list -notcontains $root) { $list.Add($root) | Out-Null }
                }
            }
    }

    if ($IncludeDevData) {
        $dev = (Resolve-Path $RepoRoot).Path
        if ($list -notcontains $dev) { $list.Add($dev) | Out-Null }
    }

    return ,$list.ToArray()
}

function Stash-PortableUserData {
    param([string[]]$Sources)
    if (-not $Sources -or $Sources.Count -eq 0) {
        Write-Host '  (no previous portable bundles to migrate)' -ForegroundColor DarkGray
        return
    }
    if (Test-Path $StashRoot) {
        Remove-Item -LiteralPath $StashRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Path $StashRoot -Force | Out-Null
    foreach ($src in $Sources) {
        Write-Host "  from: $src" -ForegroundColor DarkGray
        Merge-PortableUserData -SourceRoot $src -DestRoot $StashRoot
    }
}

function Restore-PortableUserData {
    param([string]$DestInternal)
    if (-not (Test-Path $StashRoot)) { return }
    Write-Host "  to: $DestInternal" -ForegroundColor DarkGray
    Merge-PortableUserData -SourceRoot $StashRoot -DestRoot $DestInternal
}

if (-not (Test-Path $Py)) { throw ".venv not found: $Py" }
if (-not (Test-Path $PyInstaller)) { throw "pyinstaller not found: $PyInstaller" }
if (-not (Test-Path $Spec)) { throw "spec not found: $Spec" }

if (-not $SkipMigrate) {
    Write-Host '== Pre: stash portable data (db, storage, logs, models) ==' -ForegroundColor Cyan
    $sources = Collect-PortableMigrateSources
    Stash-PortableUserData -Sources $sources
    if (Test-Path $StashRoot) {
        $db = Join-Path $StashRoot "aoi.db"
        $stor = Join-Path $StashRoot "storage"
        $parts = @()
        if (Test-Path $db) { $parts += "aoi.db" }
        if (Test-Path $stor) {
            $mb = [math]::Round(((Get-ChildItem $stor -Recurse -File -ErrorAction SilentlyContinue |
                Measure-Object -Property Length -Sum).Sum / 1MB), 1)
            $parts += "storage ~${mb} MB"
        }
        if (Test-Path (Join-Path $StashRoot "models")) { $parts += "models" }
        if (Test-Path (Join-Path $StashRoot "logs")) { $parts += "logs" }
        if (Test-Path (Join-Path $StashRoot ".env")) { $parts += ".env" }
        if ($parts.Count -gt 0) {
            Write-Host "  stashed: $($parts -join ', ')" -ForegroundColor Green
        }
    }
}

Write-Host "== Pre: compile check (aoi_https_frozen) ==" -ForegroundColor Cyan
& $Py -m py_compile (Join-Path $RepoRoot "scripts\aoi_https_frozen.py")

Write-Host "== Build PyInstaller ==" -ForegroundColor Cyan
Push-Location (Join-Path $RepoRoot "build")
try {
    & $PyInstaller --noconfirm --workpath $WorkPath --distpath $DistName "AOI-Web-Portable-HTTPS.spec"
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller exit $LASTEXITCODE" }
} finally {
    Pop-Location
}

$Bundle = Join-Path $RepoRoot "build\$DistName\AOI-Web-Portable-HTTPS"
$Internal = Join-Path $Bundle "_internal"
$Exe = Join-Path $Bundle "AOI-Web-Portable-HTTPS.exe"
if (-not (Test-Path $Exe)) { throw "exe missing: $Exe" }

$LauncherSrc = Join-Path $RepoRoot "build\launch_portable_https.bat"
$LauncherDst = Join-Path $Bundle "launch_portable_https.bat"
if (Test-Path $LauncherSrc) { Copy-Item -Force $LauncherSrc $LauncherDst }

if (-not $SkipMigrate) {
    Write-Host '== Post: restore data into new bundle ==' -ForegroundColor Cyan
    Restore-PortableUserData -DestInternal $Internal
}

Write-Host "== Post: smoke exe (45s max) ==" -ForegroundColor Cyan
$out = Join-Path $env:TEMP "aoi_portable_smoke_out.txt"
$err = Join-Path $env:TEMP "aoi_portable_smoke_err.txt"
Remove-Item $out, $err -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $Exe -WorkingDirectory $Bundle -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -NoNewWindow
$deadline = (Get-Date).AddSeconds(45)
$ok = $false
while ((Get-Date) -lt $deadline) {
    $blob = ""
    if (Test-Path $err) { $blob += Get-Content $err -Raw -ErrorAction SilentlyContinue }
    if (Test-Path $out) { $blob += Get-Content $out -Raw -ErrorAction SilentlyContinue }
    if ($blob -match "Application startup complete") { $ok = $true; break }
    if ($blob -match "Could not import module|Traceback|Error loading ASGI") { break }
    if ($p.HasExited) { Start-Sleep -Seconds 1; break }
    Start-Sleep -Milliseconds 400
}
if (-not $p.HasExited) {
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
}

Write-Host "--- stderr (tail) ---"
if (Test-Path $err) { Get-Content $err -Tail 40 }
Write-Host "--- stdout (tail) ---"
if (Test-Path $out) { Get-Content $out -Tail 20 }

if (-not $ok) {
    throw "Smoke test did not see 'Application startup complete'. See logs above."
}
Write-Host "OK: portable bundle is under $Bundle" -ForegroundColor Green
if (-not $SkipMigrate -and (Test-Path $StashRoot)) {
    Write-Host "Data restored from portable_dist_* (stash: $StashRoot)" -ForegroundColor Green
}
