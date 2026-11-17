"""Native Linux lock and PID identity invariants; separate from the daemon acceptance probe."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'linux', 'Linux native primitives')
class LinuxMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='intent-linux-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / 'd/fixture/app-server-daemon'
        self.home.mkdir(parents=True)
        (self.root / '.intent-install').write_text('codex-intent-managed-install-v1')
        (self.root / 'runtime.lock').touch()

    @contextmanager
    def process(self):
        process = subprocess.Popen(['sleep', '60'])
        try:
            yield process
        finally:
            process.terminate()
            process.wait(timeout=10)

    def record(self, pid):
        text = Path(f'/proc/{pid}/stat').read_text()
        ticks = int(text[text.rfind(')') + 2:].split()[19])
        return {'pid': pid, 'processIdentity': {
            'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'startTicks': ticks}}

    def test_pid_record_and_tracked_orphan_keep_birth_identity(self):
        from linux_processes import scoped
        with self.process() as process:
            path = self.home / 'app-server.pid'
            path.write_text(json.dumps(self.record(process.pid)))
            rows = scoped(self.root)
            self.assertEqual([row['ProcessId'] for row in rows], [process.pid])
            path.unlink()
            self.assertEqual(scoped(self.root), [])
            self.assertEqual(scoped(self.root, rows), rows)

    def test_stale_pid_record_cannot_select_reused_pid(self):
        from linux_processes import scoped
        with self.process() as process:
            record = self.record(process.pid)
            path = self.home / 'app-server.pid'
            record['processIdentity']['startTicks'] += 1
            path.write_text(json.dumps(record))
            self.assertEqual(scoped(self.root), [])
            record = self.record(process.pid)
            record['processIdentity']['bootId'] = 'different-boot'
            path.write_text(json.dumps(record))
            self.assertEqual(scoped(self.root), [])

    def test_shared_client_lease_rejects_exclusive_maintenance(self):
        from linux_entry import lease
        code = "import fcntl, sys; s=open(sys.argv[1], 'r+b'); fcntl.flock(s, fcntl.LOCK_EX | fcntl.LOCK_NB)"
        with lease(self.root, False):
            result = subprocess.run([sys.executable, '-c', code, str(self.root / 'runtime.lock')], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'BlockingIOError', result.stderr)
        result = subprocess.run([sys.executable, '-c', code, str(self.root / 'runtime.lock')], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_linked_install_paths_are_rejected_before_writes(self):
        from linux_entry import private_path
        target = self.root / 'actual'
        target.mkdir()
        link = self.root / 'linked'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'redirect'):
            private_path(link / 'child')
        self.assertFalse((target / 'child').exists())

    def test_snapshot_omits_arg0_aliases_but_preserves_other_temporary_data(self):
        from managed_runtime import copy_home
        home = self.root / 'data'
        (home / 'sessions').mkdir(parents=True)
        (home / 'sessions/one.jsonl').write_text('session evidence')
        executable = self.root / 'selected-codex'
        executable.write_text('outside the data home')
        for namespace in ['arg0', 'arg0-linux', 'arg0-windows']:
            aliases = home / 'tmp' / namespace / 'codex-arg0-example'
            aliases.mkdir(parents=True)
            (aliases / 'apply_patch').symlink_to(executable)
        (home / 'tmp/keep.txt').write_text('unrelated temporary data')
        for reset in [False, True]:
            snapshot = self.root / ('snapshot-' + str(reset))
            copy_home(home, snapshot, reset_daemon=reset)
            self.assertEqual((snapshot / 'sessions/one.jsonl').read_text(), 'session evidence')
            self.assertEqual((snapshot / 'tmp/keep.txt').read_text(), 'unrelated temporary data')
            self.assertEqual(sorted(p.name for p in (snapshot / 'tmp').iterdir()), ['keep.txt'])

    def test_snapshot_still_rejects_external_links_outside_arg0(self):
        from managed_runtime import copy_home
        home = self.root / 'data'
        (home / 'sessions').mkdir(parents=True)
        outside = self.root / 'outside.jsonl'
        outside.write_text('must not be copied')
        (home / 'sessions/escape.jsonl').symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, 'leaves the isolated home'):
            copy_home(home, self.root / 'snapshot')


if __name__ == '__main__':
    unittest.main()
