"""Regression tests for rank-complete, tournament-conditional performance."""
import unittest
from analysis.dhelmise_performance import (
    is_complete_event, fisher_two_sided, wilson95, build_report,
)


def group_rows(event_id, count=16):
    values = [1, 2, 3, 3] + [5] * 4 + [9] * 8
    return [{"event_id": event_id, "rank": rank} for rank in values[:count]]


class PerformanceTests(unittest.TestCase):
    def test_only_exact_16_slots(self):
        self.assertTrue(is_complete_event(group_rows("yes")))
        self.assertFalse(is_complete_event(group_rows("no", count=8)))
        self.assertFalse(is_complete_event(group_rows("extra") + [{"rank": 9}]))
        broken = group_rows("bad")
        broken[10]["rank"] = 5
        self.assertFalse(is_complete_event(broken))

    def test_fisher_symmetry_and_known_value(self):
        result = fisher_two_sided(1, 9, 9, 1)
        self.assertAlmostEqual(result, 0.001093333910671372, places=8)
        self.assertAlmostEqual(result, fisher_two_sided(9, 1, 1, 9))
        self.assertEqual(fisher_two_sided(0, 0, 1, 5), 1.0)

    def test_wilson_for_sparse_evidence(self):
        self.assertIsNone(wilson95(0, 0))
        low, high = wilson95(0, 5)
        self.assertEqual(low, 0)
        self.assertGreater(high, 0.4)

    def test_excludes_incomplete_ranks_even_if_rank_one(self):
        cards = [{"name": "ダダリン", "count": 4, "section": "ポケモン"}]
        r = {
            "average_profile": "ミカルゲ採用・ヤバソチャ厚め",
            "date": "2026-10-09", "rank": 1, "category": "オープン",
            "venue_prefecture": "愛知県", "deck_code": "ABC",
            "deck_url": "https://example.org/ABC", "total_cards": 60,
            "cards": cards + [{"name": "基本エネルギー", "count": 56, "section": "エネルギー"}],
        }
        complete = dict(r, event_id="complete")
        partial = dict(r, event_id="partial")
        manifest = {
            "source_city_sha256": "abc", "source_decks_sha256": "def",
            "source_updated_at": "2026-10-18T00:00+09:00",
            "calendar_id": "2027-s1-mon-sun-v1",
        }
        report = build_report(group_rows("complete") + group_rows("partial", 8), [complete, partial], manifest)
        self.assertEqual(report["observed_events"], 2)
        self.assertEqual(report["complete_16_slot_events"], 1)
        self.assertEqual(report["eligible_complete_event_dhelmise"]["placements"], 1)
        self.assertEqual(report["all_recorded_dhelmise"]["placements"], 2)
        self.assertEqual(report["groups"]["ミカルゲ採用・ヤバソチャ厚め"]["metrics"]["champion"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
