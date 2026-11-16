"""Fault-injection tests for publication and backup invariants; native acceptance is separate."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
import uuid

import managed_runtime as runtime
from upgrade import validation_workspace


REPO = Path(__file__).resolve().parent.parent
if not (REPO / 'intent/smoke_package.py').is_file():
    REPO = REPO.parent  # During development this module lives in .intent-work/phase3.
WORKSPACE = validation_workspace(REPO)
sys.path.insert(0, str(REPO / 'intent'))


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.root = WORKSPACE / ('ut-' + uuid.uuid4().hex[:8])
        assert self.root.resolve().is_relative_to(WORKSPACE)
        self.root.mkdir()
        (self.root / '.intent-install').write_text(runtime.MARKER, encoding='ascii')
        self.old = self.fixture('old', 2)
        self.new = self.fixture('new', 3)
        runtime.initialize(self.root, self.old)
        self.home = self.root / runtime.read(self.root / 'state.json')['current']['home']
        (self.home / 'marker').write_text('old-format-data')
        with closing(sqlite3.connect(self.home / 'fixture.sqlite')) as database, database:
            database.execute('CREATE TABLE original (value TEXT)')
            database.execute("INSERT INTO original VALUES ('old')")
        self.idle = patch.object(runtime, 'quiesce', lambda *_: None)
        self.processes = patch.object(runtime, 'scoped_processes', lambda *_: [])
        self.idle.start()
        self.processes.start()
        self.addCleanup(self.idle.stop)
        self.addCleanup(self.processes.stop)

    def fixture(self, name, revision):
        folder = self.root / name
        (folder / 'package/bin').mkdir(parents=True)
        (folder / 'package/bin/codex-intent.exe').write_bytes(b'UNIT FIXTURE ONLY')
        runtime.write(folder / 'release.json', {'status': 'verified-stage-2-portable',
                      'features': sorted(runtime.REQUIRED_FEATURES), 'version': '0.169.0+intent.' + str(revision),
                      'commit': str(revision) * 40, 'sha256': runtime.hashes(folder)})
        return folder

    def test_migration_failure_cannot_publish_or_modify_original_data(self):
        before = (self.root / 'state.json').read_bytes()
        with patch('smoke_package.initialize_rpc', side_effect=RuntimeError('injected migration failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected migration failure'):
                runtime.transition(self.root, self.new)
        self.assertEqual((self.root / 'state.json').read_bytes(), before)
        self.assertEqual((self.home / 'marker').read_text(), 'old-format-data')
        report = runtime.read(next((self.root / 'transactions').glob('*/report.json')))
        self.assertEqual(report['status'], 'failed-no-publication')
        self.assertTrue(report['backup_sha256'])

    def test_rollback_restores_the_snapshot_instead_of_reusing_new_data(self):
        def migration(_command, _params, environment, _run, _name):
            return {'codexHome': environment['CODEX_HOME']}
        with patch('smoke_package.initialize_rpc', side_effect=migration):
            upgraded = runtime.transition(self.root, self.new)
            current = self.root / upgraded['current']['home']
            (current / 'marker').write_text('incompatible new data')
            with closing(sqlite3.connect(current / 'fixture.sqlite')) as database, database:
                database.execute('DROP TABLE original')
                database.execute('CREATE TABLE changed_schema (new_column TEXT)')
            restored = runtime.transition(self.root, rollback=True)
        restored_home = self.root / restored['current']['home']
        self.assertNotEqual(restored_home, current)
        self.assertEqual((restored_home / 'marker').read_text(), 'old-format-data')
        self.assertEqual((current / 'marker').read_text(), 'incompatible new data')
        with closing(sqlite3.connect(restored_home / 'fixture.sqlite')) as database, database:
            self.assertEqual(database.execute('SELECT value FROM original').fetchall(), [('old',)])

    def test_corrupt_rollback_backup_keeps_current_selection(self):
        def migration(_command, _params, environment, _run, _name):
            return {'codexHome': environment['CODEX_HOME']}
        with patch('smoke_package.initialize_rpc', side_effect=migration):
            upgraded = runtime.transition(self.root, self.new)
            (self.root / upgraded['previous']['home'] / 'marker').write_text('corrupted backup')
            before = (self.root / 'state.json').read_bytes()
            with self.assertRaisesRegex(RuntimeError, 'snapshot integrity failed'):
                runtime.transition(self.root, rollback=True)
        self.assertEqual((self.root / 'state.json').read_bytes(), before)

    def test_state_paths_and_incomplete_releases_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'leaves the managed installation'):
            runtime.contained(self.root, '../escape')
        (self.new / 'package/bin/codex-intent.exe').write_bytes(b'changed after validation')
        before = (self.root / 'state.json').read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'SHA256 differs'):
            runtime.transition(self.root, self.new)
        self.assertEqual((self.root / 'state.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
