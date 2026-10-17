import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from analysis.weekly_delivery import latest_closed_week, missingness, generate
from collector.validate_snapshot import write_candidate


class WeeklyDeliveryTests(unittest.TestCase):
    def test_jst_cutoff_never_uses_active_week(self):
        self.assertEqual(latest_closed_week(datetime.fromisoformat('2026-10-18T14:59:00+00:00')), 'W03')
        self.assertEqual(latest_closed_week(datetime.fromisoformat('2026-10-18T15:00:00+00:00')), 'W04')
        with self.assertRaises(ValueError):
            latest_closed_week(datetime(2026, 10, 20))

    def test_small_missingness_is_conditional_and_not_national_coverage(self):
        city = {'events': [{'event_id': 'a', 'date': '2026-10-11', 'category': 'オープン',
                            'placements': [{'rank': i} for i in range(1, 5)]}]}
        ledger = {'records': [{'event_id': 'b', 'event_date': '2026-10-11', 'category': None},
                              {'event_id': 'c', 'event_date': '2026-10-11', 'category': 'ジュニア'}]}
        scope = {'coverage': {'captured_top8_rows': 4},
                 'classification_coverage_top8': {'unclassified_count': 1},
                 'stage_composition': {'top8': [{'parent_archetype': 'A', 'count': 3}]}}
        got = missingness(city, ledger, 'W03', scope)
        self.assertEqual(got['conditional_potential_missing_top8_slots'], 12)
        self.assertEqual(got['archetype_sensitivity'][0]['conditional_share_min'], 3/16)
        self.assertEqual(got['archetype_sensitivity'][0]['conditional_share_max'], 1)
        self.assertIsNone(got['national_missingness_upper_bound'])
        self.assertFalse(got['schedule_completeness_verified'])
        self.assertEqual(len(city['events'][0]['placements']), 4)

    def fixture(self, root):
        data = root / 'data'
        data.mkdir()
        city = {'updated_at': '2026-10-12T08:00:00+09:00', 'events': [
            {'event_id': '1', 'date': '2026-10-11', 'category': 'オープン',
             'placements': [{'rank': 1, 'deck_code': 'a'}]}]}
        decks = {'updated_at': city['updated_at'], 'decks': {'a': {
            'total_cards': 60, 'cards': [{'count': 60, 'name': '基本草エネルギー', 'card_id': 'x', 'section': 'エネルギー'}]}}}
        write_candidate(data, city, decks)
        (data/'analysis').mkdir()
        for name, doc in [('event_ledger', {'records': []}), ('deck_classifications', {'records': []})]:
            (data/'analysis'/f'{name}.json').write_text(json.dumps(doc))

    def test_offline_publication_is_idempotent_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            before = {str(p): p.read_bytes() for p in (root/'data').rglob('*.json')}
            def git(args, **kw):
                if args[1] == 'status': return ''
                return 'a'*40 + '\n'
            with patch('analysis.weekly_delivery.subprocess.check_output', side_effect=git):
                output = generate(root, root/'.tmp/out', 'W03', datetime.fromisoformat('2026-10-14T10:00:00+09:00'))
                original = (output/'summary.json').read_bytes()
                self.assertEqual(output, generate(root, root/'.tmp/out', 'W03', datetime.fromisoformat('2026-10-15T10:00:00+09:00')))
                self.assertEqual(original, (output/'summary.json').read_bytes())
            summary = json.loads(original)
            self.assertEqual(summary['stage'], 'INTERIM')
            self.assertTrue(summary['source_stale_24h'])
            self.assertEqual(summary['audit_status'], 'WARN')
            self.assertEqual(before, {p: Path(p).read_bytes() for p in before})

    def test_corrupted_input_and_protected_output_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            now = datetime.fromisoformat('2026-10-14T10:00:00+09:00')
            with self.assertRaises(ValueError):
                generate(root, root/'data/analysis/weekly_snapshots', 'W03', now)
            p = root/'data/city_db.json'
            p.write_text(p.read_text() + ' ')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                generate(root, root/'.tmp/out', 'W03', now)
            self.assertFalse((root/'.tmp/out').exists())

    def test_open_week_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'closed JST'):
                generate(Path(tmp), Path(tmp)/'out', 'W04', datetime.fromisoformat('2026-10-17T10:00:00+09:00'))


if __name__ == '__main__':
    unittest.main()
