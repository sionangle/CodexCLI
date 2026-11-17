"""Linux preparation/build/test entry; all tools, data and evidence stay project-scoped."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.request import urlopen

from platform_support import WINDOWS, assets_manifest, cache_root, digest_file, native_target, tomllib
from managed_runtime import read, stamp, write
from upgrade import git

REPO = Path(__file__).resolve().parent.parent
WORK = REPO / '.intent-work'


def environment():
    if WINDOWS or sys.platform != 'linux':
        raise RuntimeError('Use Prepare-Build.ps1 / Build-Intent.ps1 / Test-Intent.ps1 on Windows.')
    native_target()
    common = Path(git(REPO, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout.strip()).resolve(strict=True)
    cache = cache_root(common.parent)
    WORK.mkdir(exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CARGO_HOME=str(cache / 'cargo'), RUSTUP_HOME=str(cache / 'rustup'),
               CARGO_TARGET_DIR=str(cache / 'target'))
    env.pop('RUSTUP_TOOLCHAIN', None)
    env['PATH'] = str(cache / 'cargo/bin') + os.pathsep + env['PATH']
    env.setdefault('CARGO_BUILD_JOBS', '4')
    env['PYTHONPATH'] = str(WORK / 'terminal-tools') + os.pathsep + env.get('PYTHONPATH', '')
    if assets_manifest(REPO).is_file():
        env.update(read(assets_manifest(REPO))['environment'])
    return cache, env


@contextmanager
def cache_lease(cache, env):
    path = cache / 'build-test.lock'
    with path.open('a+b') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Waiting for the project build or validation cache...', flush=True)
            fcntl.flock(stream, fcntl.LOCK_EX)
        env['CODEX_INTENT_CACHE_LEASE'] = str(path)
        yield


def run(command, env, **kwargs):
    subprocess.run(list(map(str, command)), cwd=REPO / 'codex-rs', env=env, check=True, **kwargs)


def prepare(cache, env):
    rustup = cache / 'cargo/bin/rustup'
    if not rustup.is_file():
        url = 'https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init'
        with urlopen(url + '.sha256', timeout=60) as response:
            expected = response.read().decode('ascii').split()[0]
        if not re.fullmatch('[0-9a-fA-F]{64}', expected):
            raise RuntimeError('Invalid official rustup checksum.')
        installer = cache / 'rustup-init'
        if not installer.exists() or digest_file(installer) != expected.lower():
            with urlopen(url, timeout=120) as response, installer.open('wb') as output:
                import shutil
                shutil.copyfileobj(response, output)
        if digest_file(installer) != expected.lower():
            raise RuntimeError('Official rustup checksum mismatch.')
        installer.chmod(0o700)
        run([installer, '-y', '--no-modify-path', '--default-toolchain', 'none'], env)
    run([sys.executable, REPO / 'intent/lockfile.py'], env)
    toolchain = tomllib.loads((REPO / 'codex-rs/rust-toolchain.toml').read_text(encoding='utf-8'))['toolchain']['channel']
    run([rustup, 'toolchain', 'install', toolchain, '--no-self-update'], env)
    run(['cargo', 'fetch', '--locked', '--target', native_target()], env)
    run([sys.executable, REPO / 'intent/prepare_assets.py'], env)
    print('Pinned Linux build prerequisites ready; no release has been validated.', flush=True)


def source_state():
    return git(REPO, 'rev-parse', 'HEAD').stdout.strip(), not bool(git(REPO, 'status', '--porcelain').stdout)


def test(env, profile):
    if 'CODEX_SANDBOX_NETWORK_DISABLED' in env or 'INSTA_FORCE_PASS' in env:
        raise RuntimeError('Test-skipping environment markers must be unset outside the sandbox.')
    folder = WORK / ('validation-' + stamp())
    folder.mkdir()
    commit, clean = source_state()
    home = folder / 'codex-home'
    home.mkdir()
    env = dict(env, CODEX_HOME=str(home), RUST_MIN_STACK='8388608', INSTA_UPDATE='no',
               CARGO_TERM_COLOR='never', FORCE_COLOR='0', NO_COLOR='1', TERM='dumb')
    report = dict(status='running', scope='stage-3-automated', profile=profile, target=native_target(),
                  commit=commit, source_clean_at_start=clean, conditional_network_tests_enabled=True,
                  temporary_directory=tempfile.gettempdir(), checks=[],
                  platform={'os_release': Path('/etc/os-release').read_text(encoding='utf-8'),
                            'kernel': os.uname().release, 'machine': os.uname().machine, 'uid': os.getuid()})

    def check(name, args, minimum=None):
        print('CHECK: ' + name, flush=True)
        output = folder / (re.sub('[^a-zA-Z0-9_-]', '-', name) + '.log')
        with output.open('w', encoding='utf-8') as log:
            result = subprocess.run(list(map(str, args)), cwd=REPO / 'codex-rs', env=env,
                                    stdout=log, stderr=subprocess.STDOUT)
        item = dict(name=name, exit_code=result.returncode, log=str(output))
        report['checks'].append(item)
        if result.returncode:
            raise RuntimeError(name + ' failed; evidence: ' + str(output))
        if minimum is not None:
            item['passed_tests'] = sum(map(int, re.findall(r'(?m)^test result: ok\. (\d+) passed;', output.read_text(encoding='utf-8'))))
            if item['passed_tests'] < minimum:
                raise RuntimeError(name + ' did not execute the required tests.')
        if name in ['maintenance fault injection', 'Linux maintenance primitives']:
            text = output.read_text(encoding='utf-8')
            count = re.search(r'(?m)^Ran (\d+) tests? in ', text)
            if not count or int(count[1]) < 4 or not re.search(r'(?m)^OK$', text):
                raise RuntimeError('Maintenance fault-injection tests did not execute successfully.')
        print('PASS: ' + name, flush=True)

    def cargo_test(name, package, target_args, filter_name=None, minimum=1, single=False):
        args = ['cargo', 'test', '--locked', '--profile', profile, '--target', native_target(), '-p', package, *target_args]
        if filter_name:
            args.append(filter_name)
        if single:
            args += ['--', '--test-threads=1']
        check(name, args, minimum)

    try:
        check('upstream dependency lock', [sys.executable, REPO / 'intent/lockfile.py'])
        check('workspace cache provenance', [sys.executable, REPO / 'intent/cache_state.py', '--profile', profile, '--log', folder / 'workspace-cache.log'])
        check('format', ['cargo', 'fmt', '--all', '--', '--check'])
        cargo_test('intent rendering regression tests', 'codex-tui', ['--lib'], 'intent_', 37)
        cargo_test('V2 task state aggregation', 'codex-tui', ['--lib'], 'intent_tasks::tests::', 18)
        cargo_test('V2 persistent panel integration', 'codex-tui', ['--lib'], 'intent_task_panel_tests::', 2)
        cargo_test('V2 encrypted description boundary', 'codex-core', ['--lib'], 'intent_task_description_never_exposes_encrypted_messages')
        cargo_test('V2 protocol and schema contracts', 'codex-app-server-protocol', ['--lib'], minimum=323)
        cargo_test('maintenance idle state boundary', 'codex-app-server-daemon', ['--lib'], 'intent_maintenance')
        cargo_test('maintenance non-forcing drain', 'codex-app-server', ['--lib'], 'intent_daemon_drain')
        cargo_test('maintenance CLI command', 'codex-cli', ['--bin', 'codex'], 'intent_quiesce_subcommand_parses')
        check('maintenance fault injection', [sys.executable, '-W', 'error::ResourceWarning', REPO / 'intent/test_managed_runtime.py'])
        check('Linux maintenance primitives', [sys.executable, '-W', 'error::ResourceWarning', REPO / 'intent/test_linux_maintenance.py'])
        cargo_test('V2 activity preview', 'codex-tui', ['--lib'], 'agent_status_', 3)
        cargo_test('configuration persistence', 'codex-tui', ['--lib'], 'config_panel')
        cargo_test('configuration types', 'codex-config', ['--lib'])
        generated = folder / 'config.schema.json'
        check('configuration schema generation', ['cargo', 'run', '--locked', '--profile', profile, '--target', native_target(), '-p', 'codex-config-schema', '--bin', 'codex-write-config-schema', '--', '--out', generated])
        if generated.read_text(encoding='utf-8') != (REPO / 'codex-rs/core/config.schema.json').read_text(encoding='utf-8'):
            raise RuntimeError('Configuration schema differs from the checked-in fixture.')
        report['checks'].append(dict(name='configuration schema fixture', exit_code=0))
        cargo_test('configuration integration', 'codex-core', ['--lib'], 'config::')
        cargo_test('isolated home resolution', 'codex-utils-home-dir', ['--lib'])
        cargo_test('TUI regression suite', 'codex-tui', ['--lib'], minimum=6000, single=True)
        report['status'] = 'passed-automated-only'
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        end_commit, end_clean = source_state()
        report.update(commit_at_end=end_commit, source_clean_at_end=end_clean,
                      eligible_for_candidate_validation=report['status'] == 'passed-automated-only' and clean and end_clean and commit == end_commit)
        write(folder / 'report.json', report)
        print('Validation report: ' + str(folder), flush=True)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('action', choices=['prepare', 'prepare-validation', 'build', 'test'])
    parser.add_argument('--profile', choices=['dev-small', 'release'], default='dev-small')
    args = parser.parse_args()
    cache, env = environment()
    if args.action == 'prepare-validation':
        run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--require-hashes', '--only-binary=:all:', '--upgrade', '--target', WORK / 'terminal-tools', '-r', REPO / 'intent/requirements-linux.txt'], env)
        return
    with cache_lease(cache, env):
        if args.action == 'prepare':
            prepare(cache, env)
        elif args.action == 'build':
            run([sys.executable, REPO / 'intent/build_package.py', '--profile', args.profile], env)
        else:
            test(env, args.profile)


if __name__ == '__main__':
    main()
