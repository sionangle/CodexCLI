"""Repeatable terminal acceptance entry point. Each probe uses an isolated local mock provider."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from platform_support import digest_file as digest, executable, native_target

REPO = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    parser.add_argument('--stage', choices=['1', '2'], default='2')
    args = parser.parse_args()
    package = args.package.resolve()
    native_target()
    if 'CODEX_SANDBOX_NETWORK_DISABLED' in os.environ:
        raise RuntimeError('Run outside the sandbox without the inherited network skip marker.')
    binary = package / 'bin' / executable('codex-intent')
    before = {str(path.relative_to(package)): digest(path) for path in package.rglob('*') if path.is_file()}
    if not before or not binary.is_file():
        raise RuntimeError('A complete built candidate package is required.')
    run = REPO / '.intent-work' / ('terminal-acceptance-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    run.mkdir(parents=True)
    report = {'status': 'running', 'stage': int(args.stage), 'package': str(package), 'checks': [],
              'package_sha256': before, 'started_at': datetime.now(timezone.utc).isoformat()}

    def probe(name, extra=()):
        result = subprocess.run([sys.executable, str(REPO / 'intent/acceptance' / (name + '.py')),
                                 str(package), *map(str, extra)], cwd=REPO, capture_output=True,
                                encoding='utf-8', errors='replace', env=dict(os.environ, PYTHONUTF8='1'))
        (run / (name + '.stdout.log')).write_text(result.stdout, encoding='utf-8')
        (run / (name + '.stderr.log')).write_text(result.stderr, encoding='utf-8')
        matches = re.findall(r'(?im)^.*diagnostic: (.+)$', result.stdout)
        if not matches:
            raise RuntimeError(f'{name}: no report emitted; exit={result.returncode}; logs={run}')
        probe_dir = Path(matches[-1].strip()).resolve()
        if not probe_dir.is_relative_to(REPO / '.intent-work'):
            raise RuntimeError('Probe reported a directory outside the isolated work area.')
        evidence = json.loads((probe_dir / 'report.json').read_text(encoding='utf-8-sig'))
        report['checks'].append({'name': name, 'exit_code': result.returncode, 'directory': str(probe_dir), 'report': evidence})
        if result.returncode or not evidence['status'].startswith('passed-'):
            raise RuntimeError(f'{name} failed; no candidate can be published: {probe_dir}')
        print(f'PASS: {name}: {probe_dir}', flush=True)
        return probe_dir

    try:
        background = probe('terminal_probe')
        probe('normal_probe')
        probe('failure_probe')
        probe('resume_probe', [background])
        if args.stage == '2':
            probe('v2_terminal_probe')
        after = {str(path.relative_to(package)): digest(path) for path in package.rglob('*') if path.is_file()}
        if before != after:
            raise RuntimeError('Candidate package changed during acceptance.')
        report['status'] = 'passed-terminal-acceptance-only'
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        (run / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Terminal acceptance report: {run}', flush=True)


if __name__ == '__main__':
    main()
