"""Tests for evidence limits and baseline-relative card factors."""
import unittest

from analysis.dhelmise_card_factors import analyze, bh_adjust, counts, stratified_cmh

BASE = {
    "source_decks_sha256": "decks",
    "source_city_sha256": "city",
    "reference": {
        "card_count": 2,
        "copy_count": 5,
        "cards": [
            {"card": "ダダリン", "mode_copies": 4, "adoption_rate": 1.0,
             "modal_agreement": 1.0},
            {"card": "シェイミ", "mode_copies": 1, "adoption_rate": .9,
             "modal_agreement": .9},
        ],
    },
    "population": 2,
    "strict_modal_core": {"copy_count": 4},
}
MANIFEST = {
    "source_city_sha256": "city",
    "source_decks_sha256": "decks",
    "source_updated_at": "2026-10-18T07:31:27+09:00",
}


def deck(id, event, rank, kichi, shaymin):
    arr = [
        {"section": "ポケモン (2)", "name": "ダダリン", "count": 4},
        {"section": "ポケモン (2)", "name": "シェイミ", "count": int(shaymin)},
        {"section": "ポケモン (2)", "name": "キチキギスex", "count": int(kichi)},
        {"section": "エネルギー (55)", "name": "基本超エネルギー",
         "count": 56 - int(shaymin) - int(kichi)},
    ]
    return {
        "row_id": id, "event_id": event, "deck_code": id,
        "average_profile": "ヤバソチャ少数・通常ノココッチ",
        "_week_source": "W01", "rank": rank, "cards": arr,
    }


def event(event_id, ranks):
    return [
        {"event_id": event_id, "rank": rank} for rank in ranks
    ]


class FactorTests(unittest.TestCase):
    def test_fdr_adjust_and_monotonicity(self):
        self.assertEqual(bh_adjust([.04, .01, .5]), [.06, .03, .5])
        self.assertEqual(bh_adjust([1, 1]), [1, 1])

    def test_eligible_and_differences(self):
        ranks = [1, 2] + [3] * 2 + [5] * 4 + [9] * 8
        top = event("complete", ranks) + event("partial", [1, 2])
        cases = [
            deck("A", "complete", 1, True, False),
            deck("B", "partial", 1, False, True),
        ]
        result = analyze(top, cases, BASE, MANIFEST, ["W01"])
        self.assertEqual(result["population"]["reference_decks"], 2)
        self.assertEqual(result["population"]["eligible_decks"], 1)
        self.assertEqual(result["population"]["top4"], 1)
        groups = result["joint_hypothesis_kichi_shaymin"]
        self.assertEqual(sum(x["results"]["n"] for x in groups), 1)
        self.assertEqual(
            [x for x in groups if x["kichi_adopted"] and not x["shaymin_adopted"]][0]["results"]["top4"], 1)
        self.assertEqual(len(result["exploratory_feature_tests"]), 12)
        self.assertEqual(result["exploratory_feature_tests"][0]["adjusted_for_week"]["strata"], 1)

    def test_wrong_source_rejected(self):
        invalid = dict(BASE, source_decks_sha256="wrong")
        with self.assertRaises(ValueError):
            analyze([], [], invalid, MANIFEST, ["W01"])

    def test_stratified_cmh_handles_sparse_strata(self):
        rows = [{"rank": r, "yes": yes, "group": g}
                for r, yes, g in [(3, True, "a"), (5, False, "a"),
                                  (3, False, "a"), (9, False, "a"),
                                  (5, True, "b"), (3, False, "b")]]
        result = stratified_cmh(rows, lambda r: r["yes"], lambda r: r["group"])
        self.assertEqual(result["strata"], 2)
        self.assertEqual(result["informative_strata"], 2)
        self.assertIsNotNone(result["normal_approx_two_sided_p"])

    def test_printings_merge(self):
        cards = [
            {"name": "ダダリン", "count": 2},
            {"name": "ダダリンM5037/081", "count": 2},
            {"name": "基本超エネルギー", "count": 56},
        ]
        self.assertEqual(counts(cards)["ダダリン"], 4)


if __name__ == "__main__":
    unittest.main()
