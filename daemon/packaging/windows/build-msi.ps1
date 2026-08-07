<#
.SYNOPSIS
    Build the Argus Windows EXE (PyInstaller) and a per-machine MSI (WiX).

.DESCRIPTION
    One-shot local build:
      1. Ensure assets\argus.ico exists (regenerate from the mascot if Pillow is available).
      2. Build dist\argus-daemon\ (onedir: argus-daemon.exe + _internal\) from
         daemon\argus-daemon.spec (icon + version metadata).
      3. Ensure the WiX dotnet tool + UI extension are installed.
      4. Build dist\Argus-Setup.msi.
      5. (Optional) sign the exe + msi if a cert is configured — see -CertFile / $env:ARGUS_SIGN_CERT.

    CI uses Python 3.12. Locally, PySide6 may lack wheels for the newest Python;
    pass -Python "py -3.12" (or a venv python) if the default fails.

.EXAMPLE
    pwsh daemon\packaging\windows\build-msi.ps1

.EXAMPLE
    pwsh daemon\packaging\windows\build-msi.ps1 -Version 1.0.42 -Python "py -3.12"
#>
[CmdletBinding()]
param(
    # MSI ProductVersion. Defaults to daemon\version.py. For CI, pass a
    # monotonically increasing 3-field value (e.g. 1.0.<run_number>) so each
    # build is treated as a clean upgrade.
    [string]$Version,
    # Build number. When set (and -Version is not), the patch field of the
    # version.py base is replaced with it -> major.minor.<build>, giving a
    # monotonically increasing 3-field version (CI passes $GITHUB_RUN_NUMBER).
    [string]$BuildNumber,
    # Python launcher to use for PyInstaller.
    [string]$Python = "python",
    # Skip the PyInstaller step and reuse an existing dist\argus-daemon\ build.
    [switch]$SkipExe,
    # Optional Authenticode .pfx to sign exe + msi (cert-ready; unset = unsigned).
    [string]$CertFile = $env:ARGUS_SIGN_CERT,
    [string]$CertPassword = $env:ARGUS_SIGN_PASS
)

$ErrorActionPreference = "Stop"

# --- Paths ------------------------------------------------------------------
$ScriptDir = $PSScriptRoot
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..\..\..")).Path
$DaemonDir = Join-Path $RepoRoot "daemon"
$Assets    = Join-Path $RepoRoot "assets"
$Ico       = Join-Path $Assets "argus.ico"
$Spec      = Join-Path $DaemonDir "argus-daemon.spec"
$DistDir   = Join-Path $RepoRoot "dist"
# PyInstaller builds Windows as onedir (see argus-daemon.spec) — the exe and
# its _internal\ runtime both land in this folder, which the MSI harvests
# wholesale (see argus.wxs's <Files> wildcard).
$SourceDir = Join-Path $DistDir "argus-daemon"
$Exe       = Join-Path $SourceDir "argus-daemon.exe"
$License   = Join-Path $ScriptDir "license.rtf"
$Msi       = Join-Path $DistDir "Argus-Setup.msi"

# --- Version ----------------------------------------------------------------
# Anchor on __version__ (NOT the first quoted string — the module docstring
# also contains quotes). -Version overrides everything; otherwise read the base
# from version.py and optionally swap in -BuildNumber as the patch field.
if (-not $Version) {
    $verPy = Get-Content (Join-Path $DaemonDir "version.py") -Raw
    if ($verPy -match '__version__\s*=\s*"([^"]+)"') { $base = $Matches[1] }
    else { throw "Could not read __version__ from version.py" }
    if ($BuildNumber) {
        $mm = ($base -split '\.')[0..1] -join '.'
        $Version = "$mm.$BuildNumber"
    } else {
        $Version = $base
    }
}
Write-Host "==> Building Argus $Version" -ForegroundColor Cyan

