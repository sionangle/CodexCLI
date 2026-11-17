"""Install an accepted binary release and its command entries without a development toolchain."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid

from managed_runtime import MARKER, checked_root, contained, read, verify_release, write
from platform_support import WINDOWS, native_target, redirected

ENTRY_MARKER = 'codex-intent managed command entry'


def default_root():
    return Path(os.environ['LOCALAPPDATA']) / 'codex-intent' if WINDOWS else Path.home() / '.local/share/codex-intent'


def default_bin():
    if not WINDOWS and os.geteuid() == 0 and '/usr/local/bin' in os.environ.get('PATH', '').split(os.pathsep):
        return Path('/usr/local/bin')
    return Path.home() / '.local/bin'


def owned_entry(path):
    if not path.exists() and not path.is_symlink():
        return True
    if not WINDOWS and path.is_symlink():
        target = path.resolve()
        marker = target.parent / '.intent-install'
        return marker.is_file() and marker.read_text(encoding='ascii').strip() == MARKER
    return path.is_file() and ENTRY_MARKER in path.read_text(encoding='utf-8', errors='replace')[:512]


def command_entries(root, folder, publish=False):
    destinations = {'codex-intent': 'codex-intent', 'codex-intent-maintain': 'maintain-intent'}
    for name, target in destinations.items():
        path = folder / (name + '.cmd' if WINDOWS else name)
        if not owned_entry(path):
            raise RuntimeError('An unrelated command already exists; choose --bin-dir: ' + str(path))
    helper = folder / 'Invoke-CodexIntent.ps1'
    if WINDOWS and not owned_entry(helper):
        raise RuntimeError('An unrelated command helper already exists: ' + str(helper))
    if not publish:
        return
    folder.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        # ASCII wrappers also work with a non-UTF-8 Windows console code page.
        encoded = base64.b64encode(str(root).encode('utf-8')).decode('ascii')
        temporary = helper.with_name('.intent-helper-' + uuid.uuid4().hex[:8])
        temporary.write_text('# ' + ENTRY_MARKER + '\n'
                             'param([Parameter(Position=0)][string]$Mode,\n'
                             ' [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)\n'
                             "$ErrorActionPreference = 'Stop'\n"
                             "$root = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "'))\n"
                             "$script = if ($Mode -eq 'cli') { 'Run-CodexIntent.ps1' } else { 'Maintain-Intent.ps1' }\n"
                             '& (Join-Path $root $script) @Arguments\n'
                             'exit $LASTEXITCODE\n', encoding='ascii')
        os.replace(temporary, helper)
    for name, target in destinations.items():
        path = folder / (name + '.cmd' if WINDOWS else name)
        temporary = folder / ('.' + name + '-' + uuid.uuid4().hex[:8])
        if WINDOWS:
            mode = 'cli' if name == 'codex-intent' else 'maintain'
            temporary.write_text('@rem ' + ENTRY_MARKER + '\r\n@echo off\r\n'
                                 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File '
                                 '"%~dp0Invoke-CodexIntent.ps1" ' + mode + ' %*\r\n', encoding='ascii')
        else:
            temporary.symlink_to(root / target)
        os.replace(temporary, path)


def configure_path(folder):
    if WINDOWS:
        script = '''$ErrorActionPreference = 'Stop'
$entry = $env:CODEX_INTENT_INSTALL_BIN
$current = [Environment]::GetEnvironmentVariable('Path', 'User')
$parts = @($current -split ';' | Where-Object { $_ })
if ($parts -notcontains $entry) {
    [Environment]::SetEnvironmentVariable('Path', (($parts + $entry) -join ';'), 'User')
}
'''
        subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                       env=dict(os.environ, CODEX_INTENT_INSTALL_BIN=str(folder)), check=True)
        return
    if str(folder) in os.environ.get('PATH', '').split(os.pathsep):
        return
    block = ('# >>> codex-intent PATH >>>\nexport PATH=' + shlex.quote(str(folder)) +
             ':"$PATH"\n# <<< codex-intent PATH <<<\n')
    profiles = [Path.home() / '.profile', Path.home() / '.bashrc']
    if (Path.home() / '.bash_profile').exists():
        profiles.append(Path.home() / '.bash_profile')
    if Path(os.environ.get('SHELL', '')).name == 'zsh':
        profiles += [Path.home() / '.zprofile', Path.home() / '.zshrc']
    for profile in profiles:
        original = profile.read_text(encoding='utf-8') if profile.exists() else ''
        content = re.sub(r'(?ms)^# >>> codex-intent PATH >>>\n.*?^# <<< codex-intent PATH <<<\n?', '', original)
        profile.write_text(content.rstrip('\n') + '\n\n' + block, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--release-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--install-root', type=Path, default=default_root())
    parser.add_argument('--bin-dir', type=Path, default=default_bin())
    parser.add_argument('--no-path', action='store_true', help='Do not change persistent PATH; used for isolated acceptance.')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    native_target()
    release = args.release_root.resolve(strict=True)
    metadata = verify_release(release)
    if metadata['status'] != 'verified-custom-release':
        raise RuntimeError('The one-command installer only accepts a complete validated release.')
    root, folder = Path(os.path.abspath(args.install_root)), Path(os.path.abspath(args.bin_dir))
    for path in [root, folder]:
        if any(redirected(item) for item in [path, *path.parents]):
            raise RuntimeError('Installation and command paths cannot redirect through links.')
    command_entries(root, folder)
    action = 'install'
    if (root / 'state.json').is_file():
        checked_root(root)
        state = read(root / 'state.json')
        current = verify_release(contained(root, state['current']['release']))
        action = 'status' if (current['version'], current['commit']) == (metadata['version'], metadata['commit']) else 'switch'
        if action == 'status' and current != metadata:
            raise RuntimeError('This release identifier has different accepted contents; no files were replaced.')
    print(('Installing ' if action == 'install' else 'Checking installed ') + metadata['version'] + '...', flush=True)
    if WINDOWS:
        command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                   str(release / 'intent/Maintain-Intent.ps1'), action, '-InstallRoot', str(root)]
    else:
        command = ['bash', str(release / 'intent/maintain-intent'), action, '--install-root', str(root)]
    if action != 'status':
        command += ['--release', str(release)]
    result = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError('Installation was not completed. ' + result.stderr.strip() + '\n' + result.stdout.strip())
    command_entries(root, folder, publish=True)
    if not args.no_path:
        configure_path(folder)
    installed = read(root / 'state.json')
    details = {'status': 'installed', 'version': metadata['version'], 'target': native_target(),
               'root': str(root), 'bin': str(folder), 'selection': installed['current'], 'action': action}
    if args.report:
        write(args.report, details)
    print('Installed ' + metadata['version'] + '.', flush=True)
    print('Start: codex-intent', flush=True)
    print('Manage: codex-intent-maintain status', flush=True)
    if not WINDOWS and str(folder) not in os.environ.get('PATH', '').split(os.pathsep):
        print('Open a new terminal to load PATH, or run: ' + shlex.quote(str(folder / 'codex-intent')), flush=True)


if __name__ == '__main__':
    main()
