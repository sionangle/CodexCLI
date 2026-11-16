[CmdletBinding()]
param([ValidateSet('dev-small', 'release')][string]$Profile = 'dev-small')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'Build-Environment.ps1')
Initialize-IntentBuildEnvironment -Repository $repo
& python.exe -u (Join-Path $PSScriptRoot 'build_package.py') --profile $Profile
if ($LASTEXITCODE -ne 0) { throw 'Candidate build failed. No installation was switched.' }
