# Dot-source from build scripts. Changes apply only to the calling process.
function Initialize-IntentBuildEnvironment {
    [CmdletBinding()]
    param([string]$Repository, [string]$VisualStudioPath)
    $ErrorActionPreference = 'Stop'
    $buildRoot = Join-Path $Repository '.intent-work'
    New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
    # Linked upgrade worktrees reuse only downloaded/compiler caches, never configuration,
    # evidence or data. rustup still selects this worktree's pinned toolchain; Cargo uses its lock.
    $cacheRoot = $buildRoot
    if (Test-Path -LiteralPath (Join-Path $Repository '.git') -PathType Leaf) {
        $commonGit = [string](& git -c ('safe.directory=' + $Repository.Replace('\', '/')) -C $Repository rev-parse --path-format=absolute --git-common-dir)
        if ($LASTEXITCODE -ne 0 -or (Split-Path -Leaf $commonGit) -ne '.git') { throw 'Cannot establish the owning repository cache for this worktree.' }
        $cacheRoot = Join-Path (Split-Path -Parent $commonGit) '.intent-work'
        New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null
    }
    # Cargo uses libgit2 for checkout and still hits MAX_PATH in libc++ even when
    # git.exe supports long paths. A DOS drive alias keeps all data in this project.
    $driveFile = Join-Path $cacheRoot 'build-drive.txt'
    $drive = if (Test-Path -LiteralPath $driveFile) { (Get-Content -LiteralPath $driveFile -Raw).Trim() } else { $null }
    $mappings = @{}
    foreach ($line in (& subst.exe)) {
        if ($line -match '^([A-Z]:)\\: => (.+)$') { $mappings[$Matches[1]] = $Matches[2] }
    }
    if ($drive -and $drive -notmatch '^[D-Z]:$') { throw 'Invalid project build drive record.' }
    $logicalDrives = [IO.Directory]::GetLogicalDrives()
    if ($drive -and $logicalDrives -contains ($drive + '\')) {
        if (-not $mappings.ContainsKey($drive) -or
            [IO.Path]::GetFullPath($mappings[$drive]).TrimEnd('\') -ine [IO.Path]::GetFullPath($cacheRoot).TrimEnd('\')) {
            throw "Build drive $drive is occupied by another location. No mapping was changed."
        }
    } else {
        if (-not $drive) {
            $drive = 89..68 | ForEach-Object { [string][char]$_ + ':' } | Where-Object { $logicalDrives -notcontains ($_ + '\') } | Select-Object -First 1
        }
        if (-not $drive) { throw 'No free drive letter is available for the isolated Cargo cache.' }
        & subst.exe $drive $cacheRoot
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the project build drive alias.' }
        [IO.File]::WriteAllText($driveFile, $drive)
    }
    $env:CARGO_HOME = Join-Path ($drive + '\') 'cargo'
    $env:RUSTUP_HOME = Join-Path $cacheRoot 'rustup'
    # An inherited override would take priority over the worktree's pinned toolchain.
    Remove-Item Env:RUSTUP_TOOLCHAIN -ErrorAction SilentlyContinue
    $env:CARGO_TARGET_DIR = Join-Path ($drive + '\') 'target'
    if (-not $env:CARGO_BUILD_JOBS) { $env:CARGO_BUILD_JOBS = '8' }
    $env:PATH = (Join-Path $env:CARGO_HOME 'bin') + [IO.Path]::PathSeparator + $env:PATH
    $assetsPath = Join-Path $buildRoot 'assets.json'
    if (Test-Path -LiteralPath $assetsPath) {
        $assets = Get-Content -LiteralPath $assetsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($entry in $assets.environment.PSObject.Properties) {
            [Environment]::SetEnvironmentVariable($entry.Name, $entry.Value, 'Process')
        }
    }
    if (-not $VisualStudioPath) {
        $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
        if (Test-Path -LiteralPath $vswhere) {
            $VisualStudioPath = & $vswhere -latest -prerelease -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        }
    }
    if (-not $VisualStudioPath) {
        $candidate = 'C:\Program Files\Microsoft Visual Studio\18\Insiders'
        if (Test-Path -LiteralPath (Join-Path $candidate 'VC\Tools\MSVC')) { $VisualStudioPath = $candidate }
    }
    if (-not $VisualStudioPath) { throw 'MSVC and Windows SDK are required. Install the C++ desktop workload, or supply -VisualStudioPath.' }
    $devShell = Join-Path $VisualStudioPath 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll'
    Import-Module -Name $devShell
    Enter-VsDevShell -VsInstallPath $VisualStudioPath -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) { throw 'MSVC environment initialization failed.' }
}

function Enter-IntentBuildCacheLease {
    $lockPath = Join-Path (Split-Path -Parent $env:RUSTUP_HOME) 'build-test.lock'
    $deadline = [DateTime]::UtcNow.AddMinutes(30)
    $announced = $false
    while ($true) {
        try {
            return [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch [IO.IOException] {
            if ([DateTime]::UtcNow -gt $deadline) { throw 'Another build or validation still owns the project compiler cache. No files were cleaned.' }
            if (-not $announced) { Write-Host 'Waiting for the other project build or validation to finish...'; $announced = $true }
            Start-Sleep -Seconds 2
        }
    }
}
