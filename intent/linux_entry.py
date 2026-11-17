"""Linux launch exclusion and private managed data; no global configuration is changed."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

MARKER = 'codex-intent-managed-install-v1'
DEFAULT_ROOT = Path.home() / '.local/share/codex-intent'


def private_path(path):
    path = Path(os.path.abspath(path))
    for ancestor in [path, *path.parents]:
        if ancestor.is_symlink():
            raise RuntimeError('Managed paths cannot redirect through links: ' + str(ancestor))
    if path in [Path('/'), Path.home()] or path.is_relative_to(Path.home() / '.codex'):
        raise RuntimeError('Choose a private codex-intent directory outside official Codex data.')
    return path


def contained(root, value):
    path = private_path(root / value)
    if path == root or not path.is_relative_to(root):
        raise RuntimeError('Managed path leaves the installation.')
    return path


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


@contextmanager
def lease(root, exclusive):
    if (root / '.intent-install').read_text(encoding='ascii').strip() != MARKER:
        raise RuntimeError('Not a managed codex-intent installation; existing files were not changed.')
    private_path(root / 'runtime.lock')
    with (root / 'runtime.lock').open('r+b') as stream:
        try:
            fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A managed client or maintenance operation is active. No version was switched.') from None
        # Python descriptors are non-inheritable: daemon descendants cannot retain this lease.
        yield


def main():
    if sys.platform != 'linux':
        raise RuntimeError('Use the PowerShell entry on Windows.')
    os.umask(0o077)
    mode, *arguments = sys.argv[1:]
    source = Path(__file__).resolve().parent
    installed = (source / '.intent-install').is_file()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--install-root', type=Path, default=source if installed else DEFAULT_ROOT)
    args, remaining = parser.parse_known_args(arguments)
    root = private_path(args.install_root)
    if mode == 'run':
        with lease(root, False):
            state = read(root / 'state.json')
            if state['format'] != 1:
                raise RuntimeError('Unrecognized installation state format.')
            home = contained(root, state['current']['home'])
            release = contained(root, state['current']['release'])
            cli = contained(root, str(release.relative_to(root) / 'package/bin/codex-intent'))
            return subprocess.call([str(cli), *remaining], env=dict(os.environ, CODEX_HOME=str(home)))
    if mode != 'maintain':
        raise RuntimeError('Unknown entry mode.')
    action = remaining[0] if remaining else 'status'
    if installed:
        state = read(root / 'state.json')
        if state.get('format') != 1 or not state.get('manager'):
            raise RuntimeError('Use intent/maintain-intent from a verified custom release.')
        source = contained(root, state['manager'])
    sys.path.insert(0, str(source))
    from maintenance import main as maintain
    sys.argv = [str(source / 'maintenance.py'), *(remaining or ['status']), '--install-root', str(root)]
    if action not in ['install', 'switch', 'rollback', 'quiesce']:
        maintain()
        return 0
    if action == 'install' and not root.exists():
        root.mkdir(parents=True, mode=0o700)
        (root / '.intent-install').write_text(MARKER, encoding='ascii')
        (root / 'runtime.lock').touch(mode=0o600)
    with lease(root, True):
        maintain()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
