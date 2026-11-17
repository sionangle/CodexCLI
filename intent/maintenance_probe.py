"""Exercise real install/switch/rollback with private data, native binaries and a local model."""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from managed_runtime import SERVICE_IMAGES, hashes, isolated_environment, read, scoped_processes, stamp, write
from upgrade import git, replay, validation_workspace
from platform_support import WINDOWS, executable
from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, CloseConnection, Ping, Request, TextMessage

REPO = Path(__file__).resolve().parent.parent


class RPC:
    def __init__(self, command, home, run):
        self.errors = (run / 'daemon-proxy.log').open('w', encoding='utf-8')
        self.process = subprocess.Popen(list(map(str, command)), env=isolated_environment(home), cwd=run,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors)
        self.events, self.saved, self.counter = queue.Queue(), [], 0
        self.ws, self.ws_lock = WSConnection(ConnectionType.CLIENT), threading.Lock()
        def read_frames():
            fragments = []
            try:
                while data := self.process.stdout.read1(65536):
                    with self.ws_lock:
                        self.ws.receive_data(data)
                        for event in self.ws.events():
                            if isinstance(event, AcceptConnection):
                                self.events.put({'connected': True})
                            elif isinstance(event, TextMessage):
                                fragments.append(event.data)
                                if event.message_finished:
                                    self.events.put(json.loads(''.join(fragments)))
                                    fragments.clear()
                            elif isinstance(event, Ping):
                                self.process.stdin.write(self.ws.send(event.response()))
                                self.process.stdin.flush()
                            elif isinstance(event, CloseConnection):
                                self.events.put(EOFError('Daemon closed the WebSocket'))
                                return
            except Exception as error:
                self.events.put(error)
            finally:
                self.events.put(EOFError('Daemon proxy exited'))
        self.reader = threading.Thread(target=read_frames, daemon=True)
        self.reader.start()
        try:
            # app-server proxy relays raw WebSocket bytes over UDS; it is not JSON-line stdio.
            self.send_event(Request(host='localhost', target='/'))
            self.until(lambda event: event.get('connected'), timeout=15)
            self.call('initialize', {'clientInfo': {'name': 'intent-maintenance-acceptance', 'version': '1'},
                                     'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise

    def send_event(self, event):
        with self.ws_lock:
            self.process.stdin.write(self.ws.send(event))
            self.process.stdin.flush()

    def send(self, value):
        self.send_event(TextMessage(data=json.dumps(value)))

    def until(self, predicate, timeout=60):
        for index, event in enumerate(self.saved):
            if predicate(event):
                return self.saved.pop(index)
        deadline = time.monotonic() + timeout
        while True:
            event = self.events.get(timeout=max(0, deadline - time.monotonic()))
            if isinstance(event, Exception):
                raise event
            if predicate(event):
                return event
            self.saved.append(event)

    def call(self, method, params):
        self.counter += 1
        self.send({'id': self.counter, 'method': method, 'params': params})
        response = self.until(lambda event: event.get('id') == self.counter)
        if 'error' in response:
            raise RuntimeError('Acceptance RPC failed: ' + method + ' ' + str(response['error']))
        return response['result']

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=15)
        self.reader.join(timeout=5)
        self.process.stdout.close()
        self.errors.close()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--previous', type=Path, required=True)
    args = parser.parse_args()
    args.candidate = args.candidate.resolve(strict=True)
    args.previous = args.previous.resolve(strict=True)
    run = REPO / '.intent-work' / ('maintenance-acceptance-' + stamp())
    run.mkdir(parents=True)
    # Keep the actual Windows AF_UNIX socket address short; all data stays in this workspace.
    root = validation_workspace(REPO) / ('mt-' + uuid.uuid4().hex[:8])
    report = {'status': 'running', 'installation': str(root), 'checks': [],
              'candidate_sha256': hashes(args.candidate / 'package'),
              'source_commit': git(REPO, 'rev-parse', 'HEAD').stdout.strip(),
              'source_clean_at_start': not git(REPO, 'status', '--porcelain').stdout}
    report['candidate_source_matches'] = read(args.candidate / 'release.json')['commit'] == report['source_commit']
    has_candidate_tools = (args.candidate / 'intent/maintenance.py').is_file()
    environment = dict(os.environ, PYTHONUTF8='1')
    manager = (args.candidate if has_candidate_tools else REPO) / 'intent' / (
        'Maintain-Intent.ps1' if WINDOWS else 'maintain-intent')
    rpc = None
    gate, requested = threading.Event(), threading.Event()
    response_count = 0

    def command(name, command, success=True, env=None):
        print('CHECK: ' + name, flush=True)
        # A detached Windows daemon can retain nonstandard inherited pipe handles even after
        # the foreground PowerShell process exits. Wait for that process, not pipe EOF from all
        # descendants; files also retain output if the command times out.
        log = run / (name + '.log')
        with log.open('w', encoding='utf-8') as output:
            result = subprocess.run(list(map(str, command)), cwd=run, env=env or environment,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=150)
        if (result.returncode == 0) != success:
            raise RuntimeError(name + ' returned unexpected status; see retained log')
        print('PASS: ' + name, flush=True)
        return log.read_text(encoding='utf-8', errors='replace')

    def maintain(action, *extra, success=True, label=None):
        if not WINDOWS:
            return command(label or action, ['bash', manager, action, '--install-root', root, *extra], success)
        return command(label or action, ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                        '-File', manager, action, '-InstallRoot', root, *extra], success)

    def launch(name, *extra):
        if not WINDOWS:
            return command(name, [root / 'codex-intent', *extra])
        return command(name, ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                              root / 'Run-CodexIntent.ps1', *extra])

    def wait_for_background_exit(label):
        # Thread startup may synchronize plugins using Git. Wait for real idle
        # without killing commands or allowing an unrelated refusal to pass the
        # active-turn assertion below.
        print('CHECK: ' + label, flush=True)
        evidence = {'name': label, 'observed_processes': []}
        report.setdefault('background_process_waits', []).append(evidence)
        deadline, quiet_since = time.monotonic() + 120, None
        observed = {}
        while True:
            pending = [row for row in scoped_processes(root) if row['Name'].lower() not in SERVICE_IMAGES]
            now = time.monotonic()
            if pending:
                quiet_since = None
                observed.update({row['ProcessId']: row for row in pending})
                evidence['observed_processes'] = list(observed.values())
            else:
                quiet_since = now if quiet_since is None else quiet_since
                if now - quiet_since >= 1:
                    print('PASS: ' + label, flush=True)
                    return
            if now >= deadline:
                raise RuntimeError('Owned background processes did not become idle: ' + label)
            time.sleep(0.1)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[]}')

        def do_POST(self):
            nonlocal response_count
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            is_title = request.get('text', {}).get('format', {}).get('type') == 'json_schema'
            if not is_title:
                response_count += 1
                if response_count == 1:
                    requested.set()
                    if not gate.wait(120):
                        raise TimeoutError('Active-task acceptance did not release model response')
            text = '{"title":"Maintenance acceptance"}' if is_title else 'Isolated maintenance task finished.'
            events = [{'type': 'response.created', 'response': {'id': 'maintenance-response'}},
                      {'type': 'response.output_item.done', 'item': {'type': 'message', 'id': 'final',
                       'role': 'assistant', 'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': text}]}},
                      {'type': 'response.completed', 'response': {'id': 'maintenance-response',
                       'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}}]
            body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        maintain('install', '--release', args.previous)
        initial = read(root / 'state.json')
        previous_version = read(args.previous / 'release.json')['version']
        candidate_version = read(args.candidate / 'release.json')['version']
        assert previous_version in launch('initial-version', '--version')
        home = root / initial['current']['home']
        (home / 'acceptance.txt').write_text('pre-upgrade data', encoding='utf-8')
        with closing(sqlite3.connect(home / 'format-probe.sqlite')) as database, database:
            database.execute('CREATE TABLE evidence (value TEXT NOT NULL)')
            database.execute("INSERT INTO evidence VALUES ('old-compatible-data')")
            database.execute('PRAGMA user_version = 1')
        report['checks'].append('real-previous-release-installed-with-isolated-data')

        # Same sharing primitive as the installed launcher; held by an independent real process.
        holder = run / ('hold-launch-lease.ps1' if WINDOWS else 'hold-launch-lease.py')
        if WINDOWS:
            holder.write_text("$lease = [IO.File]::Open($env:INTENT_TEST_LOCK, 'Open', 'Read', 'Read')\n"
                              "try { [Console]::WriteLine('LEASE_READY'); [Console]::ReadLine() | Out-Null } finally { $lease.Dispose() }\n", encoding='utf-8-sig')
            holder_command = ['powershell.exe', '-NoProfile', '-File', str(holder)]
        else:
            holder.write_text("import fcntl, os\nwith open(os.environ['INTENT_TEST_LOCK'], 'rb') as stream:\n"
                              "    fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)\n"
                              "    print('LEASE_READY', flush=True)\n    input()\n", encoding='utf-8')
            holder_command = [sys.executable, str(holder)]
        process = subprocess.Popen(holder_command,
                                   env=dict(environment, INTENT_TEST_LOCK=str(root / 'runtime.lock')),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            ready = queue.Queue()
            threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
            assert ready.get(timeout=20).strip() == 'LEASE_READY'
            before = (root / 'state.json').read_bytes()
            maintain('switch', '--release', args.candidate, success=False, label='active-launcher-refusal')
            assert (root / 'state.json').read_bytes() == before
        finally:
            process.communicate('\n', timeout=15)
        report['checks'].append('active-launcher-prevents-publication')

        broken = run / 'invalid-release'
        broken.mkdir()
        write(broken / 'release.json', read(args.candidate / 'release.json'))
        before = (root / 'state.json').read_bytes()
        maintain('switch', '--release', broken, success=False, label='invalid-candidate-refusal')
        assert (root / 'state.json').read_bytes() == before
        assert previous_version in launch('version-after-refusal', '--version')
        report['checks'].append('invalid-candidate-keeps-current-binary-and-data-selection')

        # Deliberately conflicting upstream fixture, derived from the exact official baseline.
        # This exercises the real Git worktree and replay failure path, without pretending that
        # a fabricated tag is an official upgrade or publishing any fixture files.
        adaptation = run / 'conflict-adaptation'
        adaptation.mkdir()
        fixture = validation_workspace(REPO) / ('ic-' + uuid.uuid4().hex[:8])
        baseline = read(REPO / 'intent/baseline.json')
        patches = git(REPO, 'rev-list', '--reverse', baseline['commit'] + '..HEAD').stdout.splitlines()
        git(REPO, 'worktree', 'add', '--detach', str(fixture), baseline['commit'])
        conflict_file = fixture / 'codex-rs/tui/src/history_cell/mod.rs'
        conflict_file.write_text('// Deliberately incompatible upstream fixture for replay rejection.\n', encoding='utf-8')
        git(fixture, 'add', 'codex-rs/tui/src/history_cell/mod.rs')
        git(fixture, 'commit', '-m', 'test fixture: deliberately conflict with intent display patch')
        write(adaptation / 'report.json', {'status': 'fixture', 'fixture_only': True,
              'baseline': baseline, 'target_commit': git(fixture, 'rev-parse', 'HEAD').stdout.strip(),
              'tag': baseline['tag'], 'custom_revision': 999, 'worktree': str(fixture),
              'patches': [patches[0]], 'applied': []})
        before = (root / 'state.json').read_bytes()
        try:
            replay(adaptation)
            raise AssertionError('The deliberately incompatible replay unexpectedly succeeded')
        except RuntimeError as error:
            assert 'Replay conflict or failure' in str(error)
        assert git(fixture, 'diff', '--name-only', '--diff-filter=U').stdout.strip()
        assert read(adaptation / 'report.json')['status'] == 'failed-no-publication'
        assert (root / 'state.json').read_bytes() == before
        report['checks'].append('real-worktree-replay-conflict-retains-report-and-current-installation')

        maintain('switch', '--release', args.candidate, label='successful-switch')
        changed = read(root / 'state.json')
        assert candidate_version in launch('candidate-version', '--version')
        new_home = root / changed['current']['home']
        assert new_home != home
        assert (new_home / 'acceptance.txt').read_text() == 'pre-upgrade data'
        with closing(sqlite3.connect(new_home / 'format-probe.sqlite')) as database, database:
            database.execute('ALTER TABLE evidence ADD COLUMN new_only TEXT')
            database.execute("UPDATE evidence SET value = 'new-format-data', new_only = 'v2'")
            database.execute('PRAGMA user_version = 99')
        (new_home / 'acceptance.txt').write_text('written after upgrade', encoding='utf-8')
        report['checks'].append('switch-uses-new-data-generation-and-retains-pre-upgrade-snapshot')

        # Start the actual background service with a private local provider, then leave a real
        # turn active. The quiesce command must refuse it without terminating the daemon.
        (new_home / 'config.toml').write_text(f'''model = "gpt-5.1"
model_provider = "intent-mock"
approval_policy = "never"
sandbox_mode = "danger-full-access"
cli_auth_credentials_store = "file"
check_for_update_on_startup = false
[model_providers.intent-mock]
name = "Maintenance acceptance"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
[features]
code_mode = false
[tui]
intent_view = true
[analytics]
enabled = false
[feedback]
enabled = false
''', encoding='utf-8')
        launch('start-candidate-daemon', 'app-server', 'daemon', 'start')
        cli = root / changed['current']['release'] / 'package/bin' / executable('codex-intent')
        rpc = RPC([cli, 'app-server', 'proxy', '--sock', new_home / 'app-server-control/app-server-control.sock'], new_home, run)
        thread = rpc.call('thread/start', {'cwd': str(run), 'approvalPolicy': 'never', 'sandbox': 'danger-full-access'})['thread']['id']
        wait_for_background_exit('thread-startup-background-work-exited')
        turn = rpc.call('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': 'Run isolated idle protection acceptance.', 'textElements': []}]})['turn']['id']
        assert requested.wait(60)
        before = (root / 'state.json').read_bytes()
        refusal = maintain('quiesce', success=False, label='active-daemon-turn-refusal')
        assert 'Daemon cannot be safely quiesced' in refusal, 'Active-turn check must reach the real daemon idle boundary.'
        assert 'a task is active or its state is uncertain; no stop was requested' in refusal
        assert (root / 'state.json').read_bytes() == before
        assert candidate_version in launch('daemon-still-alive', 'app-server', 'daemon', 'version')
        gate.set()
        rpc.until(lambda event: event.get('method') == 'turn/completed' and event['params']['turn']['id'] == turn)
        rpc.close()
        rpc = None
        report['checks'].append('real-active-daemon-task-is-refused-and-finishes-without-interruption')

        wait_for_background_exit('post-turn-background-work-exited')
        report['checks'].append('owned-background-commands-exited-before-idle-rollback')
        maintain('rollback', label='rollback-with-idle-daemon')
        restored = read(root / 'state.json')
        restored_home = root / restored['current']['home']
        assert previous_version in launch('rolled-back-version', '--version')
        assert (restored_home / 'acceptance.txt').read_text() == 'pre-upgrade data'
        with closing(sqlite3.connect(restored_home / 'format-probe.sqlite')) as database, database:
            assert database.execute('PRAGMA user_version').fetchone() == (1,)
            assert database.execute('SELECT value FROM evidence').fetchone() == ('old-compatible-data',)
            assert [row[1] for row in database.execute('PRAGMA table_info(evidence)')] == ['value']
        assert (new_home / 'acceptance.txt').read_text() == 'written after upgrade'
        report['checks'].append('idle-daemon-stopped-and-actual-rollback-restores-old-binary-and-pre-upgrade-format')
        report['checks'].append('newer-data-retained-separately-for-recovery')
        if has_candidate_tools:
            entry = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                     root / 'Maintain-Intent.ps1', 'status'] if WINDOWS else [root / 'maintain-intent', 'status']
            status = json.loads(command('retained-maintenance-entry', entry))
            assert status['current_version'] == previous_version
            assert (root / restored['manager'] / 'maintenance.py').is_file()
            if (args.previous / 'intent/maintenance.py').is_file():
                report['checks'].append('installed-maintenance-entry-survives-rollback')
            else:
                report['checks'].append('installed-maintenance-entry-survives-rollback-to-version-without-tools')
        report['status'] = 'passed-maintenance-acceptance'
    except Exception as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        gate.set()
        if rpc is not None:
            try:
                rpc.close()
            except Exception as error:
                report['cleanup_error'] = repr(error)
                report['status'] = 'failed'
        server.shutdown()
        report['source_commit_at_end'] = git(REPO, 'rev-parse', 'HEAD').stdout.strip()
        report['source_clean_at_end'] = not git(REPO, 'status', '--porcelain').stdout
        if report['status'] == 'passed-maintenance-acceptance' and (
                not report['source_clean_at_start'] or not report['source_clean_at_end']
                or not report['candidate_source_matches'] or not has_candidate_tools
                or report['source_commit_at_end'] != report['source_commit']):
            report['status'] = 'passed-maintenance-diagnostic-only'
        write(run / 'report.json', report)
        print('Maintenance acceptance report: ' + str(run), flush=True)


if __name__ == '__main__':
    main()
