"""Tests for averaged-deck structural cohort routing."""
import unittest
from analysis.dhelmise_profiles import profile, PROFILES


def mon(name, count=1, section="ポケモン (10)"):
    return {"name": name, "card_id": "test", "count": count, "section": section}


class DhelmiseProfilesTests(unittest.TestCase):
    def test_spiritomb_4_4_routes_to_separate_profile(self):
        cards = [
            mon("ミカルゲM5033/081", 1), mon("チャデスM5005/081", 4),
            mon("ヤバソチャM5006/081", 4), mon("ノココッチ", 2)
        ]
        item = profile(cards)
        self.assertEqual(item["average_profile"], PROFILES[0])
        self.assertEqual(item["spiritomb_count"], 1)

    def test_dense_yabasoucha_without_spiritomb(self):
        self.assertEqual(profile([mon("ヤバソチャ", 3)])["average_profile"], PROFILES[1])

    def test_light_yabasoucha_with_ex_distinct_from_plain(self):
        cards = [mon("ヤバソチャ", 1), mon("ノココッチexMC575/742", 1)]
        item = profile(cards)
        self.assertEqual(item["average_profile"], PROFILES[2])
        self.assertEqual(item["dudunsparce_count"], 0)
        self.assertEqual(item["dudunsparce_ex_count"], 1)

    def test_light_yabasoucha_plain_or_no_dunsparce(self):
        self.assertEqual(
            profile([mon("ヤバソチャ"), mon("ノココッチ", 2)])["average_profile"],
            PROFILES[3])
        self.assertEqual(profile([mon("ヤバソチャ")])["average_profile"], PROFILES[4])

    def test_alakazam_and_plain_banette(self):
        self.assertEqual(profile([mon("フーディン")])["average_profile"], PROFILES[5])
        self.assertEqual(profile([mon("ジュペッタ")])["average_profile"], PROFILES[6])

    def test_future_spiritomb_exception_not_mislabeled_dense(self):
        self.assertEqual(
            profile([mon("ミカルゲ"), mon("ヤバソチャ", 1)])["average_profile"],
            PROFILES[7])

    def test_suspected_source_tool_section_error_is_explicit(self):
        item = profile([mon("ふうせん", 2), mon("ジュペッタ")])
        self.assertEqual(item["section_mismatch_tool_names"], ["ふうせん"])


if __name__ == "__main__":
    unittest.main()
