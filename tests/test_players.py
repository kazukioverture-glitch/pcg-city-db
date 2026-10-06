import copy
import unittest

from analysis.players import join_players


def event(eid, day, pid='0001', category='オープン', code='A', rank=1):
    return dict(event_id=eid, date=day, category=category,
                placements=[dict(player_id=pid, deck_code=code, rank=rank)])


class PlayerJoinTests(unittest.TestCase):
    def setUp(self):
        self.city = {'events': [event('old', '2025-10-01'), event('prior', '2026-09-01'),
                               event('junior', '2026-09-02', category='ジュニア'),
                               event('now', '2026-10-01'), event('same_day', '2026-10-01'),
                               event('future', '2026-10-02')]}
        self.classifications = {'records': [dict(deck_code='A', parent_archetype='ドラパルトex',
                                                classification_status='classified')]}

    def run_join(self, **kwargs):
        return join_players(self.city, self.classifications, '2026-10-01', '2026-10-01',
                            '2026-01-01', **kwargs)['records']

    def test_time_window_category_same_day_and_read_only(self):
        before = copy.deepcopy(self.city)
        rows = self.run_join()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['player_id'], '0001')
        self.assertEqual(rows[0]['observed_prior_top8'], 1)
        self.assertEqual(rows[0]['observed_same_parent_prior_top8'], 1)
        self.assertEqual(self.city, before)

    def test_previous_season_explicit_and_no_csp_inference(self):
        row = self.run_join(previous_start='2026-01-01', previous_end='2026-09-30')[0]
        self.assertEqual(row['observed_previous_season_top8'], 1)
        self.assertEqual(row['observed_previous_season_best_rank'], 1)
        self.assertIsNone(row['csp_evidence'])
        self.assertIsNone(self.run_join()[0]['observed_previous_season_top8'])

    def test_missing_ids_and_unknown_parent(self):
        self.city['events'] = [event('now', '2026-10-01', pid=None)]
        row = self.run_join()[0]
        self.assertIsNone(row['observed_prior_top8'])
        self.city['events'] = [event('now', '2026-10-01', code='unknown')]
        self.assertIsNone(self.run_join()[0]['observed_same_parent_prior_top8'])

    def test_ambiguous_identity_rejected(self):
        self.city['events'][0]['placements'][0]['player_id'] = 1
        # Outside history: not consulted at all.
        self.run_join()
        self.city['events'][1]['placements'][0]['player_id'] = 1
        with self.assertRaises(ValueError):
            self.run_join()

    def test_duplicate_event_and_player_rejected(self):
        self.city['events'].append(copy.deepcopy(self.city['events'][1]))
        with self.assertRaises(ValueError):
            self.run_join()
        self.city['events'].pop()
        self.city['events'][1]['placements'] *= 2
        with self.assertRaises(ValueError):
            self.run_join()

    def test_csp_evidence_scope_and_future_information(self):
        fact = dict(player_id='0001', category='オープン', season='2027', as_of='2026-09-20',
                    verified_on='2026-09-21', csp=100, source_url='https://example.org/source')
        row = self.run_join(facts=[fact], csp_season='2027')[0]
        self.assertEqual(row['csp_evidence']['csp'], 100)
        self.assertIsNone(self.run_join(facts=[fact], csp_season='2026')[0]['csp_evidence'])
        fact['verified_on'] = '2026-10-01'
        self.assertIsNone(self.run_join(facts=[fact], csp_season='2027')[0]['csp_evidence'])
        with self.assertRaises(ValueError):
            self.run_join(facts=[fact])

    def test_invalid_period_and_csp_conflicts(self):
        with self.assertRaises(ValueError):
            self.run_join(previous_start='2026-01-01')
        with self.assertRaises(ValueError):
            self.run_join(previous_start='2026-01-01', previous_end='2026-10-01')
        fact = dict(player_id='0001', category='オープン', season='2027', as_of='2026-09-20',
                    verified_on='2026-09-21', csp=100, source_url='https://example.org/source')
        with self.assertRaises(ValueError):
            self.run_join(facts=[fact, fact], csp_season='2027')


if __name__ == '__main__':
    unittest.main()
