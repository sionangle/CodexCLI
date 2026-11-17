"""Local mock-provider / ConPTY diagnostic. No account or real model is used."""
import argparse
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / '.intent-work/terminal-tools'))
from winpty import PtyProcess

def clean(data):
    data = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', data)
    data = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', data)
    return data

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    run = REPO / '.intent-work' / ('failure-terminal-probe-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    home = run / 'codex-home'
    cwd = run / 'workspace'
    home.mkdir(parents=True)
    cwd.mkdir()
    release = cwd / 'release-command'
    command = (
        "Set-Content -LiteralPath 'started-command' -Value 'started'; Write-Output 'RAW_STDOUT_MARKER'; "
        "1..1000 | ForEach-Object { Write-Output ('RAW_STDOUT_MARKER_' + $_) }; "
        "Write-Error 'RAW_STDERR_MARKER' -ErrorAction Continue; "
        "while (-not (Test-Path -LiteralPath 'release-command')) { Start-Sleep -Milliseconds 100 }; "
        "Write-Output 'RAW_FINISHED_MARKER'; exit 7 # RAW_CMD_MARKER " + 'long-command-content ' * 100
    )
    requests = []
    model_count = 0

    def message(ident, text, phase):
        return {'type': 'response.output_item.done', 'item': {'type': 'message', 'role': 'assistant',
                'id': ident, 'phase': phase, 'content': [{'type': 'output_text', 'text': text}]}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[]}')
        def do_POST(self):
            nonlocal model_count
            payload = self.rfile.read(int(self.headers['Content-Length']))
            request = json.loads(payload)
            requests.append(request)
            number = len(requests)
            is_title = request.get('text', {}).get('format', {}).get('type') == 'json_schema'
            if not is_title:
                model_count += 1
            (run / f'request-{number}.json').write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding='utf-8')
            events = [{'type': 'response.created', 'response': {'id': f'response-{number}'}}]
            if is_title:
                events.append(message('title', json.dumps({'title': 'Intent acceptance'}), 'final_answer'))
            elif model_count == 1:
                events += [message('progress', '正在验证长命令与输出的意图显示。', 'commentary'),
                    {'type': 'response.output_item.done', 'item': {'type': 'function_call', 'name': 'exec_command',
                     'call_id': 'intent-command', 'arguments': json.dumps({'cmd': command, 'yield_time_ms': 30000, 'max_output_tokens': 16000})}}]
            else:
                outputs = [str(item.get('output', '')) for item in request.get('input', []) if item.get('type') == 'function_call_output']
                pending = re.search(r'Process running with session ID (\d+)', outputs[-1]) if outputs else None
                if pending:
                    events.append({'type': 'response.output_item.done', 'item': {'type': 'function_call', 'name': 'write_stdin',
                                   'call_id': f'intent-poll-{number}', 'arguments': json.dumps({'session_id': int(pending[1]), 'chars': '', 'yield_time_ms': 1000, 'max_output_tokens': 16000})}})
                else:
                    events.append(message('final', '已完成隔离显示测试。', 'final_answer'))
            events.append({'type': 'response.completed', 'response': {'id': f'response-{number}',
                           'usage': {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20}}})
            body = ''.join('data: ' + json.dumps(e, ensure_ascii=False) + '\n\n' for e in events).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = f'''model = "gpt-5.1-codex"
model_provider = "intent-mock"
approval_policy = "never"
sandbox_mode = "danger-full-access"
check_for_update_on_startup = false
cli_auth_credentials_store = "file"
[model_providers.intent-mock]
name = "Isolated intent acceptance provider"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
[tui]
intent_view = true
animations = false
[features]
code_mode = false
[analytics]
enabled = false
[feedback]
enabled = false
[projects.{json.dumps(str(cwd))}]
trust_level = "trusted"
'''
    (home / 'config.toml').write_text(config, encoding='utf-8')
    environment = dict(os.environ, CODEX_HOME=str(home), TERM='xterm-256color')
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_SANDBOX_NETWORK_DISABLED'):
        environment.pop(name, None)
    cli = args.package.resolve() / 'bin/codex-intent.exe'
    proc = PtyProcess.spawn([str(cli), '--no-daemon', '--no-alt-screen', '执行隔离显示测试。'],
                           cwd=str(cwd), env=environment, dimensions=(36, 110), backend=0)
    events = queue.Queue()
    output = ''
    def reader():
        try:
            while True:
                events.put(proc.read(65536))
        except Exception as error:
            events.put(error)
    threading.Thread(target=reader, daemon=True).start()
    def until(predicate, timeout=90):
        nonlocal output
        deadline = time.monotonic() + timeout
        while not predicate(clean(output)):
            try:
                chunk = events.get(timeout=min(0.2, max(0, deadline - time.monotonic())))
            except queue.Empty:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Terminal state deadline exceeded')
                continue
            if isinstance(chunk, Exception):
                raise chunk
            output += chunk
            (run / 'terminal.vt').write_text(output, encoding='utf-8')
            if '\x1b[6n' in chunk:
                proc.write('\x1b[1;1R')
        return clean(output)
    report = {'status': 'running', 'run': str(run), 'package': str(args.package.resolve()), 'checks': []}
    try:
        text = until(lambda value: (cwd / 'started-command').exists())
        assert not any(marker in text for marker in ('RAW_CMD_MARKER', 'RAW_STDOUT_MARKER', 'RAW_STDERR_MARKER'))
        report['checks'].append('intent-running-hides-raw-markers')
        proc.setwinsize(25, 60)
        proc.write('\x1b[5~')
        release.write_text('release', encoding='utf-8')
        text = until(lambda value: '已完成隔离显示测试' in value)
        assert not any(marker in text for marker in ('RAW_CMD_MARKER', 'RAW_STDOUT_MARKER', 'RAW_STDERR_MARKER'))
        assert '退出码 7' in text and '[查看详情]' in text, 'No actionable failure status and detail entry were painted'
        report['checks'].append('intent-error-summary-visible-without-raw-output')
        assert any('RAW_STDOUT_MARKER' in str(item.get('output', '')) for request in requests for item in request.get('input', []) if item.get('type') == 'function_call_output')
        assert 'Process running with session ID' not in json.dumps(requests[-1]), 'Expected ordinary foreground execution'
        report['checks'].append('ordinary-command-finished-without-background-yield')
        report['checks'].append('model-input-retains-execution-output')
        proc.setwinsize(40, 120)
        proc.write('\x14')
        proc.write('\x1b[H')
        until(lambda value: 'RAW_STDOUT_MARKER' in value or 'RAW_CMD_MARKER' in value, timeout=30)
        report['checks'].append('explicit-full-records-reveals-source')
        report['status'] = 'passed-terminal-probe-only'
    except Exception as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        release.touch(exist_ok=True)
        (run / 'terminal.txt').write_text(clean(output), encoding='utf-8')
        (run / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        proc.close(force=True)
        server.shutdown()
        print('Terminal diagnostic:', run, flush=True)

if __name__ == '__main__':
    main()
