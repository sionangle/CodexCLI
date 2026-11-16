# Run from a normal PowerShell session with network access.
[CmdletBinding()]
param([string]$VisualStudioPath)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$buildRoot = Join-Path $repo '.intent-work'
. (Join-Path $PSScriptRoot 'Build-Environment.ps1')
Initialize-IntentBuildEnvironment -Repository $repo -VisualStudioPath $VisualStudioPath

$rustup = Join-Path $env:CARGO_HOME 'bin\rustup.exe'
if (-not (Test-Path -LiteralPath $rustup)) {
    $installer = Join-Path $buildRoot 'rustup-init.exe'
    $url = 'https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe'
    # Windows PowerShell can expose binary HTTP responses as byte arrays. Read the
    # checksum from a file so parsing always receives text, independent of Content-Type.
    $checksumPath = Join-Path $buildRoot 'rustup-init.exe.sha256'
    Invoke-WebRequest -Uri ($url + '.sha256') -OutFile $checksumPath -UseBasicParsing
    $checksumText = [string](Get-Content -LiteralPath $checksumPath -Raw -Encoding ASCII)
    $expected = ($checksumText.Trim() -split '\s+')[0]
    if ($expected -notmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid rustup SHA256 response.' }
    if (-not (Test-Path -LiteralPath $installer) -or
        (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ine $expected) {
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
    }
    $actual = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
    if ($expected -notmatch '^[a-fA-F0-9]{64}$' -or $actual -ine $expected) { throw 'rustup installer SHA256 mismatch.' }
    & $installer -y --no-modify-path --default-toolchain none
    if ($LASTEXITCODE -ne 0) { throw "rustup installation failed: $LASTEXITCODE" }
}
Push-Location (Join-Path $repo 'codex-rs')
try {
    & python.exe (Join-Path $PSScriptRoot 'lockfile.py')
    if ($LASTEXITCODE -ne 0) { throw 'Upstream dependency lock verification failed.' }
    # rustup reads this worktree's rust-toolchain.toml, including its components.
    & $rustup toolchain install --no-self-update
    if ($LASTEXITCODE -ne 0) { throw 'The pinned toolchain could not be installed.' }
    & (Join-Path $env:CARGO_HOME 'bin\cargo.exe') fetch --locked --target x86_64-pc-windows-msvc
    if ($LASTEXITCODE -ne 0) { throw 'Dependency fetch failed. The lockfile has not been relaxed.' }
} finally { Pop-Location }
& python.exe (Join-Path $PSScriptRoot 'prepare_assets.py')
if ($LASTEXITCODE -ne 0) { throw 'Pinned binary dependency preparation failed.' }
Write-Host 'Build prerequisites are ready. This script has not built or validated a release.'
