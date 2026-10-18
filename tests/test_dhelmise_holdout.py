"""Keep future holdout independent and fail closed on missing evidence."""
import unittest
from analysis.dhelmise_holdout import assess

def protocol():
    return {
        "training": {"source_decks_sha256": "frozen_training_sha", "reference_copies": 52,
                     "modal_reference": [{"card": "ダダリン", "copies": 4}]},
        "validation": {
            "weeks": ["W05", "W06", "W07", "W08"],
            "population_profiles": ["ヤバソチャ少数・通常ノココッチ"],
            "primary_comparison": {
                "minimum_each_group": 10, "alpha": .05,
            }
        }
    }

def manifest(*weeks):
    return {
        "weeks": [{"week_id": w} for w in weeks],
        "source_decks_sha256": "new_future_sha",
        "source_updated_at": "2026-10-25T22:00:00+09:00",
    }

def event(event_id):
    ranks = [1, 2, 3, 3] + [5] * 4 + [9] * 8
    return [{"event_id": event_id, "rank": rank} for rank in ranks]

def deck(name, event_id, rank, mon, week):
    return {
        "event_id": event_id, "deck_code": name, "rank": rank,
        "_week": week, "average_profile": "ヤバソチャ少数・通常ノココッチ",
        "cards": [
            {"name": "ダダリン", "count": 4},
            {"name": mon, "count": 1},
            {"name": "基本超エネルギー", "count": 55},
        ],
    }

class HoldoutTests(unittest.TestCase):
    def test_unstarted_window_has_no_success(self):
        result = assess(protocol(), manifest(), [], [])
        self.assertEqual(result["status"], "not_started")
        self.assertEqual(result["available_weeks"], [])
        self.assertEqual(result["training_base_copies_frozen"], 52)
        self.assertFalse(result["exclusive_comparison"]["exploratory_replication_criterion_met"])

    def test_partial_window_does_not_claim_success(self):
        t = event("confirmed") + event("incomplete")[:8]
        rows = [
            deck("K1", "confirmed", 1, "キチキギスex", "W05"),
            deck("S1", "confirmed", 9, "シェイミ", "W05"),
            deck("K2", "incomplete", 1, "キチキギスex", "W05"),
        ]
        r = assess(protocol(), manifest("W05"), t, rows)
        self.assertEqual(r["status"], "window_in_progress_descriptive_only")
        self.assertEqual(r["eligible_common_engine_decks"]["placements"], 2)
        self.assertEqual(r["exclusive_comparison"]["kichi_only"]["placements"], 1)
        self.assertIsNotNone(r["exclusive_comparison"]["fisher_two_sided_p"])
        self.assertFalse(r["exclusive_comparison"]["exploratory_replication_criterion_met"])

    def test_no_success_on_tiny_completed_window(self):
        t = event("yes")
        decks = [
            deck("K1", "yes", 1, "キチキギスex", "W08"),
            deck("S1", "yes", 9, "シェイミ", "W08"),
        ]
        r = assess(protocol(), manifest("W05", "W06", "W07", "W08"), t, decks)
        self.assertEqual(r["status"], "insufficient_samples")
        self.assertFalse(r["exclusive_comparison"]["sufficient_group_size"])

if __name__ == "__main__":
    unittest.main()
