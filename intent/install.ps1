[CmdletBinding()]
param([string]$InstallRoot, [string]$BinDirectory, [switch]$NoPath)
$ErrorActionPreference = 'Stop'
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    throw 'This installer currently supports Windows x64.'
}
$base = if ($env:CODEX_INTENT_RELEASE_BASE_URL) { $env:CODEX_INTENT_RELEASE_BASE_URL } else { 'https://github.com/sionangle/CodexCLI/releases/latest/download' }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('codex-intent-install-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $archive = Join-Path $temporary 'package.zip'
    Write-Host 'Downloading codex-intent...'
    Invoke-WebRequest -UseBasicParsing -Uri ($base + '/codex-intent-windows-x64.zip') -OutFile $archive
    $checksumResponse = Invoke-WebRequest -UseBasicParsing -Uri ($base + '/codex-intent-windows-x64.zip.sha256')
    $expected = if ($checksumResponse.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($checksumResponse.Content) } else { [string]$checksumResponse.Content }
    $expected = $expected.Trim()
    if ($expected -notmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ine $expected) {
        throw 'The downloaded archive differs from the published SHA256. Installation stopped.'
    }
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $temporary 'unpacked')
    $release = Join-Path $temporary 'unpacked\codex-intent'
    $report = Join-Path $temporary 'result.json'
    $parameters = @('--report', $report)
    if ($InstallRoot) { $parameters += @('--install-root', $InstallRoot) }
    if ($BinDirectory) { $parameters += @('--bin-dir', $BinDirectory) }
    if ($NoPath) { $parameters += '--no-path' }
    & (Join-Path $release 'runtime\python.exe') -E -s -B -X utf8 (Join-Path $release 'intent\install.py') @parameters
    if ($LASTEXITCODE -ne 0) { throw 'Installation did not complete; the previous selected version was retained.' }
    $installed = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $NoPath -and @($env:PATH -split ';') -notcontains $installed.bin) { $env:PATH = $installed.bin + ';' + $env:PATH }
} finally {
    # Only remove this invocation's verified temporary child, using PowerShell end-to-end.
    $resolved = [IO.Path]::GetFullPath($temporary)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolved) -match '^codex-intent-install-[0-9a-f]{8}$') {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
