"""Real Git history checks for native cache reuse, including native-file rename escapes."""
from pathlib import Path
import tempfile
import unittest

from cache_state import can_reuse_native_artifacts
from upgrade import git

REPO = Path(__file__).resolve().parent.parent


class CacheIdentityTests(unittest.TestCase):
    def setUp(self):
        work = REPO / '.intent-work'
        work.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='cache-proof-', dir=work)
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to(work.resolve())
        self.addCleanup(self.temporary.cleanup)
        git(self.root, 'init', '-q')
        self.native = self.root / 'codex-rs/core/src/lib.rs'
        self.native.parent.mkdir(parents=True)
        self.native.write_text('// original native source\n')
        self.original = self.commit()
        self.identity = dict(repository=str(self.root), commit=self.original,
                             target='test-target', toolchain='pinned', lockfile='pinned')

    def commit(self):
        git(self.root, 'add', '--all')
        git(self.root, '-c', 'user.name=Cache Test', '-c', 'user.email=cache@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '-qm', 'isolated fixture')
        return git(self.root, 'rev-parse', 'HEAD').stdout.strip()

    def change(self, path):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text('// changed fixture\n')
        return dict(self.identity, commit=self.commit())

    def test_runtime_only_change_keeps_native_artifacts(self):
        changed = self.change('intent/managed_runtime.py')
        self.assertTrue(can_reuse_native_artifacts(self.root, self.identity, changed, False))

    def test_native_or_build_driver_changes_require_invalidation(self):
        changed = self.change('codex-rs/core/src/lib.rs')
        self.assertFalse(can_reuse_native_artifacts(self.root, self.identity, changed, False))
        self.native.write_text('// original native source\n')
        changed = self.change('intent/build_package.py')
        self.assertFalse(can_reuse_native_artifacts(self.root, self.identity, changed, False))

    def test_rename_from_native_source_to_allowed_script_is_not_hidden(self):
        destination = self.root / 'intent/managed_runtime.py'
        destination.parent.mkdir()
        self.native.rename(destination)
        changed = dict(self.identity, commit=self.commit())
        self.assertFalse(can_reuse_native_artifacts(self.root, self.identity, changed, False))

    def test_dirty_source_or_changed_environment_never_reuses(self):
        changed = self.change('intent/managed_runtime.py')
        self.assertFalse(can_reuse_native_artifacts(self.root, self.identity, changed, True))
        for field in ['repository', 'target', 'toolchain', 'lockfile']:
            with self.subTest(field=field):
                self.assertFalse(can_reuse_native_artifacts(
                    self.root, self.identity, dict(changed, **{field: 'different'}), False))

    def test_missing_or_invalid_provenance_requires_invalidation(self):
        changed = self.change('intent/cache_state.py')
        for old in [None, dict(self.identity, commit='-not-a-commit'),
                    dict(self.identity, commit='f' * 40)]:
            with self.subTest(previous=old):
                self.assertFalse(can_reuse_native_artifacts(self.root, old, changed, False))


if __name__ == '__main__':
    unittest.main()
