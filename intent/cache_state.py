"""Keep shared external Cargo artifacts while invalidating another checkout's local crates."""
import argparse
import errno
import hashlib
import os
from pathlib import Path
import subprocess
from managed_runtime import read, write
from upgrade import git
from platform_support import WINDOWS, cache_root, native_target

REPO = Path(__file__).resolve().parent.parent
TARGET = native_target()


def refresh(profile, log_path):
    common = Path(git(REPO, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout.strip()).resolve(strict=True)
    if common.name != '.git':
        raise RuntimeError('Cannot establish the owning project cache.')
    cache = cache_root(common.parent)
    target = Path(os.environ['CARGO_TARGET_DIR']).resolve()
    if target != (cache / 'target').resolve():
        raise RuntimeError('Refusing to clean a compiler cache outside the owning project.')
    lease = Path(os.environ.get('CODEX_INTENT_CACHE_LEASE', 'missing'))
    if lease.parent.resolve() / lease.name != cache / 'build-test.lock':
        raise RuntimeError('Use the PowerShell build or test entry to acquire the project cache lease.')
    try:
        with lease.open('r+b') as stream:
            if not WINDOWS:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(stream, fcntl.LOCK_UN)
            raise RuntimeError('The exclusive project cache lease is not held.')
    except OSError as error:
        # Python's Windows CRT may expose sharing violation 32 as EACCES without winerror.
        # The checked path is the lease opened by our parent PowerShell entry, not user data.
        if (WINDOWS and (error.errno != errno.EACCES or getattr(error, 'winerror', None) not in (None, 32))) or (not WINDOWS and error.errno not in (errno.EAGAIN, errno.EACCES)):
            raise

    identity = {'repository': str(REPO.resolve()), 'commit': git(REPO, 'rev-parse', 'HEAD').stdout.strip(),
                'target': TARGET,
                'toolchain': hashlib.sha256((REPO / 'codex-rs/rust-toolchain.toml').read_bytes()).hexdigest(),
                'lockfile': hashlib.sha256((REPO / 'codex-rs/Cargo.lock').read_bytes()).hexdigest()}
    dirty = bool(git(REPO, 'status', '--porcelain').stdout)
    state_file = cache / 'workspace-cache.json'
    state = read(state_file) if state_file.exists() else {'format': 1, 'profiles': {}}
    if state.get('format') != 1:
        raise RuntimeError('Unknown workspace cache provenance format.')
    if not dirty and state['profiles'].get(profile) == identity:
        Path(log_path).write_text('Shared cache already belongs to this clean source and profile.\n', encoding='utf8')
        return {'status': 'matching-source', **identity}

    metadata = subprocess.run(['cargo', 'metadata', '--locked', '--no-deps', '--format-version', '1'],
                              cwd=REPO / 'codex-rs', capture_output=True, encoding='utf8', check=True)
    import json
    metadata = json.loads(metadata.stdout)
    members = set(metadata['workspace_members'])
    packages = [p for p in metadata['packages'] if p['id'] in members]
    if not packages or len(packages) > 1000 or any(p['source'] is not None or not
            Path(p['manifest_path']).resolve().is_relative_to(REPO / 'codex-rs') for p in packages):
        raise RuntimeError('Workspace package ownership could not be established; nothing was cleaned.')
    command = ['cargo', 'clean', '--locked', '--profile', profile, '--target', TARGET,
               '--target-dir', str(target)]
    for package in packages:
        command += ['--package', package['name'] + '@' + package['version']]
    # Invalidate before cleaning: an interrupted cleanup must never certify the old contents.
    state['profiles'].pop(profile, None)
    write(state_file, state)
    print('Refreshing local workspace artifacts for this source; external dependencies are retained.', flush=True)
    with Path(log_path).open('w', encoding='utf8') as output:
        if target.exists():
            subprocess.run(command, cwd=REPO / 'codex-rs', stdout=output, stderr=subprocess.STDOUT, check=True)
        else:
            output.write('No existing compiler cache.\n')
    if not dirty:
        state['profiles'][profile] = identity
        write(state_file, state)
    return {'status': 'refreshed-local-workspace', 'packages': len(packages), 'command': command, **identity}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--profile', choices=['dev-small', 'release'], required=True)
    parser.add_argument('--log', type=Path, required=True)
    args = parser.parse_args()
    print(refresh(args.profile, args.log)['status'])
