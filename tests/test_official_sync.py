import copy
import json
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from collector import sync_official_api as official
from collector.merge_termux_snapshot import merge_snapshot


class OfficialSyncTests(unittest.TestCase):
    def fixture(self):
        city = {'season': '2027-S1', 'updated_at': '2026-10-05T10:00:00+09:00',
                'events': [{'event_id': '1', 'date': '2026-10-04', 'placements': [
                    {'player_id': 'a', 'rank': 1}, {'player_id': 'b', 'rank': 5}]}]}
        decks = {'updated_at': city['updated_at'], 'decks': {'code': {'total_cards': 60,
                 'cards': [{'count': 60}], 'usages': [{'event_id': '1', 'player_id': 'a', 'rank': 1}]}}}
        return city, decks

    def test_stale_smaller_snapshot_preserves_records_and_usages(self):
        city, decks = self.fixture()
        new_city, new_decks = copy.deepcopy(city), copy.deepcopy(decks)
        new_city['updated_at'] = new_decks['updated_at'] = '2026-10-04T10:00:00+09:00'
        new_city['events'][0]['placements'] = [{'player_id': 'a', 'rank': 8}]
        new_decks['decks']['code']['usages'] = []
        merged_city, merged_decks = merge_snapshot(city, decks, new_city, new_decks)
        self.assertEqual(merged_city['events'][0]['placements'], city['events'][0]['placements'])
        self.assertEqual(merged_decks['decks']['code']['usages'], decks['decks']['code']['usages'])

    def test_newer_snapshot_cannot_delete_old_placement(self):
        city, decks = self.fixture()
        new_city = copy.deepcopy(city)
        new_city['updated_at'] = '2026-10-05T12:00:00+09:00'
        new_city['events'][0]['placements'] = [{'player_id': 'a', 'rank': 2}]
        merged, _ = merge_snapshot(city, decks, new_city, decks)
        self.assertEqual(merged['placement_count'], 2)
        self.assertEqual(merged['events'][0]['placements'][0]['rank'], 2)

    def test_timestamp_comparison_normalizes_timezones(self):
        city, decks = self.fixture()
        new_city = copy.deepcopy(city)
        new_city['updated_at'] = '2026-10-05T02:00:00+00:00'
        new_city['events'][0]['placements'][0]['rank'] = 2
        merged, _ = merge_snapshot(city, decks, new_city, decks)
        self.assertEqual(merged['events'][0]['placements'][0]['rank'], 2)

    def test_real_card_quantity_is_validated(self):
        city, decks = self.fixture()
        broken = copy.deepcopy(decks)
        broken['decks']['code']['cards'][0]['count'] = 59
        with self.assertRaisesRegex(ValueError, '60-card'):
            merge_snapshot(city, decks, city, broken)

    def test_top8_ties_are_paged_beyond_eight_rows(self):
        def row(i, rank):
            return {'player_id': str(i), 'rank': rank, 'point': 25, 'name': 'name', 'area': '愛知県'}
        payloads = [{'event': {'eventDate': {'date': '2026-10-04'}}, 'count': 12,
                     'results': [row(i, 1 if i == 0 else 5) for i in range(8)]},
                    {'count': 12, 'results': [row(8, 5), row(9, 5), row(10, 9), row(11, 9)]}]
        with patch.object(official, 'fetch_json', side_effect=payloads) as fetch:
            result = official.result_event_from_api('1')
        self.assertEqual(result['placement_count'], 10)
        self.assertEqual(fetch.call_args_list[1].args[1]['offset'], 8)

    def test_unpublished_result_is_empty(self):
        with patch.object(official, 'fetch_json', return_value={'results': [], 'count': 0}):
            self.assertIsNone(official.result_event_from_api('1'))

    def test_cli_records_official_top8_coverage(self):
        city, decks = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('city', city), ('decks', decks)]:
                (root / f'{name}.json').write_text(json.dumps(value))
            ledger = root / 'ledger.json'
            run = subprocess.run([sys.executable, '-m', 'analysis', 'sync-ledger',
                '--city', str(root / 'city.json'), '--decks', str(root / 'decks.json'),
                '--ledger', str(ledger), '--fetch-status', 'success', '--coverage-scope', 'top8',
                '--source-url', official.RESULT_LIST_URL], cwd=official.ROOT, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            observation = json.loads(ledger.read_text())['records'][0]['observations'][-1]
            self.assertEqual(observation['coverage_scope'], 'top8')
            self.assertEqual(observation['top8_slots_retrieved'], 2)

    def test_deck_parse_requires_sixty(self):
        page = '<table class="KSTable"><th>ポケモン</th><tr><a id="cardName_1">カード</a><span>60枚</span></tr></table>'
        with patch.object(official, 'fetch_bytes', return_value=page.encode()):
            self.assertEqual(official.parse_deck('x')['total_cards'], 60)
        with patch.object(official, 'fetch_bytes', return_value=page.replace('60枚', '59枚').encode()):
            with self.assertRaises(ValueError):
                official.parse_deck('x')

    def test_official_embedded_deck_inputs_and_names(self):
        page = """<input name="deck_pke" value="1_4_1"><input name="deck_ene" value="2_56_1">
        <script>PCGDECK.searchItemNameAlt[1]='ドラパルトex';
        PCGDECK.searchItemNameAlt[2]='基本超エネルギー';</script>"""
        with patch.object(official, 'fetch_bytes', return_value=page.encode()):
            deck = official.parse_deck('x')
        self.assertEqual(deck['total_cards'], 60)
        self.assertEqual(deck['cards'][0]['name'], 'ドラパルトex')
        self.assertEqual(deck['cards'][0]['section'], 'ポケモン (4)')

    def test_deck_failure_leaves_all_main_files_unchanged(self):
        city, decks = self.fixture()
        event = {'event_id': '2', 'date': '2026-10-04', 'placements': [{'rank': 1, 'player_id': 'c', 'deck_code': 'bad'}], 'placement_count': 1}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / name for name in ('city.json', 'decks.json', 'index.json')]
            for path, value in zip(paths, (city, decks, {'status': 'ok'})):
                path.write_text(json.dumps(value))
            before = [p.read_bytes() for p in paths]
            with patch.multiple(official, CITY_PATH=paths[0], DECKS_PATH=paths[1], INDEX_PATH=paths[2],
                                TMP=root / '.tmp', PARTIAL_CITY_PATH=root / 'partial_city',
                                PARTIAL_DECKS_PATH=root / 'partial_decks', CHANGED_FLAG=root / 'changed'), \
                 patch('sys.argv', ['sync']), \
                 patch.object(official, 'discover_recent_city_events', return_value=[{'event_id': '2', 'date': '2026-10-04', 'category': 'オープン'}]), \
                 patch.object(official, 'result_event_from_api', return_value=event), \
                 patch.object(official, 'parse_deck', side_effect=ValueError('59 cards')):
                with self.assertRaisesRegex(RuntimeError, 'no files written'):
                    official.main()
            self.assertEqual([p.read_bytes() for p in paths], before)
