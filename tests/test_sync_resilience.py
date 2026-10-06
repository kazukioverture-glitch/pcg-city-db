import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from collector import download_termux as download
from collector import sync as transaction
from collector import sync_official_api as official
from collector.validate_snapshot import read, validate_snapshot


class DownloadTests(unittest.TestCase):
    def run_download(self, responses):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        target = Path(self.tmp.name) / 'city_db.json'
        target.write_text('old')
        calls = []
        def curl(command, **kwargs):
            calls.append(command)
            code, status, body = responses.pop(0)
            Path(command[command.index('--output') + 1]).write_bytes(body)
            return subprocess.CompletedProcess(command, code, status, 'test error' if code else '')
        with patch.object(download.subprocess, 'run', side_effect=curl), patch.object(download.time, 'sleep'):
            result = download.download_one('https://example.test', 'city-db.json', target, budget=10)
        return result, target, calls

    def test_success_uses_http1_and_part_file(self):
        result, target, calls = self.run_download([(0, '200', b'{"events": []}')])
        self.assertTrue(result['success'])
        self.assertIn('--http1.1', calls[0])
        self.assertTrue(calls[0][calls[0].index('--output') + 1].endswith('.part'))
        self.assertEqual(json.loads(target.read_text()), {'events': []})

    def test_transport_failure_retries_then_promotes_complete_json(self):
        result, target, calls = self.run_download([(18, '200', b'{'), (0, '200', b'{}')])
        self.assertTrue(result['success'])
        self.assertEqual(len(calls), 2)

    def test_404_is_retried_but_never_adopted(self):
        result, target, calls = self.run_download([(22, '404', b'ngrok offline')] * 3)
        self.assertFalse(result['success'])
        self.assertFalse(target.exists())
        self.assertEqual(len(calls), 3)
        self.assertFalse(target.with_suffix('.json.part').exists())

    def test_truncated_json_retry(self):
        result, target, calls = self.run_download([(0, '200', b'{'), (0, '200', b'{}')])
        self.assertTrue(result['success'])
        self.assertEqual(len(calls), 2)

    def test_html_success_status_rejected(self):
        result, target, _ = self.run_download([(0, '200', b'<html>error</html>')] * 3)
        self.assertFalse(result['success'])
        self.assertFalse(target.exists())

    def test_non_object_json_rejected(self):
        result, target, _ = self.run_download([(0, '200', b'[]')] * 3)
        self.assertFalse(result['success'])

    def test_both_files_attempted_when_one_fails(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(download, 'download_one', side_effect=[
                {'success': False}, {'success': True}]) as one:
            results = download.download_pair('', tmp)
            self.assertEqual(one.call_count, 2)
            self.assertEqual(len(results), 2)
            self.assertEqual(one.call_args_list[0].args[0], download.DEFAULT_URL)

    def test_403_official_is_not_retried(self):
        error = HTTPError('url', 403, 'Forbidden', {}, None)
        with patch.object(official, 'urlopen', side_effect=error) as fetch, patch.object(official.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'unavailable: HTTP 403'):
                official.fetch_bytes('https://example.test')
            self.assertEqual(fetch.call_count, 1)
            sleep.assert_not_called()


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in transaction.DATA_FILES:
            path = self.root / 'data' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(transaction.ROOT / 'data' / name, path)
        self.before = self.contents()

    def contents(self):
        return {name: (self.root / 'data' / name).read_bytes() for name in transaction.DATA_FILES}

    def acquire(self, city_ok=True, decks_ok=True, mutate=None):
        def pair(url, destination):
            destination.mkdir(parents=True)
            for name, ok in [('city_db.json', city_ok), ('city_decks.json', decks_ok)]:
                if ok:
                    shutil.copyfile(self.root / 'data' / name, destination / name)
            if mutate:
                mutate(destination)
            return {'city-db.json': {'success': city_ok}, 'city-decks.json': {'success': decks_ok}}
        return patch.object(transaction, 'download_pair', side_effect=pair)

    def test_both_sources_fail_preserves_every_byte(self):
        with self.acquire(False, False), patch.object(transaction, 'run_official', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Neither source'):
                transaction.sync(self.root)
        self.assertEqual(self.contents(), self.before)

    def test_only_city_downloaded_preserves_every_byte(self):
        with self.acquire(True, False), patch.object(transaction, 'run_official', return_value=False):
            with self.assertRaises(RuntimeError):
                transaction.sync(self.root)
        self.assertEqual(self.contents(), self.before)

    def test_only_decks_downloaded_preserves_every_byte(self):
        with self.acquire(False, True), patch.object(transaction, 'run_official', return_value=False):
            with self.assertRaises(RuntimeError):
                transaction.sync(self.root)
        self.assertEqual(self.contents(), self.before)

    def test_invalid_card_quantity_preserves_every_byte(self):
        def corrupt(directory):
            path = directory / 'city_decks.json'
            value = read(path)
            next(iter(value['decks'].values()))['cards'][0]['count'] += 1
            path.write_text(json.dumps(value))
        with self.acquire(mutate=corrupt), patch.object(transaction, 'run_official', return_value=False):
            with self.assertRaises(RuntimeError):
                transaction.sync(self.root)
        self.assertEqual(self.contents(), self.before)

    def test_termux_success_official_unavailable_publishes_valid_candidate(self):
        with self.acquire(), patch.object(transaction, 'run_official', return_value=False):
            result = transaction.sync(self.root)
        self.assertTrue(result['published'])
        self.assertTrue(result['termux_accepted'])
        self.assertFalse(result['official_accepted'])
        validate_snapshot(read(self.root / 'data/city_db.json'), read(self.root / 'data/city_decks.json'))

    def test_failed_official_partial_write_restores_accepted_termux(self):
        def official_fail(stage, work):
            (stage / 'city_db.json').write_text('broken')
            return False
        with self.acquire(), patch.object(transaction, 'run_official', side_effect=official_fail):
            self.assertTrue(transaction.sync(self.root)['published'])
        self.assertEqual(read(self.root / 'data/city_db.json')['events'], read(transaction.ROOT / 'data/city_db.json')['events'])

    def test_ledger_failure_prevents_db_publication(self):
        with self.acquire(), patch.object(transaction, 'run_official', return_value=False), \
             patch.object(transaction, 'sync_ledger', side_effect=ValueError('ledger invalid')):
            with self.assertRaisesRegex(ValueError, 'ledger invalid'):
                transaction.sync(self.root)
        self.assertEqual(self.contents(), self.before)

    def test_missing_deck_reference_is_rejected(self):
        city = read(self.root / 'data/city_db.json')
        decks = read(self.root / 'data/city_decks.json')
        city['events'][0]['placements'][0]['deck_code'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'Missing referenced deck'):
            validate_snapshot(city, decks)

    def test_identity_replacement_at_equal_count_is_rejected(self):
        city = read(self.root / 'data/city_db.json')
        decks = read(self.root / 'data/city_decks.json')
        altered = copy.deepcopy(city)
        altered['events'][0]['event_id'] = 'replaced'
        with self.assertRaisesRegex(ValueError, 'identity/count decreased'):
            validate_snapshot(altered, decks, city, decks)

    def test_restart_cleans_previous_partial_work(self):
        work = self.root / '.tmp/sync'
        work.mkdir(parents=True)
        (work / 'stale.part').write_text('bad')
        with self.acquire(), patch.object(transaction, 'run_official', return_value=False):
            transaction.sync(self.root)
        self.assertFalse((work / 'stale.part').exists())

    def test_official_timeout_returns_unavailable(self):
        with patch.object(transaction.subprocess, 'run', side_effect=subprocess.TimeoutExpired('official', 180)):
            self.assertFalse(transaction.run_official(self.root, self.root))

    def test_official_only_success_can_publish_valid_candidate(self):
        def official_ok(stage, work):
            work.mkdir(parents=True)
            (work / 'official_audit.json').write_text(json.dumps({'events': [], 'fetch_errors': {}}))
            return True
        with self.acquire(False, False), patch.object(transaction, 'run_official', side_effect=official_ok):
            result = transaction.sync(self.root)
        self.assertTrue(result['published'])
        self.assertFalse(result['termux_accepted'])
        self.assertTrue(result['official_accepted'])

    def test_official_inconsistent_index_cannot_publish(self):
        def official_bad(stage, work):
            work.mkdir(parents=True)
            (work / 'official_audit.json').write_text(json.dumps({'events': [], 'fetch_errors': {}}))
            (stage / 'index.json').write_text('{}')
            return True
        with self.acquire(), patch.object(transaction, 'run_official', side_effect=official_bad):
            with self.assertRaises(subprocess.CalledProcessError):
                transaction.sync(self.root)
        self.assertEqual(self.contents(), self.before)

    def test_publish_write_failure_rolls_back_every_file(self):
        real_copy = shutil.copyfile
        def copy_fail(source, destination, **kwargs):
            if str(destination) == str(self.root / 'data/city_decks.json.part'):
                raise OSError('disk full')
            return real_copy(source, destination, **kwargs)
        with self.acquire(), patch.object(transaction, 'run_official', return_value=False), \
             patch.object(transaction.shutil, 'copyfile', side_effect=copy_fail):
            with self.assertRaisesRegex(OSError, 'disk full'):
                transaction.sync(self.root)
        self.assertEqual(self.contents(), self.before)
