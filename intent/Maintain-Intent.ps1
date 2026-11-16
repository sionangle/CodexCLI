[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('status', 'check', 'adapt', 'continue', 'validate', 'install', 'switch', 'rollback', 'quiesce')]
    [string]$Action,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'codex-intent'),
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$profileRoot = [Environment]::GetFolderPath('UserProfile')
$official = [IO.Path]::GetFullPath((Join-Path $profileRoot '.codex')).TrimEnd('\')
if ($root -ieq $official -or $root.StartsWith($official + '\', [StringComparison]::OrdinalIgnoreCase) -or $root -ieq $profileRoot -or $root.Length -lt 4) {
    throw 'Choose a dedicated installation directory outside official Codex data.'
}
$ancestor = $root
while ($ancestor) {
    if (Test-Path -LiteralPath $ancestor) {
        if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Managed installation paths cannot pass through directory links.' }
    }
    $ancestor = Split-Path -Parent $ancestor
}
$exclusive = $Action -in @('install', 'switch', 'rollback', 'quiesce')
$lease = $null
try {
    if ($exclusive) {
        if ($Action -eq 'install' -and -not (Test-Path -LiteralPath $root)) {
            New-Item -ItemType Directory -Path $root | Out-Null
            # Private installation also contains authentication and consistent data backups.
            $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            & icacls.exe $root /inheritance:r /grant:r ('*' + $sid + ':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Cannot establish private installation permissions.' }
            [IO.File]::WriteAllText((Join-Path $root '.intent-install'), 'codex-intent-managed-install-v1')
            [IO.File]::WriteAllText((Join-Path $root 'runtime.lock'), '')
        }
        if ((Get-Content -LiteralPath (Join-Path $root '.intent-install') -Raw).Trim() -ne 'codex-intent-managed-install-v1') {
            throw 'Not a managed codex-intent installation. Existing files were not changed.'
        }
        if ((Get-Item -LiteralPath $root -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Install root cannot be a link.' }
        try {
            $lease = [IO.File]::Open((Join-Path $root 'runtime.lock'), [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch { throw 'A managed Codex client or another maintenance operation is active. No version was switched.' }
    }
    & python.exe -B -u (Join-Path $PSScriptRoot 'maintenance.py') $Action --install-root $root @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Maintenance did not complete. Inspect the retained report and run status before retrying.' }
} finally {
    if ($null -ne $lease) { $lease.Dispose() }
}
