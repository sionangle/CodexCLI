[CmdletBinding()]
param([ValidateSet('dev-small', 'release')][string]$Profile = 'dev-small')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'Build-Environment.ps1')
Initialize-IntentBuildEnvironment -Repository $repo
$cacheLease = Enter-IntentBuildCacheLease
$previousLease = $env:CODEX_INTENT_CACHE_LEASE
try {
    $env:CODEX_INTENT_CACHE_LEASE = $cacheLease.Name
    & python.exe -B -u (Join-Path $PSScriptRoot 'build_package.py') --profile $Profile
    if ($LASTEXITCODE -ne 0) { throw 'Candidate build failed. No installation was switched.' }
} finally {
    $env:CODEX_INTENT_CACHE_LEASE = $previousLease
    $cacheLease.Dispose()
}
