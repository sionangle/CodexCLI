"""Package an accepted release for direct installation; publication requires installer acceptance."""
import argparse
from pathlib import Path
import shutil
import tarfile
import zipfile

from managed_runtime import hashes, read, verify_release, write
from platform_support import WINDOWS, digest_file, native_target


def package(release, destination):
    release = Path(release).resolve(strict=True)
    destination = Path(destination).resolve()
    metadata = verify_release(release)
    if metadata['status'] != 'verified-custom-release':
        raise RuntimeError('Distribution requires complete custom CLI and maintenance acceptance.')
    interpreter = release / ('runtime/python.exe' if WINDOWS else 'runtime/bin/python3')
    if not interpreter.is_file() or not (release / 'intent/install.py').is_file():
        raise RuntimeError('The accepted release lacks its private runtime or installer.')
    if destination.exists():
        raise RuntimeError('Distribution output already exists; no files were overwritten.')
    destination.mkdir(parents=True)
    filename = 'codex-intent-windows-x64.zip' if WINDOWS else 'codex-intent-linux-x64.tar.gz'
    archive = destination / filename
    if WINDOWS:
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
            for path in sorted(release.rglob('*')):
                if path.is_file():
                    output.write(path, 'codex-intent/' + path.relative_to(release).as_posix())
    else:
        with tarfile.open(archive, 'w:gz', dereference=True) as output:
            output.add(release, arcname='codex-intent')
    verify_release(release)
    digest = digest_file(archive)
    (destination / (filename + '.sha256')).write_text(digest + '\n', encoding='ascii')
    for script in ['install.sh', 'install.ps1']:
        shutil.copy2(release / 'intent' / script, destination / script)
    report = {'status': 'packaged-installer-unvalidated', 'target': native_target(),
              'version': metadata['version'], 'commit': metadata['commit'], 'release': str(release),
              'archive': filename, 'archive_sha256': digest, 'files': hashes(destination)}
    write(destination / 'distribution.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(package(args.release, args.destination))
