import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

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
        session = Mock()
        session.get.return_value = Mock(status_code=403)
        with patch.object(official, '_get_session', return_value=session), patch.object(official, '_wait_for_request'), patch.object(official.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'unavailable: HTTP 403'):
                official.fetch_bytes('https://example.test')
            self.assertEqual(session.get.call_count, 1)
            sleep.assert_not_called()

    def test_official_transient_failure_recovers(self):
        session = Mock()
        session.get.side_effect = [Mock(status_code=503), Mock(status_code=200, content=b'{}')]
        with patch.object(official, '_get_session', return_value=session), patch.object(official, '_wait_for_request'), patch.object(official.time, 'sleep'):
            self.assertEqual(official.fetch_bytes(official.EVENT_SEARCH_URL), b'{}')
        headers = session.get.call_args.kwargs['headers']
        self.assertEqual(headers['Referer'], official.RESULT_LIST_URL)

    def test_official_not_found_is_not_retried(self):
        session = Mock()
        session.get.return_value = Mock(status_code=404)
        with patch.object(official, '_get_session', return_value=session), patch.object(official, '_wait_for_request'):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 404'):
                official.fetch_bytes('https://example.test')
        self.assertEqual(session.get.call_count, 1)

    def test_official_rate_limit_stops_this_run(self):
        session = Mock()
        session.get.return_value = Mock(status_code=429)
        with patch.object(official, '_get_session', return_value=session), \
             patch.object(official, '_wait_for_request'), patch.object(official.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'unavailable: HTTP 429'):
                official.fetch_bytes('https://example.test')
        self.assertEqual(session.get.call_count, 1)
        sleep.assert_not_called()

    def test_rate_limit_prevents_queued_worker_requests(self):
        with patch.object(official, '_RATE_LIMITED', True):
            with self.assertRaisesRegex(RuntimeError, 'rate limited'):
                official._wait_for_request()

    def test_request_gate_spaces_worker_starts(self):
        with patch.object(official, '_RATE_LIMITED', False), \
             patch.object(official, '_NEXT_REQUEST', 11.0), \
             patch.object(official.time, 'monotonic', side_effect=[10.0, 11.0]), \
             patch.object(official.time, 'sleep') as sleep:
            official._wait_for_request()
            sleep.assert_called_once_with(1.0)
            self.assertEqual(official._NEXT_REQUEST, 12.0)

    def test_official_transport_timeout_is_bounded(self):
        session = Mock()
        session.get.side_effect = official.cffi_requests.RequestsError('timeout')
        with patch.object(official, '_get_session', return_value=session), patch.object(official, '_wait_for_request'), patch.object(official.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'Failed after 2 attempts'):
                official.fetch_bytes('https://example.test', attempts=2, timeout=1)
        self.assertEqual(session.get.call_count, 2)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'TERMUX_BASE_URL': 'https://example.test'})
        env.start()
        self.addCleanup(env.stop)
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
        def official_fail(stage, work, **kwargs):
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
        def official_ok(stage, work, **kwargs):
            work.mkdir(parents=True)
            (work / 'official_audit.json').write_text(json.dumps({'events': [], 'fetch_errors': {}}))
            return True
        with self.acquire(False, False), patch.object(transaction, 'run_official', side_effect=official_ok):
            result = transaction.sync(self.root)
        self.assertTrue(result['published'])
        self.assertFalse(result['termux_accepted'])
        self.assertTrue(result['official_accepted'])

    def test_unconfigured_termux_is_skipped_when_official_succeeds(self):
        def official_ok(stage, work, **kwargs):
            work.mkdir(parents=True)
            (work / 'official_audit.json').write_text(json.dumps({'events': [], 'fetch_errors': {}}))
            return True
        with patch.dict(os.environ, {'TERMUX_BASE_URL': ''}), \
             patch.object(transaction, 'download_pair') as pair, \
             patch.object(transaction, 'run_official', side_effect=official_ok):
            result = transaction.sync(self.root)
        pair.assert_not_called()
        self.assertTrue(result['published'])
        self.assertFalse(result['termux_configured'])
        self.assertTrue(result['official_accepted'])

    def test_unconfigured_termux_and_official_failure_preserves_every_byte(self):
        with patch.dict(os.environ, {'TERMUX_BASE_URL': ''}), \
             patch.object(transaction, 'download_pair') as pair, \
             patch.object(transaction, 'run_official', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Neither source'):
                transaction.sync(self.root)
        pair.assert_not_called()
        self.assertEqual(self.contents(), self.before)

    def test_partial_official_updates_report_incomplete_coverage(self):
        def official_partial(stage, work, **kwargs):
            work.mkdir(parents=True)
            (work / 'official_audit.json').write_text(json.dumps({
                'events': [], 'fetch_errors': {'failed-id': 'HTTP 403'}}))
            return True
        with self.acquire(False, False), patch.object(transaction, 'run_official', side_effect=official_partial):
            result = transaction.sync(self.root)
        self.assertTrue(result['published'])
        self.assertTrue(result['official_accepted'])
        self.assertFalse(result['official_complete'])
        self.assertEqual(result['official_failed_event_ids'], ['failed-id'])
        self.assertEqual(read(self.root / 'data/city_db.json')['events'],
                         read(transaction.ROOT / 'data/city_db.json')['events'])

    def test_official_inconsistent_index_cannot_publish(self):
        def official_bad(stage, work, **kwargs):
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


class PartialOfficialTests(unittest.TestCase):
    def test_valid_update_survives_unrelated_detail_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            data.mkdir()
            for name in transaction.DATA_FILES[:3]:
                shutil.copyfile(transaction.ROOT / 'data' / name, data / name)
            before = read(data / 'city_db.json')
            failed = before['events'][0]
            new = copy.deepcopy(before['events'][1])
            new['event_id'] = 'new-test-event'
            candidates = [{'event_id': new['event_id'], 'date': new['date'], 'category': new['category']},
                          {'event_id': str(failed['event_id']), 'date': failed['date'], 'category': failed['category']}]
            def result(event_id):
                if event_id == new['event_id']:
                    return new
                raise RuntimeError('HTTP 403')
            paths = dict(DATA=data, TMP=root / 'work', CITY_PATH=data / 'city_db.json',
                         DECKS_PATH=data / 'city_decks.json', INDEX_PATH=data / 'index.json',
                         PARTIAL_CITY_PATH=root / 'work/city.json',
                         PARTIAL_DECKS_PATH=root / 'work/decks.json', CHANGED_FLAG=root / 'work/changed')
            with patch.multiple(official, **paths), patch('sys.argv', ['sync_official_api']), \
                 patch.object(official, 'discover_recent_city_events', return_value=candidates), \
                 patch.object(official, 'result_event_from_api', side_effect=result):
                self.assertEqual(official.main(), 0)
            after = read(data / 'city_db.json')
            self.assertEqual(len(after['events']), len(before['events']) + 1)
            self.assertEqual(next(e for e in after['events'] if e['event_id'] == failed['event_id']), failed)
            validate_snapshot(after, read(data / 'city_decks.json'), before, read(transaction.ROOT / 'data/city_decks.json'))
            self.assertIn(str(failed['event_id']), read(root / 'work/official_audit.json')['fetch_errors'])

    def test_all_detail_failures_leave_files_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            data.mkdir()
            for name in transaction.DATA_FILES[:3]:
                shutil.copyfile(transaction.ROOT / 'data' / name, data / name)
            before = {name: (data / name).read_bytes() for name in transaction.DATA_FILES[:3]}
            candidates = [{'event_id': 'failed', 'date': '2026-10-06', 'category': 'OPEN'}]
            paths = dict(DATA=data, TMP=root / 'work', CITY_PATH=data / 'city_db.json',
                         DECKS_PATH=data / 'city_decks.json', INDEX_PATH=data / 'index.json',
                         PARTIAL_CITY_PATH=root / 'work/city.json',
                         PARTIAL_DECKS_PATH=root / 'work/decks.json', CHANGED_FLAG=root / 'work/changed')
            with patch.multiple(official, **paths), patch('sys.argv', ['sync_official_api']), \
                 patch.object(official, 'discover_recent_city_events', return_value=candidates), \
                 patch.object(official, 'result_event_from_api', side_effect=RuntimeError('HTTP 403')):
                with self.assertRaisesRegex(RuntimeError, 'without usable updates'):
                    official.main()
            self.assertEqual(before, {name: (data / name).read_bytes() for name in transaction.DATA_FILES[:3]})
