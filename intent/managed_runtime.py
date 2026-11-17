"""Internal managed installation operations; the native entry holds the runtime lock.

Data generations are never reused across versions. Rollback copies the pre-switch snapshot;
it never asks an old executable to open a newer database. Failed candidates stay unpublished.
"""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import uuid
from platform_support import WINDOWS, digest_file, executable, native_target, redirected


MARKER = 'codex-intent-managed-install-v1'
REQUIRED_FEATURES = {'intent-view', 'v2-task-panel'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def new_home(root):
    # Windows AF_UNIX addresses are bounded; timestamps belong in the transaction report,
    # while data generations use short opaque names. Never silently disable daemon support.
    home = contained(root, 'd/' + uuid.uuid4().hex[:8])
    if len(str(home / 'app-server-control/app-server-control.sock').encode('utf-8')) >= 108:
        raise RuntimeError('Install path is too long for the daemon Unix socket. Choose a shorter installation directory.')
    return home


def contained(root, relative):
    root = Path(root).resolve(strict=True)
    candidate = root / relative
    if redirected(candidate):
        raise RuntimeError('Managed state must not redirect through a link: ' + str(candidate))
    resolved = candidate.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise RuntimeError('Path leaves the managed installation: ' + str(candidate))
    return resolved


def hashes(root):
    result = {}
    root = Path(root).resolve(strict=True)
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(directory) / name
            if redirected(path):
                raise RuntimeError('Release package contains a redirect: ' + str(path))
        for name in files:
            path = Path(directory) / name
            result[path.relative_to(root).as_posix()] = digest_file(path)
    return result


def verify_release(folder):
    folder = Path(folder).resolve(strict=True)
    release = read(folder / 'release.json')
    if release.get('status') not in ['verified-stage-2-portable', 'verified-custom-release']:
        raise RuntimeError('Only an accepted custom release can be selected.')
    if not REQUIRED_FEATURES.issubset(release.get('features', [])):
        raise RuntimeError('Required custom features are absent. Refusing the upgrade.')
    expected = release['sha256']
    actual = hashes(folder)
    actual.pop('release.json')
    if actual != expected:
        raise RuntimeError('Release file set or SHA256 differs from acceptance evidence.')
    if read(folder / 'package/codex-package.json')['target'] != native_target():
        raise RuntimeError('Release target does not match this operating system and architecture.')
    if '+intent.' not in release['version']:
        raise RuntimeError('Official releases cannot replace codex-intent.')
    return release


def checked_root(root):
    root = Path(root).resolve(strict=True)
    if (root / '.intent-install').read_text(encoding='ascii').strip() != MARKER:
        raise RuntimeError('Not a managed codex-intent installation.')
    if root == Path.home() or root.is_relative_to(Path.home() / '.codex'):
        raise RuntimeError('The official Codex data directory cannot be managed here.')
    return root


def package_path(root, selection):
    release = contained(root, selection['release'])
    verify_release(release)
    return release / 'package'


def scoped_processes(root, tracked=()):
    if not WINDOWS:
        from linux_processes import scoped
        return scoped(root, tracked)
    # Filter inside PowerShell; no unrelated command lines, credentials or environment are read.
    script = r'''$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$base = [IO.Path]::GetFullPath($env:INTENT_PROCESS_ROOT).TrimEnd('\') + '\'
$maintenancePid = [uint32]$env:INTENT_MAINTENANCE_PID
$all = @(Get-CimInstance Win32_Process)
$rows = @($all | Where-Object {
    $_.ProcessId -ne $maintenancePid -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)
})
$ids = New-Object 'System.Collections.Generic.HashSet[uint32]'
foreach ($row in $rows) { $null = $ids.Add($row.ProcessId) }
do {
    $changed = $false
    foreach ($row in $all) {
        if ($ids.Contains($row.ParentProcessId) -and $ids.Add($row.ProcessId)) {
            $rows += $row
            $changed = $true
        }
    }
} while ($changed)
$output = @($rows | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath)
ConvertTo-Json -InputObject $output -Compress'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                            env=dict(os.environ, INTENT_PROCESS_ROOT=str(root), INTENT_MAINTENANCE_PID=str(os.getpid())),
                            capture_output=True, encoding='utf-8', timeout=30, check=True)
    return json.loads(result.stdout or '[]')


def isolated_environment(home):
    environment = dict(os.environ, CODEX_HOME=str(home))
    for key in ['OPENAI_API_KEY', 'CODEX_API_KEY']:
        environment.pop(key, None)
    return environment


def quiesce(root, selection, report):
    package = package_path(root, selection)
    home = contained(root, selection['home'])
    processes = scoped_processes(root)
    has_control_state = any((home / 'app-server-daemon').glob('*.pid')) or (home / 'app-server-control/app-server-control.sock').exists()
    if not processes and not has_control_state:
        report['daemon'] = 'no-owned-processes'
        return
    service_images = {'codex.exe', 'codex-intent.exe', 'codex-exec-server.exe', 'codex-code-mode-host.exe'} if WINDOWS else {
        'codex', 'codex-intent', 'codex-exec-server', 'codex-code-mode-host',
        'codex-exec-serv', 'codex-code-mode',  # Linux comm is truncated to 15 bytes.
    }
    if any(row['Name'].lower() not in service_images for row in processes):
        raise RuntimeError('A background command or owned child process is still active; no stop or switch was requested.')
    # The native command refuses active turns, uses an idempotent graceful drain, never kills,
    # and holds the daemon lifecycle lock. It also rejects remote control and updater races.
    result = subprocess.run([str(package / 'bin' / executable('codex-intent')), 'app-server', 'daemon', 'intent-quiesce'],
                            env=isolated_environment(home), cwd=root, capture_output=True,
                            encoding='utf-8', timeout=75)
    report['daemon_exit_code'] = result.returncode
    if result.returncode:
        raise RuntimeError('Daemon cannot be safely quiesced; current selection is unchanged. ' + result.stderr.strip())
    remaining = scoped_processes(root, processes)
    if remaining:
        raise RuntimeError('Owned processes remain active; no snapshot or version switch was made. PIDs: ' +
                           ', '.join(str(row['ProcessId']) for row in remaining))
    report['daemon'] = 'gracefully-stopped'


def copy_home(source, destination, reset_daemon=False):
    """Offline full copy. Internal package junctions are materialized, never followed outside home."""
    source = Path(source).resolve(strict=True)
    destination = Path(destination)
    if destination.exists():
        raise RuntimeError('Snapshot destination already exists.')
    seen = set()

    def copy_dir(current, target, relative):
        canonical = current.resolve(strict=True)
        if not canonical.is_relative_to(source) or canonical in seen:
            raise RuntimeError('Data contains an external link or directory cycle: ' + str(current))
        seen.add(canonical)
        target.mkdir()
        try:
            for child in current.iterdir():
                rel = relative / child.name
                # Socket rendezvous and startup locks are process state, not user/session data.
                if rel.as_posix() == 'app-server-control':
                    continue
                # These are derived daemon install/control state. The complete backup keeps them;
                # a new generation starts a fresh daemon from its selected CLI package.
                if reset_daemon and rel.as_posix() in ['packages/app-server-daemon', 'packages/standalone',
                                                     'app-server-daemon', 'app-server.sock']:
                    continue
                resolved = child.resolve(strict=True)
                if not resolved.is_relative_to(source):
                    raise RuntimeError('Data link leaves the isolated home: ' + str(child))
                if child.is_dir():
                    copy_dir(child, target / child.name, rel)
                elif child.is_file():
                    shutil.copy2(child, target / child.name)
                else:
                    # A stopped Windows daemon may leave a socket rendezvous file.
                    if rel.as_posix() != 'app-server.sock':
                        raise RuntimeError('Unsupported data entry: ' + str(child))
        finally:
            seen.remove(canonical)
    copy_dir(source, destination, Path())
    if reset_daemon and (source / 'app-server-daemon/settings.json').is_file():
        settings = read(source / 'app-server-daemon/settings.json')
        settings.setdefault('updater', {})['autoUpdateEnabled'] = False
        (destination / 'app-server-daemon').mkdir(exist_ok=True)
        write(destination / 'app-server-daemon/settings.json', settings)


def check_databases(home):
    # Open only the new copy, so SQLite may recover its WAL without altering the backup.
    checked = []
    for path in Path(home).rglob('*'):
        if not path.is_file() or path.suffix not in ['.sqlite', '.sqlite3', '.db']:
            continue
        with path.open('rb') as stream:
            if stream.read(16) != b'SQLite format 3\x00':
                continue
        with closing(sqlite3.connect(str(path), timeout=10)) as database, database:
            result = database.execute('PRAGMA quick_check').fetchall()
        if result != [('ok',)]:
            raise RuntimeError('Snapshot database integrity failed: ' + str(path.relative_to(home)))
        checked.append(path.relative_to(home).as_posix())
    return checked


def register_release(root, source):
    release = verify_release(source)
    name = release['version'] + '-' + release['commit'][:12]
    if any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.+-_' for char in name):
        raise RuntimeError('Invalid release identifier.')
    target = contained(root, 'releases/' + name)
    target.parent.mkdir(exist_ok=True)
    if not target.exists():
        staging = target.with_name(target.name + '.preparing-' + uuid.uuid4().hex)
        shutil.copytree(source, staging)
        verify_release(staging)
        staging.rename(target)
    else:
        installed = verify_release(target)
        if installed != release:
            raise RuntimeError('Release identifier collision; existing files were retained.')
    return target.relative_to(root).as_posix()


def initialize(root, source):
    root = checked_root(root)
    state_file = root / 'state.json'
    if state_file.exists():
        raise RuntimeError('Installation already initialized. Use switch for an upgrade.')
    release = register_release(root, source)
    home = new_home(root)
    home.mkdir(parents=True)
    (home / 'config.toml').write_text('check_for_update_on_startup = false\ncli_auth_credentials_store = "file"\n'
                                    '[tui]\nintent_view = true\n', encoding='utf-8')
    state = {'format': 1, 'current': {'release': release, 'home': home.relative_to(root).as_posix()},
             'previous': None, 'last_transaction': None,
             'manager': release + '/intent' if (root / release / 'intent/maintenance.py').is_file() else None}
    write(state_file, state)
    return state


def transition(root, source=None, rollback=False):
    root = checked_root(root)
    state_file = root / 'state.json'
    state = read(state_file)
    report_dir = contained(root, 'transactions/' + stamp())
    report_dir.mkdir(parents=True)
    report = {'status': 'preparing', 'action': 'rollback' if rollback else 'switch', 'before': state}
    try:
        if rollback:
            if not state.get('previous'):
                raise RuntimeError('No previous accepted version and snapshot are available.')
            chosen = state['previous']
            package_path(root, chosen)
        else:
            chosen = {'release': register_release(root, source)}
        if chosen['release'] == state['current']['release']:
            raise RuntimeError('This release is already active.')
        quiesce(root, state['current'], report)
        # The outer exclusive launch lock remains held until the single pointer replacement.
        current_home = contained(root, state['current']['home'])
        backup = report_dir / 'before-home'
        copy_home(current_home, backup)
        report['backup_sha256'] = hashes(backup)
        backup_state = {**state['current'], 'home': backup.relative_to(root).as_posix()}
        candidate_home = new_home(root)
        candidate_home.parent.mkdir(exist_ok=True)
        origin = contained(root, chosen['home']) if rollback else backup
        if rollback:
            recorded = chosen.get('sha256')
            if recorded is None or hashes(origin) != recorded:
                raise RuntimeError('Rollback snapshot integrity failed; selection is unchanged.')
        copy_home(origin, candidate_home, reset_daemon=True)
        report['databases_checked'] = check_databases(candidate_home)
        report['new_home'] = candidate_home.relative_to(root).as_posix()
        # Smoke start on the copy triggers that executable's normal data migrations. Failure only
        # changes the unpublished copy; the running selection and consistent backup stay intact.
        package = contained(root, chosen['release']) / 'package'
        from smoke_package import initialize_rpc
        result = initialize_rpc([str(package / 'bin' / executable('codex-intent')), 'app-server', '--listen', 'stdio://'],
                                {'clientInfo': {'name': 'intent-maintenance', 'version': '1'},
                                 'capabilities': {'experimentalApi': True}},
                                isolated_environment(candidate_home), report_dir, 'candidate-migration')
        if Path(result['codexHome']).resolve() != candidate_home:
            raise RuntimeError('Candidate opened an unexpected data directory.')
        if scoped_processes(root):
            raise RuntimeError('Candidate or another managed process has not exited. Publication refused.')
        report['databases_after_migration'] = check_databases(candidate_home)
        chosen_manager = chosen['release'] + '/intent' if (root / chosen['release'] / 'intent/maintenance.py').is_file() else None
        # Keep the installed maintenance implementation when rolling the CLI/data back.
        # Older releases may have tools but no private interpreter or newer state support.
        manager = (state.get('manager') or chosen_manager) if rollback else (chosen_manager or state.get('manager'))
        after = {'format': 1, 'current': {'release': chosen['release'], 'home': report['new_home']},
                 'previous': {**backup_state, 'sha256': report['backup_sha256']},
                 'last_transaction': report_dir.relative_to(root).as_posix(),
                 'manager': manager}
        report['after'] = after
        report['status'] = 'ready-to-commit'
        write(report_dir / 'report.json', report)
        # Data generations and both package directories already exist and are closed. This is
        # the only publication step; a crash before it leaves the old pointer usable.
        write(state_file, after)
        report['status'] = 'committed'
        return after
    except Exception as error:
        report.update(status='failed-no-publication', error=str(error))
        raise
    finally:
        write(report_dir / 'report.json', report)
