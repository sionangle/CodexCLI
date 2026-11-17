"""Prepare a pinned private maintenance interpreter; never install it on the user's PATH."""
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from urllib.request import urlopen
import uuid

from managed_runtime import hashes, read, write
from platform_support import cache_root, digest_file, native_target
from upgrade import git

REPO = Path(__file__).resolve().parent.parent


def prepare(repository=REPO):
    repository = Path(repository).resolve(strict=True)
    manifest = read(repository / 'intent/runtime-manifest.json')
    target = native_target()
    asset = manifest['targets'][target]
    common = Path(git(repository, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout.strip()).resolve(strict=True)
    platform = 'windows' if 'windows' in target else 'linux'
    folder = cache_root(common.parent) / 'runtimes' / (platform + '-' + asset['sha256'][:16])
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / 'python.tar.gz'
    if not archive.exists() or digest_file(archive) != asset['sha256']:
        temporary = folder / ('download-' + uuid.uuid4().hex + '.partial')
        print('Downloading pinned private maintenance runtime...', flush=True)
        with urlopen(asset['url'], timeout=120) as response, temporary.open('xb') as output:
            shutil.copyfileobj(response, output)
        if digest_file(temporary) != asset['sha256']:
            raise RuntimeError('Private runtime download differs from the recorded publisher SHA256.')
        temporary.replace(archive)
    runtime = folder / 'runtime'
    recorded = folder / 'runtime-files.json'
    if runtime.exists():
        if not recorded.is_file() or hashes(runtime) != read(recorded):
            raise RuntimeError('Cached private runtime changed; retain it for diagnosis and use a new cache directory.')
    else:
        unpacked = folder / ('x-' + uuid.uuid4().hex[:8])
        unpacked.mkdir()
        with tarfile.open(archive) as bundle:
            if any(not member.name.startswith('python/') for member in bundle.getmembers()):
                raise RuntimeError('Unexpected private runtime archive layout.')
            bundle.extractall(unpacked, filter='data')
        # The publisher uses internal aliases. Materialize them in the immutable release so
        # the same no-redirect file hash policy applies to the CLI and its private runtime.
        prepared = folder / ('p-' + uuid.uuid4().hex[:8])
        shutil.copytree(unpacked / 'python', prepared, symlinks=False)
        prepared.rename(runtime)
        write(recorded, hashes(runtime))
    executable = runtime / asset['executable']
    command = [str(executable), '-E', '-s', '-B', '-X', 'utf8', '-c',
               'import json,sys,sqlite3,ssl,tomllib,lzma; '
               'db=sqlite3.connect(":memory:"); db.execute("select 1"); db.close(); '
               'print(json.dumps({"python":sys.version.split()[0],"openssl":ssl.OPENSSL_VERSION}))']
    result = subprocess.run(command, capture_output=True, encoding='utf-8', check=True)
    smoke = json.loads(result.stdout)
    if smoke['python'] != manifest['python_version']:
        raise RuntimeError('Private runtime version differs from its pinned manifest.')
    write(folder / 'report.json', {'status': 'verified-private-runtime', 'manifest': manifest,
                                 'target': target, 'smoke': smoke, 'sha256': read(recorded)})
    return runtime


if __name__ == '__main__':
    print('Private runtime: ' + str(prepare()), flush=True)
