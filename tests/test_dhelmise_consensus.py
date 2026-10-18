"""Reproducible consent baseline: card identity, modal copies, strict core."""
import unittest
from analysis.dhelmise_consensus import consensus, deck_counts, logical_name


def row(deckcode, counts, rank=9):
    cards = []
    for name, count in counts.items():
        group = "ポケモン" if name in ("ダダリン", "ジュペッタ") else "グッズ"
        cards.append({"name": name, "section": group + " (1)",
                      "card_id": "synthetic", "count": count})
    return {
        "average_profile": "ヤバソチャ少数・通常ノココッチ",
        "deck_code": deckcode, "rank": rank, "total_cards": 60,
        "cards": cards,
    }


META = {
    "source_city_sha256": "a", "source_decks_sha256": "b",
    "source_updated_at": "2026-10-18T07:31:27+09:00",
    "calendar_id": "2027-s1-mon-sun-v1",
}


class ConsensusTests(unittest.TestCase):
    def test_printings_coalesce_into_logical_name(self):
        self.assertEqual(logical_name({
            "name": "ダダリンM5037/081"}), "ダダリン")
        deck, types = deck_counts([
            {"name": "ダダリン", "count": 2, "section": "ポケモン (2)"},
            {"name": "ダダリンM5037/081", "count": 2, "section": "ポケモン (2)"},
            {"name": "基本超エネルギー", "count": 56, "section": "エネルギー (56)"},
        ])
        self.assertEqual(deck["ダダリン"], 4)
        self.assertEqual(types["ダダリン"], {"ポケモン"})

    def test_modal_reference_and_high_agreement(self):
        # 90% same card, 80% same count, 20% alternative copy count
        arr = [
            row(str(i), {"ダダリン": 4, "ジュペッタ": 3, "ハイパーボール": 4,
                         "夜のタンカ": 2, "基本超エネルギー": 47})
            for i in range(8)
        ] + [
            row(str(i), {"ダダリン": 4, "ジュペッタ": 4, "ハイパーボール": 4,
                         "夜のタンカ": 2, "基本超エネルギー": 46})
            for i in range(8, 10)
        ]
        report = consensus(arr, META)
        cards = {v["card"]: v for v in report["reference"]["cards"]}
        self.assertEqual(cards["ジュペッタ"]["mode_copies"], 3)
        self.assertEqual(cards["ジュペッタ"]["matching_mode"], 8)
        self.assertNotIn("ジュペッタ", report["strict_modal_core"]["cards"])
        self.assertIn("ダダリン", report["strict_modal_core"]["cards"])
        self.assertEqual(report["population"], 10)

    def test_reject_incomplete_60_cards(self):
        with self.assertRaises(ValueError):
            consensus([row("A", {"ダダリン": 4})], META)

    def test_reject_outside_cohort(self):
        case = row("A", {"ダダリン": 4, "基本超エネルギー": 56})
        case["average_profile"] = "ミカルゲ採用・ヤバソチャ厚め"
        with self.assertRaises(ValueError):
            consensus([case], META)


if __name__ == "__main__":
    unittest.main()
