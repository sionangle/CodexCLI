"""Download an already accepted custom binary release; official source adaptation stays separate."""
from pathlib import Path
import re
import shutil
import tarfile
from urllib.request import urlopen
import zipfile

from managed_runtime import contained, read, stamp, transition, verify_release, write
from platform_support import WINDOWS, digest_file

BASE = 'https://github.com/sionangle/CodexCLI/releases/latest/download'


def update(root):
    root = Path(root).resolve(strict=True)
    folder = contained(root, 'downloads/' + stamp())
    folder.mkdir(parents=True)
    report = {'status': 'downloading', 'source': BASE}
    name = 'codex-intent-windows-x64.zip' if WINDOWS else 'codex-intent-linux-x64.tar.gz'
    archive = folder / name
    try:
        with urlopen(BASE + '/' + name + '.sha256', timeout=60) as response:
            expected = response.read(256).decode('ascii').strip()
        if not re.fullmatch('[0-9a-f]{64}', expected):
            raise RuntimeError('Published archive checksum is invalid.')
        with urlopen(BASE + '/' + name, timeout=120) as response, archive.open('xb') as output:
            shutil.copyfileobj(response, output)
        if digest_file(archive) != expected:
            raise RuntimeError('Published archive differs from its SHA256; no version was switched.')
        unpacked = folder / 'unpacked'
        unpacked.mkdir()
        if WINDOWS:
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    path = unpacked / member.filename
                    if not path.resolve().is_relative_to(unpacked) or '\\' in member.filename:
                        raise RuntimeError('Invalid archive path.')
                bundle.extractall(unpacked)
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(unpacked, filter='data')
        candidate = unpacked / 'codex-intent'
        metadata = verify_release(candidate)
        if metadata['status'] != 'verified-custom-release':
            raise RuntimeError('Published updates require full maintenance acceptance.')
        state = read(root / 'state.json')
        current = verify_release(contained(root, state['current']['release']))
        if current['version'] == metadata['version'] and current['commit'] == metadata['commit']:
            if current != metadata:
                raise RuntimeError('Published release identifier has different accepted contents.')
            report['status'] = 'already-current'
            return state
        result = transition(root, candidate)
        report.update(status='committed', version=metadata['version'], after=result)
        return result
    except Exception as error:
        report.update(status='failed-no-publication', error=str(error))
        raise
    finally:
        write(folder / 'report.json', report)
