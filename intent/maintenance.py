"""codex-intent maintenance entry. Invoke through Maintain-Intent.ps1 for launch exclusion."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True  # Installed release directories remain immutable.
from managed_runtime import checked_root, initialize, package_path, quiesce, read, stamp, transition, write
from upgrade import adapt, check_stable, git, replay, require_source


def validate(folder, previous_release, profile):
    folder = Path(folder).resolve(strict=True)
    plan = read(folder / 'report.json')
    review = read(folder / 'equivalence-review.json')
    if (review['target_commit'] != plan['target_commit'] or review['decision'] not in ['retain', 'replace-with-upstream']
            or not review.get('reason', '').strip()):
        raise RuntimeError('Review the listed upstream changes and record an equivalence decision before validation.')
    if review['decision'] == 'replace-with-upstream' and not review.get('equivalence_evidence'):
        raise RuntimeError('Patch replacement requires concrete upstream source and behavior evidence; all feature tests remain mandatory.')
    repo, baseline = require_source(plan['worktree'])
    if baseline['commit'] != plan['target_commit'] or baseline['tag'] != plan['tag']:
        raise RuntimeError('Candidate does not record the selected target upstream.')
    if len(plan['applied']) != len(plan['patches']):
        raise RuntimeError('Not all custom commits were replayed.')
    git(repo, 'diff', '--exit-code', baseline['commit'], '--', 'codex-rs/rust-toolchain.toml')
    for item in plan['applied']:
        git(repo, 'merge-base', '--is-ancestor', item['replayed'], 'HEAD')
    commit = git(repo, 'rev-parse', 'HEAD').stdout.strip()
    run = folder / ('validation-' + stamp())
    run.mkdir()
    report = {'status': 'running', 'commit': commit, 'review': review, 'checks': []}
    environment = dict(os.environ, PYTHONUTF8='1')
    if 'CODEX_SANDBOX_NETWORK_DISABLED' in environment or 'INSTA_FORCE_PASS' in environment:
        raise RuntimeError('Run validation outside the sandbox with test-skipping environment markers unset.')
    environment['PYTHONPATH'] = str(repo / '.intent-work/terminal-tools')

    def command(name, args, marker=None):
        print('CHECK: ' + name, flush=True)
        log = run / (name + '.log')
        with log.open('w', encoding='utf-8') as output:
            result = subprocess.run(list(map(str, args)), cwd=repo, env=environment,
                                    stdout=output, stderr=subprocess.STDOUT)
        report['checks'].append({'name': name, 'exit_code': result.returncode, 'log': str(log)})
        if result.returncode:
            raise RuntimeError(name + ' failed; worktree and evidence retained: ' + str(run))
        if marker:
            matches = re.findall(re.escape(marker) + r'(.+)', log.read_text(encoding='utf-8', errors='replace'))
            if len(matches) != 1:
                raise RuntimeError(name + ' did not emit exactly one evidence location.')
            path = Path(matches[0].strip()).resolve()
            if path.name == 'report.json':
                path = path.parent
            if not path.is_relative_to(repo / '.intent-work'):
                raise RuntimeError('Validation evidence leaves the isolated work area.')
            return path

    def ps(name, *args, marker=None):
        return command(name, ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                              repo / 'intent' / (name + '.ps1'), *args], marker)
    try:
        ps('Prepare-Build')
        ps('Prepare-Validation')
        build = ps('Build-Intent', '-Profile', profile, marker='Build report: ')
        tests = ps('Test-Intent', '-Profile', profile, marker='Validation report: ')
        package = build / 'package'
        smoke = command('binary-smoke', [sys.executable, repo / 'intent/smoke_package.py', package], 'Binary smoke report: ')
        terminal = command('terminal', [sys.executable, repo / 'intent/validate_terminal.py', package, '--stage', '2'], 'Terminal acceptance report: ')
        from release import assemble
        provisional = assemble(repo, build, tests, smoke, terminal, run / 'validated-ui-candidate', stage=2,
                               maintenance_candidate=True)
        maintenance = command('maintenance', [sys.executable, repo / 'intent/maintenance_probe.py',
                                              '--candidate', provisional, '--previous', previous_release],
                              'Maintenance acceptance report: ')
        destination = run / 'release'
        assemble(repo, build, tests, smoke, terminal, destination, stage=3, maintenance=maintenance)
        report.update(status='passed-not-switched', release=str(destination), version=read(destination / 'release.json')['version'])
        print('Validated release (current installation unchanged): ' + str(destination), flush=True)
        return report
    except Exception as error:
        report.update(status='failed-no-publication', error=str(error))
        raise
    finally:
        write(run / 'report.json', report)


def install_launchers(root):
    source = Path(__file__).resolve().parent
    shutil.copy2(source / 'Run-CodexIntent.ps1', root / 'Run-CodexIntent.ps1')
    (root / 'codex-intent.cmd').write_text('@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-CodexIntent.ps1" %*\r\n', encoding='ascii')
    (root / 'Maintain-Intent.ps1').write_text('''param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
$ErrorActionPreference = 'Stop'
$state = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($state.format -ne 1 -or -not $state.manager) { throw 'Use the maintenance tool supplied with the verified custom release.' }
$manager = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $state.manager))
if (-not $manager.StartsWith($PSScriptRoot.TrimEnd('\\') + '\\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Manager path leaves the installation.' }
& (Join-Path $manager 'Maintain-Intent.ps1') @Arguments -InstallRoot $PSScriptRoot
''', encoding='utf-8-sig')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('action', choices=['status', 'check', 'adapt', 'continue', 'validate', 'install', 'switch', 'rollback', 'quiesce'])
    parser.add_argument('--install-root', type=Path, required=True)
    parser.add_argument('--repository', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--tag')
    parser.add_argument('--adaptation', type=Path)
    parser.add_argument('--release', type=Path)
    parser.add_argument('--previous-release', type=Path)
    parser.add_argument('--profile', choices=['dev-small', 'release'], default='dev-small')
    args = parser.parse_args()
    result = None
    if args.action == 'status':
        result = {}
        if (args.repository / 'intent/baseline.json').is_file():
            result['source_baseline'] = read(args.repository / 'intent/baseline.json')
            if (args.repository / '.git').exists():
                result['source_commit'] = git(args.repository, 'rev-parse', 'HEAD').stdout.strip()
        if (args.install_root / 'state.json').is_file():
            root = checked_root(args.install_root)
            state = read(root / 'state.json')
            result['installation'] = state
            release = read(root / state['current']['release'] / 'release.json')
            result['current_version'] = release['version']
            result['upstream'] = release['upstream']
            result['configuration'] = str(root / state['current']['home'] / 'config.toml')
        else:
            result['installation'] = 'not-installed'
    elif args.action == 'check':
        result = check_stable(args.repository)
    elif args.action == 'adapt':
        if not args.tag:
            parser.error('adapt requires --tag rust-vX.Y.Z')
        result = adapt(args.repository, args.tag)
    elif args.action == 'continue':
        if not args.adaptation:
            parser.error('continue requires --adaptation <retained report directory>')
        result = replay(args.adaptation, resume=True)
    elif args.action == 'validate':
        if not args.adaptation or not args.previous_release:
            parser.error('validate requires --adaptation and --previous-release for actual rollback acceptance')
        result = validate(args.adaptation, args.previous_release, args.profile)
    elif args.action == 'install':
        if not args.release:
            parser.error('install requires --release <verified portable release>')
        root = checked_root(args.install_root)
        result = initialize(root, args.release)
        install_launchers(root)
    elif args.action == 'switch':
        if not args.release:
            parser.error('switch requires --release <verified portable release>')
        result = transition(args.install_root, args.release)
    elif args.action == 'rollback':
        result = transition(args.install_root, rollback=True)
    else:
        root = checked_root(args.install_root)
        result = {}
        quiesce(root, read(root / 'state.json')['current'], result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
