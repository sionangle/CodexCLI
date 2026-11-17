import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import threading
import time
from terminal_probe import PtyProcess, REPO, clean
from terminal_platform import WINDOWS, executable, long_command, task_command

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    parser.add_argument('source_run', type=Path)
    args = parser.parse_args()
    source = args.source_run.resolve()
    home = source / 'codex-home'
    cwd = source / 'workspace'
    original = (home / 'config.toml').read_text(encoding='utf-8')
    request = json.loads((source / 'request-1.json').read_text(encoding='utf-8'))
    thread_id = request['client_metadata']['thread_id']
    run = REPO / '.intent-work' / ('resume-probe-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    run.mkdir()
    report = {'status': 'running', 'thread_id': thread_id, 'package': str(args.package.resolve()), 'checks': []}

    def check(intent):
        mode = 'intent' if intent else 'detailed'
        (home / 'config.toml').write_text(original.replace('intent_view = true', f'intent_view = {str(intent).lower()}'), encoding='utf-8')
        env = dict(os.environ, CODEX_HOME=str(home), TERM='xterm-256color')
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY'):
            env.pop(key, None)
        proc = PtyProcess.spawn([str(args.package.resolve() / 'bin' / executable('codex-intent')), 'resume', thread_id,
                                '--no-daemon', '--no-alt-screen'], cwd=str(cwd), env=env, dimensions=(36, 110), backend=0)
        events = queue.Queue()
        output = ''
        def reader():
            try:
                while True:
                    events.put(proc.read(65536))
            except Exception as error:
                events.put(error)
        threading.Thread(target=reader, daemon=True).start()
        def until(predicate):
            nonlocal output
            deadline = time.monotonic() + 60
            while not predicate(clean(output)):
                chunk = events.get(timeout=max(0, deadline - time.monotonic()))
                if isinstance(chunk, Exception):
                    raise chunk
                output += chunk
                (run / f'{mode}.vt').write_text(output, encoding='utf-8')
                if '\x1b[6n' in chunk:
                    proc.write('\x1b[1;1R')
            return clean(output)
        try:
            text = until(lambda value: '已完成隔离显示测试' in value)
            markers = ('RAW_CMD_MARKER', 'RAW_STDOUT_MARKER', 'RAW_STDERR_MARKER', 'RAW_FINISHED_MARKER')
            if intent:
                assert not any(marker in text for marker in markers)
                report['checks'].append('resume-intent-keeps-tools-hidden')
                proc.setwinsize(25, 60)
                proc.write('\x1b[5~')
                proc.write('\x14')
                proc.write('\x1b[H')
                until(lambda value: any(marker in value for marker in markers))
                report['checks'].append('resumed-full-records-retains-source')
            else:
                until(lambda value: any(marker in value for marker in markers))
                report['checks'].append('saved-detailed-mode-shows-execution-history')
        finally:
            (run / f'{mode}.txt').write_text(clean(output), encoding='utf-8')
            proc.close(force=True)
    try:
        check(True)
        check(False)
        report['status'] = 'passed-resume-probe-only'
    except Exception as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        (home / 'config.toml').write_text(original, encoding='utf-8')
        (run / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('Resume diagnostic:', run, flush=True)

if __name__ == '__main__':
    main()
