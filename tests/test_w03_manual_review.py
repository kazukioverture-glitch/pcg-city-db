"""W03 targeted deck-classification evidence checks.

Uses only fixed local DB files; does not fetch, rewrite, or modify FINAL inputs.
"""
import json
import unittest
from pathlib import Path

from analysis.classification import classify
from analysis.state import ROOT, read_state
from analysis.weekly import _classification_resolver

MANUAL_EXPECTED = {
    "FvFbf1-bRJd32-kVvbVv": "オーガポン みどりのめんex／メガニウム",
    "gP9ngH-RzOnVS-n6nn6g": "オーガポン みどりのめんex／メガニウム",
    "FkkVkb-FavYA4-VfwdbF": "ホップのオーロット",
    "fwkfbd-3kVII3-FVFkkF": "ホップのオーロット",
    "JDx8cK-HhY1bK-cxGxx4": "ホップのオーロット",
    "8Kcc48-mZBXJP-GcxcxG": "多色バレット",
    "xc8YYc-hRUwO9-axGcYx": "多色バレット",
    "Vd1Ffw-VCrSye-kFVFbV": "ダイゴのメタグロスex",
    "cDx8GD-iXHP0m-c8cxYx": "ダイゴのメタグロスex",
    "MyEp2p-LS9fFS-yRppSy": "ゲンガーex",
    "8cxccK-4UrzP3-x8Gcxx": "ゲンガーex",
    "Lg9LLg-XAjwlk-6Lning": "ヒビキのバクフーン",
    "FVkbFv-GbLKgo-wwVFkV": "メガヤンマex",
    "yRyM2p-6G0xLQ-3MpM2X": "リーリエのピッピex",
    "SpUpyR-V8LfHv-ppMyMM": "スピアーex",
}
AUTO_NEW_PRINT_CODES = {
    "LLnggi-ygYLAQ-n9QgiL": "シロナのガブリアスex",
    "QL9gnL-V6AXXU-ngLPgQ": "シロナのガブリアスex",
    "VffdVV-olXZdV-FVbkFF": "シロナのガブリアスex",
    "xcc48x-MnWRSj-cxGxc8": "シロナのガブリアスex",
    "F1VFVk-cdLJSL-bV5wV5": "ロケット団のワナイダー",
}
AMBIGUOUS_W03_CODES = {
    "4cxG8c-AJK9KN-xGGxcx",
    "LPNnQL-OHS2zK-9QggLg",
}

class W03ManualReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.city = json.loads((ROOT / "data/city_db.json").read_text(encoding="utf-8"))
        cls.decks = json.loads((ROOT / "data/city_decks.json").read_text(encoding="utf-8"))["decks"]
        cls.index = json.loads((ROOT / "data/index.json").read_text(encoding="utf-8"))
        cls.sidecar = read_state(ROOT / "data/analysis/deck_classifications.json")
        cls.records = {r["deck_code"]: r for r in cls.sidecar["records"]}
        cls.w03_top8 = [
            p["deck_code"]
            for e in cls.city["events"]
            if e.get("category") == "オープン" and "2026-10-05" <= e.get("date", "") <= "2026-10-11"
            for p in e.get("placements", [])
            if 1 <= p.get("rank", 100) <= 8
        ]

    def test_manual_classifications_are_source_anchored(self):
        self.assertEqual(len(MANUAL_EXPECTED), 15)
        for code, parent in MANUAL_EXPECTED.items():
            with self.subTest(code=code):
                self.assertIn(code, self.w03_top8)
                self.assertIn(code, self.decks)
                record = self.records[code]
                self.assertEqual(record["classifier_version"], "manual")
                self.assertEqual(record["classification_status"], "classified")
                self.assertEqual(record["parent_archetype"], parent)
                deck = self.decks[code]
                self.assertEqual(deck["total_cards"], 60)
                self.assertEqual(sum(card["count"] for card in deck["cards"]), 60)
                proof = json.loads(record["evidence"])
                self.assertEqual(proof["source_decks_sha256"], self.index["decks_sha256"])
                self.assertEqual(proof["calendar_id"], "2027-s1-mon-sun-v1")
                for anchor in proof["card_anchors"]:
                    observed = sum(card["count"] for card in deck["cards"]
                                   if card["name"] == anchor["card"])
                    self.assertEqual(observed, anchor["observed_copies"])
                    self.assertGreaterEqual(observed, anchor["minimum_copies"])

    def test_exact_print_aliases_restore_five_known_parents(self):
        for code, parent in AUTO_NEW_PRINT_CODES.items():
            with self.subTest(code=code):
                self.assertIn(code, self.w03_top8)
                self.assertNotIn(code, self.records)
                output = classify(code, self.decks[code], self.index["source_updated_at"])
                self.assertEqual(output["parent_archetype"], parent)

    def test_ambiguous_cases_remain_unknown(self):
        resolve = _classification_resolver(
            self.sidecar, self.decks, self.index["source_updated_at"])
        for code in AMBIGUOUS_W03_CODES:
            with self.subTest(code=code):
                self.assertIn(code, self.w03_top8)
                self.assertNotIn(code, self.records)
                self.assertIsNone(resolve(code)["parent"])

    def test_fixed_input_remaining_two_not_promoted_to_final(self):
        if self.index["source_updated_at"] != "2026-10-14T22:31:28+09:00":
            self.skipTest("The city DB has advanced; retest aggregate separately")
        resolve = _classification_resolver(
            self.sidecar, self.decks, self.index["source_updated_at"])
        unknown = [code for code in self.w03_top8 if not resolve(code)["parent"]]
        self.assertEqual(len(self.w03_top8), 580)
        self.assertEqual(set(unknown), AMBIGUOUS_W03_CODES)


if __name__ == "__main__":
    unittest.main()
