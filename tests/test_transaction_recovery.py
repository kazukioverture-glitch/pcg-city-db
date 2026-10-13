"""Small offline fault injection; never fetch or modify production data."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collector import transaction as tx
from collector import sync as sync_module


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stage = self.root / 'stage'
        for directory, text in ((self.root / 'data', 'old'), (self.stage, 'new')):
            for name in tx.DATA_FILES:
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text + name)
        self.before = self.contents()

    def contents(self):
        return {name: (self.root / 'data' / name).read_bytes() for name in tx.DATA_FILES}

    def test_unchanged_publication_writes_nothing(self):
        with tx.collection_lock(self.root), patch.object(tx, 'durable_copy') as copy:
            self.assertEqual(tx.publish(self.root, self.root / 'data'), [])
            copy.assert_not_called()

    def test_exception_rolls_back_all_changed_files(self):
        original = tx.replace_file
        def fail(source, target):
            if Path(source) == self.stage / 'city_decks.json':
                raise OSError('injected failure')
            original(source, target)
        with tx.collection_lock(self.root), patch.object(tx, 'replace_file', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'injected'):
                tx.publish(self.root, self.stage)
        self.assertEqual(self.before, self.contents())
        self.assertFalse((self.root / '.tmp/publication.pending').exists())

    def test_process_death_restores_every_file_and_releases_lock(self):
        code = '''
import os,sys
from pathlib import Path
from collector import transaction as tx
root=Path(sys.argv[1])
original=tx.replace_file
def stop(source,target):
    original(source,target)
    os._exit(77)
tx.replace_file=stop
with tx.collection_lock(root):
    tx.publish(root,root/'stage')
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.root)])
        self.assertEqual(result.returncode, 77)
        self.assertNotEqual(self.before, self.contents())
        with tx.collection_lock(self.root):
            self.assertTrue(tx.recover(self.root))
            self.assertFalse(tx.recover(self.root))
        self.assertEqual(self.before, self.contents())

    def test_corrupt_journal_stops_without_partial_restore(self):
        pending = self.root / '.tmp/publication.pending'
        pending.mkdir(parents=True)
        name = tx.DATA_FILES[0]
        (pending / name).write_bytes(b'broken')
        (pending / 'manifest.json').write_text(json.dumps({name: hashlib.sha256(b'old').hexdigest()}))
        with self.assertRaisesRegex(ValueError, 'Invalid rollback backup'):
            tx.recover(self.root)
        self.assertEqual(self.before, self.contents())

    def test_double_run_fails_before_touching_work(self):
        work = self.root / '.tmp/sync'
        work.mkdir(parents=True)
        sentinel = work / 'in-progress'
        sentinel.write_text('keep')
        with tx.collection_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, 'Another collector'):
                sync_module.sync(self.root)
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_success_has_no_pending_rollback(self):
        with tx.collection_lock(self.root):
            self.assertEqual(tx.publish(self.root, self.stage), list(tx.DATA_FILES))
            self.assertFalse(tx.recover(self.root))
        self.assertEqual(self.contents(), {n: (self.stage / n).read_bytes() for n in tx.DATA_FILES})


class RuntimeGuardTests(unittest.TestCase):
    def test_github_without_feed_never_uses_direct_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in tx.DATA_FILES:
                path = root / 'data' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}')
            with (patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'TERMUX_BASE_URL': ''}),
                  patch.object(sync_module, 'validate_snapshot'),
                  patch.object(sync_module, 'run_official') as official):
                with self.assertRaisesRegex(RuntimeError, 'required on GitHub'):
                    sync_module.sync(root)
                official.assert_not_called()
