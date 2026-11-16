[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$InstallRoot,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
# Windows PowerShell -File evaluates parameter defaults before setting PSScriptRoot.
if (-not $InstallRoot) { $InstallRoot = $PSScriptRoot }
$root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
if ((Get-Content -LiteralPath (Join-Path $root '.intent-install') -Raw).Trim() -ne 'codex-intent-managed-install-v1') {
    throw 'This launcher requires a managed codex-intent installation.'
}
# A shared read handle lives for the whole foreground CLI lifetime. Maintenance needs an
# exclusive read/write handle and cannot publish while any managed launcher is open.
try {
    $lease = [IO.File]::Open((Join-Path $root 'runtime.lock'), [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
} catch { throw 'codex-intent maintenance is in progress. Retry after it finishes.' }
$originalCodexHome = $env:CODEX_HOME
try {
    $state = Get-Content -LiteralPath (Join-Path $root 'state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($state.format -ne 1) { throw 'Unrecognized installation state format.' }
    $data = [IO.Path]::GetFullPath((Join-Path $root $state.current.home))
    $release = [IO.Path]::GetFullPath((Join-Path $root $state.current.release))
    foreach ($path in @($data, $release)) {
        if (-not $path.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Installation path leaves its managed root.' }
        $item = Get-Item -LiteralPath $path -Force
        while ($item.FullName -ine $root) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Managed paths cannot redirect through links.' }
            $item = $item.Parent
        }
    }
    $env:CODEX_HOME = $data
    & (Join-Path $release 'package\bin\codex-intent.exe') @Arguments
    $result = $LASTEXITCODE
} finally {
    $env:CODEX_HOME = $originalCodexHome
    $lease.Dispose()
}
exit $result