# --- 1. Icon ----------------------------------------------------------------
if (-not (Test-Path $Ico)) {
    Write-Host "==> assets\argus.ico missing; generating from mascot" -ForegroundColor Yellow
    & $Python (Join-Path $RepoRoot "tools\make_ico.py")
}
if (-not (Test-Path $Ico)) { throw "assets\argus.ico not found and could not be generated." }

# --- 2. EXE (PyInstaller) ---------------------------------------------------
if (-not $SkipExe) {
    Write-Host "==> PyInstaller: building argus-daemon.exe" -ForegroundColor Cyan
    Push-Location $RepoRoot
    try {
        & $Python -m PyInstaller $Spec --noconfirm --distpath $DistDir --workpath (Join-Path $RepoRoot "build")
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
    } finally { Pop-Location }
}
if (-not (Test-Path $Exe)) { throw "$Exe not found (expected PyInstaller onedir output)." }

# --- 3+4. MSI (WiX v5 via the MSBuild SDK project) --------------------------
# We use `dotnet build` on Argus.wixproj rather than the `wix` global tool:
#   - WiX v6/v7 require accepting the OSMF EULA (avoided by pinning v5).
#   - The WiX v5 `wix extension add` global cache is broken on .NET-10-only SDK
#     machines (a NuGet/System.Text.Json load failure).
# The SDK project restores WiX 5 + the UI extension via normal NuGet, which works.
$Proj = Join-Path $ScriptDir "Argus.wixproj"
Write-Host "==> WiX (MSBuild SDK): building Argus-Setup.msi" -ForegroundColor Cyan
# Clean prior outputs first: MSBuild incremental build does NOT treat a changed
# -p:ArgusVersion as a rebuild trigger, so a stale .msi could keep an old
# version. Removing bin/obj guarantees the version is recompiled.
Remove-Item (Join-Path $ScriptDir "bin"), (Join-Path $ScriptDir "obj") -Recurse -Force -ErrorAction SilentlyContinue
# Explicit restore: the WixToolset.Sdk project SDK is resolved from NuGet and
# must be present before MSBuild can evaluate the project (a single implicit
# restore+build can fail the first time on a clean machine).
dotnet restore $Proj
if ($LASTEXITCODE -ne 0) { throw "dotnet restore (WiX) failed ($LASTEXITCODE)" }
dotnet build $Proj -c Release --no-restore `
    "-p:ArgusVersion=$Version" `
    "-p:ArgusSourceDir=$SourceDir" `
    "-p:ArgusIco=$Ico" `
    "-p:ArgusLicense=$License"
if ($LASTEXITCODE -ne 0) { throw "dotnet build (WiX) failed ($LASTEXITCODE)" }

$built = Get-ChildItem (Join-Path $ScriptDir "bin") -Recurse -Filter "Argus-Setup.msi" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $built) { throw "Argus-Setup.msi not found under $ScriptDir\bin" }
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
Copy-Item $built.FullName $Msi -Force

# --- 5. Optional signing (cert-ready) ---------------------------------------
if ($CertFile) {
    if (-not (Test-Cmd "signtool")) {
        Write-Warning "ARGUS_SIGN_CERT set but signtool not found (install the Windows SDK). Skipping signing."
    } else {
        Write-Host "==> Signing exe + msi with $CertFile" -ForegroundColor Cyan
        $ts = "http://timestamp.digicert.com"
        foreach ($f in @($Exe, $Msi)) {
            signtool sign /fd SHA256 /td SHA256 /tr $ts /f $CertFile `
                $(if ($CertPassword) { "/p"; $CertPassword }) $f
            if ($LASTEXITCODE -ne 0) { throw "signtool failed for $f" }
        }
    }
} else {
    Write-Host "==> Unsigned build (no cert configured). Windows will show 'unknown publisher'." -ForegroundColor DarkYellow
}

Write-Host ""
Write-Host "Done:" -ForegroundColor Green
Write-Host "  $Exe"
Write-Host "  $Msi"
