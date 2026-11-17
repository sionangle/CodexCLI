"""Portable Linux launcher; configuration and sessions stay separate from official Codex."""
import os
from pathlib import Path
import sys


def main():
    os.umask(0o077)
    root = Path(__file__).resolve().parent
    home = Path(os.environ.get('CODEX_INTENT_HOME', str(Path.home() / '.codex-intent'))).resolve()
    if home in [Path('/'), Path.home()] or home.is_relative_to((Path.home() / '.codex').resolve()):
        raise RuntimeError('Use a dedicated codex-intent data directory outside official Codex data.')
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with (home / 'config.toml').open('x', encoding='utf-8') as config:
            config.write('check_for_update_on_startup = false\ncli_auth_credentials_store = "file"\n[tui]\nintent_view = true\n')
    except FileExistsError:
        pass
    os.environ['CODEX_HOME'] = str(home)
    cli = root / 'package/bin/codex-intent'
    os.execv(str(cli), [str(cli), *sys.argv[1:]])


if __name__ == '__main__':
    main()
