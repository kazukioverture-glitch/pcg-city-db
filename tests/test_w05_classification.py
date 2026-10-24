"""W05 INTERIM evidence-backed classification checks."""
import json
import unittest

from analysis.classification import classify
from analysis.state import ROOT

EXPECTED = {
    "KccDx8-J6fGlu-Gx88KK": "メガガルーラ・オーガポンバレット",
}

class W05ClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        city=json.loads((ROOT/"data/city_db.json").read_text(encoding="utf-8"))
        cls.decks=json.loads((ROOT/"data/city_decks.json").read_text(encoding="utf-8"))["decks"]
        cls.index=json.loads((ROOT/"data/index.json").read_text(encoding="utf-8"))
        cls.w05_top8={
            p["deck_code"]
            for event in city["events"]
            if "2026-10-19" <= event.get("date","") <= "2026-10-25"
            for p in event.get("placements",[])
            if 1 <= p.get("rank",100) <= 8 and p.get("deck_code")
        }

    def test_expected_w05_parent_is_source_anchored(self):
        for code,parent in EXPECTED.items():
            with self.subTest(code=code):
                self.assertIn(code,self.w05_top8)
                deck=self.decks[code]
                self.assertEqual(deck["total_cards"],60)
                self.assertEqual(sum(card["count"] for card in deck["cards"]),60)
                result=classify(code,deck,self.index["source_updated_at"])
                self.assertEqual(result["classification_status"],"classified")
                self.assertEqual(result["parent_archetype"],parent)

if __name__=="__main__":
    unittest.main()
