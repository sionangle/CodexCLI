[CmdletBinding()]
param([ValidateSet('dev-small', 'release')][string]$Profile = 'dev-small')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$buildRoot = Join-Path $repo '.intent-work'
$env:CARGO_HOME = Join-Path $buildRoot 'cargo'
$env:RUSTUP_HOME = Join-Path $buildRoot 'rustup'
$env:CARGO_TARGET_DIR = Join-Path $buildRoot 'target'
$env:PATH = (Join-Path $env:CARGO_HOME 'bin') + [IO.Path]::PathSeparator + $env:PATH
$run = Join-Path $buildRoot ('validation-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
New-Item -ItemType Directory -Path $run -Force | Out-Null
# Each validation run gets a separate config, authentication and session directory.
$originalCodexHome = $env:CODEX_HOME
$env:CODEX_HOME = Join-Path $run 'codex-home'
New-Item -ItemType Directory -Path $env:CODEX_HOME -Force | Out-Null
$env:RUST_MIN_STACK = '8388608'
$env:INSTA_UPDATE = 'no'
$env:CARGO_TERM_COLOR = 'never'
# Fixed terminal capabilities keep snapshots independent of the invoking Windows
# Terminal session. Tests that exercise palettes still use their explicit overrides.
$env:FORCE_COLOR = '0'
$env:NO_COLOR = '1'
$env:TERM = 'dumb'
$cargo = Join-Path $env:CARGO_HOME 'bin\cargo.exe'
$log = Join-Path $run 'commands.log'
$report = [ordered]@{ status = 'running'; scope = 'stage-1-automated'; profile = $Profile; started_at = [DateTime]::UtcNow.ToString('o'); checks = @(); error = $null }
$gitArguments = @('-c', ('safe.directory=' + $repo.Replace('\', '/')))
$report['terminal_profile'] = 'FORCE_COLOR=0; NO_COLOR=1; TERM=dumb'
$report['tui_snapshot_version'] = '0.0.0; actual binaries are checked separately'

function Get-SourceState {
    $commit = [string](& git @gitArguments -C $repo rev-parse HEAD)
    if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[a-f0-9]{40}$') {
        throw 'Cannot verify the source commit. Validation cannot authorize a candidate.'
    }
    $changes = @(& git @gitArguments -C $repo status --porcelain)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify whether the source tree is clean.' }
    return @{ commit = $commit; clean = $changes.Count -eq 0 }
}

function Invoke-Check([string]$Name, [string[]]$Arguments, [int]$MinimumPassed = 1) {
    if ($Arguments[0] -eq 'test') {
        $Arguments += @('--profile', $Profile, '--target', 'x86_64-pc-windows-msvc')
        if ($Name -eq 'TUI regression suite') {
            # Embedded-server tests share process state and can time out when the
            # Windows test runner starts one thread per logical processor.
            $Arguments += @('--', '--test-threads=1')
        }
    }
    Add-Content -LiteralPath $log -Value ("`nCHECK: " + $Name)
    Write-Host "CHECK: $Name"
    $logName = $Name -replace '[^a-zA-Z0-9_-]', '-'
    $stdout = Join-Path $run ($logName + '.stdout.log')
    $stderr = Join-Path $run ($logName + '.stderr.log')
    # Avoid PowerShell 5.1 converting ordinary native stderr into terminating errors.
    $process = Start-Process -FilePath $cargo -ArgumentList $Arguments -WorkingDirectory (Join-Path $repo 'codex-rs') -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $code = $process.ExitCode
    $report.checks += [ordered]@{ name = $Name; exit_code = $code; stdout = $stdout; stderr = $stderr }
    if ($code -ne 0) {
        Get-Content -LiteralPath $stdout, $stderr -Tail 60 | Out-Host
        throw "$Name failed with exit code $code. No release was published. Full logs: $run"
    }
    if ($Arguments[0] -eq 'test') {
        $testLog = [string](Get-Content -LiteralPath $stdout -Raw -Encoding UTF8)
        $passed = 0
        foreach ($result in [regex]::Matches($testLog, '(?m)^test result: ok\. (\d+) passed;')) {
            $passed += [int]$result.Groups[1].Value
        }
        $report.checks[-1]['passed_tests'] = $passed
        if ($passed -lt $MinimumPassed) {
            throw "$Name ran $passed passing tests; expected at least $MinimumPassed. Missing tests cannot pass the release gate."
        }
    }
    Write-Host "PASS: $Name (full logs: $run)"
}

Push-Location (Join-Path $repo 'codex-rs')
try {
    if (Test-Path Env:CODEX_SANDBOX_NETWORK_DISABLED) {
        throw 'The inherited CODEX_SANDBOX_NETWORK_DISABLED marker makes upstream tests silently return early. Run validation outside the sandbox with this marker unset; no candidate can be validated in this environment.'
    }
    if (Test-Path Env:INSTA_FORCE_PASS) {
        throw 'INSTA_FORCE_PASS must be unset; snapshot failures must fail validation.'
    }
    $report['conditional_network_tests_enabled'] = $true
    $source = Get-SourceState
    $report['commit'] = $source.commit
    $report['source_clean_at_start'] = $source.clean
    if (-not (Test-Path -LiteralPath $cargo)) { throw 'Rust is missing. Run intent/Prepare-Build.ps1 first.' }
    . (Join-Path $PSScriptRoot 'Build-Environment.ps1')
    Initialize-IntentBuildEnvironment -Repository $repo
    # Upstream fixtures create isolated OS temp directories. Placing those below
    # this Git checkout changes project discovery and invalidates projectless tests.
    $report['temporary_directory'] = [IO.Path]::GetTempPath()
    & python.exe (Join-Path $PSScriptRoot 'lockfile.py')
    if ($LASTEXITCODE -ne 0) { throw 'Upstream dependency lock verification failed.' }
    $report.checks += [ordered]@{ name = 'upstream dependency lock'; exit_code = 0 }
    Invoke-Check 'format' @('fmt', '--all', '--', '--check')
    Invoke-Check 'intent rendering regression tests' @('test', '--locked', '-p', 'codex-tui', '--lib', 'intent_') 12
    Invoke-Check 'V2 activity preview' @('test', '--locked', '-p', 'codex-tui', '--lib', 'agent_status_') 3
    Invoke-Check 'configuration persistence' @('test', '--locked', '-p', 'codex-tui', '--lib', 'config_panel')
    Invoke-Check 'configuration types' @('test', '--locked', '-p', 'codex-config', '--lib')
    $generatedSchema = '../.intent-work/' + (Split-Path $run -Leaf) + '/config.schema.json'
    Invoke-Check 'configuration schema generation' @('run', '--locked', '--profile', $Profile, '--target', 'x86_64-pc-windows-msvc', '-p', 'codex-config-schema', '--bin', 'codex-write-config-schema', '--', '--out', $generatedSchema)
    $generated = [IO.File]::ReadAllText((Join-Path $run 'config.schema.json')).Replace("`r`n", "`n")
    $checkedIn = [IO.File]::ReadAllText((Join-Path $repo 'codex-rs/core/config.schema.json')).Replace("`r`n", "`n")
    if ($generated -cne $checkedIn) { throw 'Generated config schema differs from the checked-in fixture.' }
    $report.checks += [ordered]@{ name = 'configuration schema fixture'; exit_code = 0 }
    Invoke-Check 'configuration integration' @('test', '--locked', '-p', 'codex-core', '--lib', 'config::')
    Invoke-Check 'isolated home resolution' @('test', '--locked', '-p', 'codex-utils-home-dir', '--lib')
    Invoke-Check 'TUI regression suite' @('test', '--locked', '-p', 'codex-tui', '--lib')
    $report.status = 'passed-automated-only'
} catch {
    $report.status = 'failed'
    $report.error = $_.Exception.Message
    throw
} finally {
    $sourceCheckError = $null
    try {
        $source = Get-SourceState
        $report['commit_at_end'] = $source.commit
        $report['source_clean_at_end'] = $source.clean
    } catch {
        $sourceCheckError = $_.Exception.Message
        $report['source_clean_at_end'] = $false
        $report.status = 'failed'
        $report.error = $sourceCheckError
    }
    $report['eligible_for_candidate_validation'] = $report.status -eq 'passed-automated-only' -and
        $report.source_clean_at_start -and $report.source_clean_at_end -and
        $report.commit -eq $report.commit_at_end
    $report.finished_at = [DateTime]::UtcNow.ToString('o')
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $run 'report.json') -Encoding UTF8
    $env:CODEX_HOME = $originalCodexHome
    Pop-Location
    Write-Host "Validation report: $run"
    if ($sourceCheckError) { throw $sourceCheckError }
}
