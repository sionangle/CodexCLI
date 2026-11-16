"""Real V2 workers and ConPTY with a deterministic local model. No accounts or remote calls."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
from terminal_probe import PtyProcess, REPO, clean
import pyte
from wcwidth import wcwidth


def message(ident, text, phase='commentary'):
    return {'type': 'response.output_item.done', 'item': {'type': 'message', 'role': 'assistant',
            'id': ident, 'phase': phase, 'content': [{'type': 'output_text', 'text': text}]}}


def call(ident, name, arguments, namespace=None):
    item = {'type': 'function_call', 'name': name, 'call_id': ident,
            'arguments': json.dumps(arguments, ensure_ascii=False)}
    if namespace:
        item['namespace'] = namespace
        if namespace == 'collaboration' and name in ['spawn_agent', 'followup_task', 'send_message']:
            # The upstream router only accepts explicit [] as plaintext. Omitted metadata is
            # conservatively encrypted and must never be surfaced as a task description.
            item['encrypted_function_args'] = []
    return {'type': 'response.output_item.done', 'item': item}


class Terminal:
    def __init__(self, args, cwd, env, run, label):
        self.proc = PtyProcess.spawn(args, cwd=str(cwd), env=env, dimensions=(42, 120), backend=0)
        self.screen = pyte.Screen(120, 42)
        self.stream = pyte.Stream(self.screen)
        self.events = queue.Queue()
        self.output = ''
        self.closed = False
        self.run, self.label = run, label
        def read():
            try:
                while True:
                    self.events.put(self.proc.read(65536))
            except Exception as error:
                self.events.put(error)
        threading.Thread(target=read, daemon=True).start()

    def text(self):
        # pyte 0.8.2 display indexes data[0] even for an orphaned wide-character stub left by
        # resize/insert operations. Render such empty cells as blanks without dropping VT data.
        lines = []
        for y in range(self.screen.lines):
            cells, skip_stub = [], False
            for x in range(self.screen.columns):
                if skip_stub:
                    skip_stub = False
                    continue
                value = self.screen.buffer[y][x].data
                if not value:
                    cells.append(' ')
                    continue
                cells.append(value)
                skip_stub = wcwidth(value[0]) == 2
            lines.append(''.join(cells))
        return '\n'.join(lines)

    def until(self, predicate, timeout=90):
        deadline = time.monotonic() + timeout
        while not predicate(self.text()):
            chunk = self.events.get(timeout=max(0, deadline - time.monotonic()))
            if isinstance(chunk, Exception):
                raise chunk
            self.output += chunk
            self.stream.feed(chunk)
            (self.run / f'{self.label}.vt').write_text(self.output, encoding='utf-8')
            (self.run / f'{self.label}-screen.txt').write_text(self.text(), encoding='utf-8')
            if '\x1b[6n' in chunk:
                self.proc.write('\x1b[1;1R')
        return self.text()

    def resize(self, rows, columns):
        self.screen.resize(rows, columns)
        self.proc.setwinsize(rows, columns)

    def save(self, name):
        (self.run / (name + '.txt')).write_text(self.text(), encoding='utf-8')

    def close(self):
        if self.closed:
            return
        self.closed = True
        (self.run / f'{self.label}-all.txt').write_text(clean(self.output), encoding='utf-8')
        self.proc.close(force=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    run = REPO / '.intent-work' / ('v2-terminal-probe-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    home, cwd = run / 'codex-home', run / 'workspace'
    home.mkdir(parents=True)
    cwd.mkdir()
    # The legacy gpt-5.1 fallback does not advertise V2 delegation to children. Use an explicit
    # local mock model based on the bundled V2 catalog, exposing direct tools for deterministic SSE.
    bundled = json.loads((REPO / 'codex-rs/models-manager/models.json').read_text(encoding='utf-8'))
    mock_model = next(model for model in bundled['models'] if model['multi_agent_version'] == 'v2')
    mock_model.update(slug='intent-v2-mock', display_name='Local V2 acceptance', tool_mode='direct')
    catalog = home / 'mock-models.json'
    catalog.write_text(json.dumps({'models': [mock_model]}, ensure_ascii=False), encoding='utf-8')
    gates = {name: threading.Event() for name in ['fail_nested', 'interrupt', 'final']}
    lock = threading.Lock()
    counts, turns, requests, threads = {}, {}, [], {}

    def command(name):
        return (f"Set-Content -LiteralPath 'started-{name}' -Value 'started'; "
                f"Write-Output 'RAW_V2_STDOUT_{name}'; Write-Error 'RAW_V2_STDERR_{name}' -ErrorAction Continue; "
                f"while (-not (Test-Path -LiteralPath 'release-{name}')) {{ Start-Sleep -Milliseconds 100 }}; "
                f"Write-Output 'RAW_V2_DONE_{name}' # RAW_V2_COMMAND " + 'long-script ' * 100)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[]}')

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            meta = request.get('client_metadata', {})
            details = json.loads(meta.get('x-codex-turn-metadata', '{}'))
            agent = details.get('agent_name', '/root')
            turn_id = meta.get('turn_id', 'unknown')
            is_title = request.get('text', {}).get('format', {}).get('type') == 'json_schema'
            with lock:
                requests.append(request)
                number = len(requests)
                (run / f'request-{number}.json').write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding='utf-8')
                if not is_title:
                    threads[agent] = meta.get('thread_id')
                    key = (agent, turn_id)
                    counts[key] = counts.get(key, 0) + 1
                    count = counts[key]
                    if turn_id not in turns.setdefault(agent, []):
                        turns[agent].append(turn_id)
                    generation = len(turns[agent])
            events = []
            if is_title:
                events.append(message('title', '{"title":"V2 面板验收"}', 'final_answer'))
            elif agent == '/root':
                stage = sum(count for (path, _), count in counts.items() if path == '/root')
                if stage == 1:
                    events += [message('root-progress', '正在验证并行子任务的进度和状态。'),
                        call('spawn-freeze', 'spawn_agent', {'task_name': 'freeze_docs', 'message': '核实冻结逻辑与文档对应关系。', 'fork_turns': 'none'}, 'collaboration'),
                        call('spawn-runtime', 'spawn_agent', {'task_name': 'runtime_state', 'message': '核对运行状态与计时来源。', 'fork_turns': 'none'}, 'collaboration')]
                elif stage == 2:
                    if not gates['interrupt'].wait(90):
                        raise TimeoutError('terminal never authorized interruption stage')
                    events.append(call('interrupt-runtime', 'interrupt_agent', {'target': '/root/runtime_state'}, 'collaboration'))
                elif stage == 3:
                    events.append(call('redispatch-freeze', 'followup_task', {'target': '/root/freeze_docs', 'message': '复查冻结逻辑的边界条件。'}, 'collaboration'))
                else:
                    if not gates['final'].wait(90):
                        raise TimeoutError('terminal never authorized final stage')
                    events.append(message(f'root-final-{number}', '已完成 V2 面板隔离验收。', 'final_answer'))
            elif agent.endswith('/nested_error'):
                if count == 1:
                    events += [message('nested-progress', '已定位嵌套任务的事件来源。'),
                        call('nested-trigger', 'exec_command', {'cmd': "Write-Output 'RAW_V2_NESTED_OUTPUT'", 'yield_time_ms': 1000})]
                else:
                    if not gates['fail_nested'].wait(90):
                        raise TimeoutError('terminal never authorized failure stage')
                    body = b'{"error":{"type":"server_error","message":"ISOLATED_V2_FAILURE"}}'
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            else:
                name = 'freeze-1' if agent.endswith('/freeze_docs') and generation == 1 else (
                    'freeze-2' if agent.endswith('/freeze_docs') else 'runtime')
                if count == 1:
                    progress = {'freeze-1': '正在查找笛卡尔规划冻结逻辑及相关 G 代码文档。',
                                'freeze-2': '正在复查冻结逻辑的边界条件。', 'runtime': '正在核对状态事件与耗时记录。'}[name]
                    events.append(message('progress-' + name, progress))
                    if name == 'freeze-1':
                        events.append(call('spawn-nested', 'spawn_agent', {'task_name': 'nested_error', 'message': '验证嵌套任务失败事件。', 'fork_turns': 'none'}, 'collaboration'))
                    events.append(call('command-' + name, 'exec_command', {'cmd': command(name), 'yield_time_ms': 1000, 'max_output_tokens': 4000}))
                else:
                    outputs = [str(item.get('output', '')) for item in request.get('input', []) if item.get('type') == 'function_call_output']
                    pending = re.search(r'Process running with session ID (\d+)', outputs[-1]) if outputs else None
                    if pending:
                        events.append(call(f'poll-{name}-{number}', 'write_stdin', {'session_id': int(pending[1]), 'chars': '', 'yield_time_ms': 1000, 'max_output_tokens': 4000}))
                    else:
                        result = '已完成首次冻结逻辑核查。' if name == 'freeze-1' else '已完成边界条件复查。'
                        events.append(message('result-' + name, result, 'final_answer'))
            events.insert(0, {'type': 'response.created', 'response': {'id': f'response-{number}'}})
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
    (home / 'config.toml').write_text(f'''model = "intent-v2-mock"
model_catalog_json = {json.dumps(str(catalog))}
model_provider = "intent-mock"
approval_policy = "never"
sandbox_mode = "danger-full-access"
check_for_update_on_startup = false
cli_auth_credentials_store = "file"
[model_providers.intent-mock]
name = "Isolated V2 acceptance provider"
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
multi_agent_v2 = true
[analytics]
enabled = false
[feedback]
enabled = false
[projects.{json.dumps(str(cwd))}]
trust_level = "trusted"
''', encoding='utf-8')
    env = dict(os.environ, CODEX_HOME=str(home), TERM='xterm-256color')
    for key in ['OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_SANDBOX_NETWORK_DISABLED']:
        env.pop(key, None)
    cli = str(args.package.resolve() / 'bin/codex-intent.exe')
    terminal = Terminal([cli, '--no-daemon', '--no-alt-screen', '执行 V2 子任务面板隔离验收。'], cwd, env, run, 'live')
    report = {'status': 'running', 'package': str(args.package.resolve()), 'checks': []}
    def row(text, name, status):
        return any(name in line and '· ' + status in line for line in text.splitlines())
    try:
        terminal.until(lambda text: '子任务 (3)' in text and '核实冻结逻辑' in text and '正在核对状态事件' in text and '嵌套任务' in text)
        terminal.save('parallel-running')
        assert not any(token in clean(terminal.output) for token in ['RAW_V2_', 'Started `/root/'])
        report['checks'].append('parallel-and-nested-v2-purpose-real-progress-no-raw-tools')
        terminal.resize(36, 92)
        terminal.proc.write('\x1b[5~')
        gates['fail_nested'].set()
        (cwd / 'release-freeze-1').touch()
        terminal.until(lambda text: row(text, 'freeze docs', '完成') and row(text, 'nested error', '失败'))
        terminal.save('complete-and-failed')
        frozen = next(line for line in terminal.text().splitlines() if 'freeze docs' in line and '· 完成' in line)
        frozen_duration = re.search(r'\d+分\d+秒', frozen).group(0)
        previous_screen = terminal.text()
        terminal.until(lambda text: text != previous_screen)
        still_done = next(line for line in terminal.text().splitlines() if 'freeze docs' in line and '· 完成' in line)
        assert re.search(r'\d+分\d+秒', still_done).group(0) == frozen_duration
        report['checks'].append('completed-duration-remains-frozen-across-updates')
        report['checks'].append('completed-and-failed-status-from-real-turns')
        gates['interrupt'].set()
        terminal.until(lambda text: row(text, 'runtime state', '中断') and '正在复查冻结逻辑' in text and row(text, 'freeze docs', '运行中'))
        terminal.save('interrupted-and-redispatched')
        assert '复查冻结逻辑的边界条件' in terminal.text()
        report['checks'].append('interruption-and-redispatch-reuse-row-with-new-progress')
        (cwd / 'release-freeze-2').touch()
        terminal.until(lambda text: row(text, 'freeze docs', '完成') and '已完成边界条件复查' in text)
        terminal.save('redispatched-complete')
        gates['final'].set()
        terminal.until(lambda text: '已完成 V2 面板隔离验收' in text)
        assert 'RAW_V2_' not in clean(terminal.output)
        report['checks'].append('resize-scroll-and-all-completed-output-keeps-raw-hidden')
        # Send an explicit paste followed by Enter. A fast plain-text burst plus Enter is
        # intentionally treated as a multiline paste by the upstream composer.
        terminal.proc.write('\x1b[200~/subagents\x1b[201~')
        terminal.until(lambda text: any(line.startswith('› /subagents') for line in text.splitlines()))
        terminal.proc.write('\r')
        terminal.until(lambda text: 'Enter 打开子任务' in text or 'Subagents' in text)
        terminal.save('explicit-agent-details')
        assert 'RAW_V2_' not in clean(terminal.output)
        report['checks'].append('explicit-agent-picker-keeps-raw-commands-hidden')
        terminal.proc.write('\x1b[B\r')
        terminal.until(lambda text: 'Subagents' not in text and '已完成边界条件复查' in text)
        terminal.save('child-activity-preview')
        assert 'RAW_V2_' not in clean(terminal.output)
        report['checks'].append('opened-child-activity-preview-keeps-raw-commands-hidden')
        terminal.close()
        terminal = Terminal([cli, 'resume', threads['/root'], '--no-daemon', '--no-alt-screen'], cwd, env, run, 'resumed')
        terminal.until(lambda text: row(text, 'freeze docs', '完成') and row(text, 'runtime state', '中断') and row(text, 'nested error', '失败'))
        terminal.save('resumed-statuses')
        assert 'RAW_V2_' not in clean(terminal.output)
        report['checks'].append('resume-preserves-terminal-statuses-and-intent-policy')
        report['status'] = 'passed-v2-terminal-probe-only'
    except Exception as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        for gate in gates.values():
            gate.set()
        for name in ['freeze-1', 'freeze-2', 'runtime']:
            (cwd / ('release-' + name)).touch()
        terminal.close()
        server.shutdown()
        report['threads'] = threads
        (run / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('V2 terminal diagnostic:', run, flush=True)


if __name__ == '__main__':
    main()
