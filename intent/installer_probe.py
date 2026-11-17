"""Exercise the actual download installers and installed commands with no development PATH."""
import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import uuid

from managed_runtime import read, stamp, verify_release, write
from platform_support import WINDOWS, digest_file
from upgrade import git, validation_workspace

REPO = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--distribution', type=Path, required=True)
    parser.add_argument('--previous', type=Path, required=True)
    args = parser.parse_args()
    distribution = args.distribution.resolve(strict=True)
    manifest = read(distribution / 'distribution.json')
    candidate = Path(manifest['release']).resolve(strict=True)
    metadata = verify_release(candidate)
    previous = verify_release(args.previous)
    archive = distribution / manifest['archive']
    assert digest_file(archive) == manifest['archive_sha256']
    run = REPO / '.intent-work' / ('installer-acceptance-' + stamp())
    run.mkdir(parents=True)
    fixture = validation_workspace(REPO) / ('ip-' + uuid.uuid4().hex[:6])
    fixture.mkdir()
    root, bin_dir = fixture / '安装', fixture / 'commands'
    env = dict(os.environ, PYTHONUTF8='1')
    for key in ['PYTHONPATH', 'PYTHONHOME', 'OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_HOME']:
        env.pop(key, None)
    if WINDOWS:
        system = Path(os.environ['SystemRoot']) / 'System32'
        env['PATH'] = os.pathsep.join(map(str, [system, system / 'WindowsPowerShell/v1.0', system / 'Wbem']))
    else:
        minimal = fixture / 'system-bin'
        minimal.mkdir()
        for name in ['bash', 'curl', 'tar', 'gzip', 'sha256sum', 'uname', 'mktemp', 'rm', 'mkdir', 'dirname']:
            source = shutil.which(name)
            assert source, name
            (minimal / name).symlink_to(source)
        env['PATH'] = str(minimal)
    assert not any(shutil.which(name, path=env['PATH']) for name in ['python', 'python3', 'rustc', 'cargo', 'git'])
    env['PATH'] = str(bin_dir) + os.pathsep + env['PATH']
    report = {'status': 'running', 'archive_sha256': manifest['archive_sha256'],
              'version': metadata['version'], 'target': manifest['target'], 'fixture': str(fixture),
              'source_commit': git(REPO, 'rev-parse', 'HEAD').stdout.strip(),
              'source_clean_at_start': not git(REPO, 'status', '--porcelain').stdout, 'checks': []}
    corrupt = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            name = self.path.lstrip('/')
            if name not in [archive.name, archive.name + '.sha256']:
                self.send_error(404)
                return
            path = distribution / name
            self.send_response(200)
            self.send_header('Content-Length', str(path.stat().st_size))
            self.end_headers()
            with path.open('rb') as stream:
                first = stream.read(1024 * 1024)
                if corrupt and name == archive.name:
                    first = bytes([first[0] ^ 1]) + first[1:]
                self.wfile.write(first)
                shutil.copyfileobj(stream, self.wfile)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env['CODEX_INTENT_RELEASE_BASE_URL'] = f'http://127.0.0.1:{server.server_port}'

    def command(label, arguments, success=True, environment=None):
        print('CHECK: ' + label, flush=True)
        log = run / (label + '.log')
        with log.open('w', encoding='utf-8') as output:
            result = subprocess.run(list(map(str, arguments)), cwd=fixture, env=environment or env,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=600)
        if (result.returncode == 0) != success:
            raise RuntimeError(label + ' returned unexpected status; see ' + str(log))
        print('PASS: ' + label, flush=True)
        return log.read_text(encoding='utf-8', errors='replace')

    def bootstrap(label, success=True, install_root=root, commands=bin_dir):
        if WINDOWS:
            args = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    distribution / 'install.ps1', '-InstallRoot', install_root,
                    '-BinDirectory', commands, '-NoPath']
        else:
            args = ['bash', distribution / 'install.sh', '--install-root', install_root,
                    '--bin-dir', commands, '--no-path']
        return command(label, args, success)

    def named(label, name, *extra):
        if WINDOWS:
            # PowerShell resolves the command through PATH, exactly as a new terminal does.
            return command(label, ['powershell.exe', '-NoProfile', '-Command', name + ' ' + ' '.join(extra)])
        return command(label, [name, *extra])

    @contextmanager
    def active_lease():
        if WINDOWS:
            script = fixture / 'hold.ps1'
            script.write_text("$f=[IO.File]::Open($env:INTENT_TEST_LOCK,'Open','Read','Read')\n"
                              "try { [Console]::WriteLine('ready'); [Console]::ReadLine() | Out-Null } finally { $f.Dispose() }\n")
            arguments = ['powershell.exe', '-NoProfile', '-File', str(script)]
        else:
            script = fixture / 'hold.py'
            script.write_text("import fcntl,os\nf=open(os.environ['INTENT_TEST_LOCK'],'rb')\n"
                              "fcntl.flock(f,fcntl.LOCK_SH|fcntl.LOCK_NB)\nprint('ready',flush=True)\ninput()\n")
            arguments = [str(candidate / 'runtime/bin/python3'), '-E', '-s', '-B', str(script)]
        process = subprocess.Popen(arguments, env=dict(env, INTENT_TEST_LOCK=str(root / 'runtime.lock')),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            ready = queue.Queue()
            threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
            assert ready.get(timeout=30).strip() == 'ready'
            yield
        finally:
            process.communicate('\n', timeout=30)

    try:
        if WINDOWS:
            manager = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                       candidate / 'intent/Maintain-Intent.ps1', 'install', '-InstallRoot', root]
        else:
            manager = ['bash', candidate / 'intent/maintain-intent', 'install', '--install-root', root]
        command('seed-real-previous-release', [*manager, '--release', args.previous.resolve()])
        initial = read(root / 'state.json')
        (root / initial['current']['home'] / 'installer-data.txt').write_text('before upgrade')
        before = (root / 'state.json').read_bytes()
        with active_lease():
            bootstrap('active-client-refuses-installer-upgrade', False)
        assert (root / 'state.json').read_bytes() == before
        report['checks'].append('active-client-refuses-upgrade-with-unchanged-selection')
        bootstrap('download-and-install-upgrade')
        assert metadata['version'] in named('named-cli-version', 'codex-intent', '--version')
        assert json.loads(named('named-maintenance-status', 'codex-intent-maintain', 'status'))['current_version'] == metadata['version']
        changed = read(root / 'state.json')
        new_home = root / changed['current']['home']
        assert (new_home / 'installer-data.txt').read_text() == 'before upgrade'
        (new_home / 'installer-data.txt').write_text('after upgrade')
        report['checks'].append('download-installer-and-PATH-commands-work-without-python-git-rust-in-Unicode-directory')
        before = (root / 'state.json').read_bytes()
        bootstrap('repeat-install-is-idempotent')
        assert (root / 'state.json').read_bytes() == before
        report['checks'].append('repeat-install-keeps-current-data-generation')
        corrupt = True
        bootstrap('corrupted-download-refused', False)
        assert (root / 'state.json').read_bytes() == before
        corrupt = False
        report['checks'].append('corrupted-archive-keeps-current-version-and-data')
        named('named-maintenance-rollback', 'codex-intent-maintain', 'rollback')
        assert previous['version'] in named('named-cli-after-rollback', 'codex-intent', '--version')
        assert json.loads(named('private-manager-after-rollback', 'codex-intent-maintain', 'status'))['current_version'] == previous['version']
        restored = read(root / 'state.json')
        assert (root / restored['current']['home'] / 'installer-data.txt').read_text() == 'before upgrade'
        assert (new_home / 'installer-data.txt').read_text() == 'after upgrade'
        report['checks'].append('named-rollback-restores-old-binary-and-data-with-retained-private-runtime')
        # Exercise the production downloader/transition with only its HTTP endpoint replaced
        # in the test process. The immutable installed scripts and all safety checks remain.
        driver = fixture / 'published-update.py'
        driver.write_text('''import json,os,sys
from pathlib import Path
root=Path(os.environ['INTENT_PROBE_ROOT'])
state=json.loads((root/'state.json').read_text(encoding='utf-8'))
sys.path.insert(0,str(root/state['manager']))
import published_update
from maintenance import install_launchers
published_update.BASE=os.environ['CODEX_INTENT_RELEASE_BASE_URL']
if os.name != 'nt':
    import fcntl
    lock=(root/'runtime.lock').open('r+b')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
published_update.update(root)
install_launchers(root)
''', encoding='utf-8')
        interpreter = root / restored['manager'] / '..' / ('runtime/python.exe' if WINDOWS else 'runtime/bin/python3')
        update_env = dict(env, INTENT_PROBE_ROOT=str(root), INTENT_PROBE_PYTHON=str(interpreter.resolve()),
                          INTENT_PROBE_DRIVER=str(driver))
        if WINDOWS:
            holder = fixture / 'update.ps1'
            holder.write_text("$ErrorActionPreference='Stop'\n"
                              "$lock=[IO.File]::Open((Join-Path $env:INTENT_PROBE_ROOT 'runtime.lock'),'Open','ReadWrite','None')\n"
                              "try { & $env:INTENT_PROBE_PYTHON -E -s -B -X utf8 $env:INTENT_PROBE_DRIVER; exit $LASTEXITCODE } finally { $lock.Dispose() }\n")
            arguments = ['powershell.exe', '-NoProfile', '-File', holder]
        else:
            arguments = [interpreter.resolve(), '-E', '-s', '-B', '-X', 'utf8', driver]
        command('published-binary-update', arguments, environment=update_env)
        assert metadata['version'] in named('updated-named-cli', 'codex-intent', '--version')
        before = (root / 'state.json').read_bytes()
        command('published-update-already-current', arguments, environment=update_env)
        assert (root / 'state.json').read_bytes() == before
        report['checks'].append('published-downloader-upgrades-and-repeated-update-is-idempotent')
        # Verify a first installation as well as an upgrade. Commands go in a different
        # isolated directory; no user PATH, registry, shell profile or official data is used.
        bootstrap('fresh-install', install_root=fixture / 'fresh', commands=fixture / 'fresh-bin')
        fresh_env = dict(env, PATH=str(fixture / 'fresh-bin') + os.pathsep + env['PATH'])
        arguments = ['powershell.exe', '-NoProfile', '-Command', 'codex-intent --version'] if WINDOWS else ['codex-intent', '--version']
        assert metadata['version'] in command('fresh-named-cli', arguments, environment=fresh_env)
        report['checks'].append('fresh-install-works-without-development-toolchain')
        report['status'] = 'passed-installer-acceptance'
    except Exception as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        server.shutdown()
        report['source_commit_at_end'] = git(REPO, 'rev-parse', 'HEAD').stdout.strip()
        report['source_clean_at_end'] = not git(REPO, 'status', '--porcelain').stdout
        if report['status'] == 'passed-installer-acceptance' and (
                not report['source_clean_at_start'] or not report['source_clean_at_end']
                or report['source_commit'] != report['source_commit_at_end']):
            report['status'] = 'passed-installer-diagnostic-only'
        write(run / 'report.json', report)
        print('Installer acceptance report: ' + str(run), flush=True)


if __name__ == '__main__':
    main()
