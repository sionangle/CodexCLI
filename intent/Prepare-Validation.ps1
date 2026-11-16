[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $repo '.intent-work\terminal-tools'
& python.exe -c "import sys; assert sys.platform == 'win32' and sys.version_info[:2] == (3, 13), 'Terminal acceptance requires Windows x64 and Python 3.13'; assert sys.maxsize > 2**32"
if ($LASTEXITCODE -ne 0) { throw 'Unsupported terminal acceptance Python runtime.' }
& python.exe -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: --upgrade --target $destination -r (Join-Path $PSScriptRoot 'requirements-terminal.txt')
if ($LASTEXITCODE -ne 0) { throw 'Pinned terminal acceptance dependencies could not be installed.' }
Write-Host "Terminal acceptance dependencies: $destination"
