"""Dhelmise group classifier: distinct Pokemon names and printing suffixes."""
import unittest
from analysis.dhelmise_groups import group_labels, pokemon_count, GROUPS, ENGINES

def mon(name, count=1):
    return {"name": name, "card_id": "x", "count": count, "section": "ポケモン (10)"}

class VariantGroupTests(unittest.TestCase):
    def test_yabasoucha_and_ex_engine(self):
        cards = [mon("ヤバソチャM5006/081"), mon("ノココッチexMC575/742")]
        self.assertEqual(group_labels(cards), (GROUPS[0], ENGINES[0]))
        self.assertEqual(pokemon_count(cards, "ノココッチ"), 0)

    def test_alakazam_and_normal_dudunsparce(self):
        cards = [mon("フーディンM1S038/063", 3), mon("ノココッチM-P048/M-P", 2)]
        self.assertEqual(group_labels(cards), (GROUPS[2], ENGINES[1]))

    def test_no_extras(self):
        self.assertEqual(group_labels([mon("ダダリン"), mon("ジュペッタ")]),
                         (GROUPS[1], ENGINES[2]))

    def test_name_not_just_substring_and_wrong_section(self):
        cards = [{"name": "ヤバソチャM5006/081", "card_id": "x",
                  "count": 1, "section": "グッズ"}]
        self.assertEqual(pokemon_count(cards, "ヤバソチャ"), 0)
        self.assertEqual(pokemon_count([{"name": "ヤバソチャ", "card_id": "x", "count": 2, "section": "ポケモンのどうぐ (2)"}], "ヤバソチャ"), 0)
        self.assertEqual(pokemon_count([mon("ヤバソチャ別名")], "ヤバソチャ"), 0)

if __name__ == "__main__":
    unittest.main()
