"""Small native-platform boundary shared by build, verification and maintenance tools."""
import hashlib
import os
import platform
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
    # Upstream packaging imports tomllib lazily. Ubuntu 22.04 ships Python 3.10.
    sys.modules['tomllib'] = tomllib

WINDOWS = os.name == 'nt'


def native_target():
    machine = platform.machine().lower()
    if machine not in ('amd64', 'x86_64'):
        raise RuntimeError('This delivery is validated for x86_64 only; other architectures require their own build and acceptance.')
    if WINDOWS:
        return 'x86_64-pc-windows-msvc'
    if sys.platform == 'linux':
        return 'x86_64-unknown-linux-gnu'
    raise RuntimeError('Only Windows and Linux are supported by the custom maintenance tools.')


def executable(name):
    return name + '.exe' if WINDOWS else name


def cache_root(repository):
    root = Path(repository) / '.intent-work'
    return root if WINDOWS else root / 'linux'


def assets_manifest(repository):
    return Path(repository) / '.intent-work' / ('assets.json' if WINDOWS else 'assets-linux.json')


def redirected(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()
