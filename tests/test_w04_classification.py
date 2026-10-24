"""W04 evidence-backed classification regression checks.

Uses repository-local 60-card lists only. Historical FINAL snapshots are not
rewritten; these checks validate current classifier behavior for W04 deck codes.
"""
import json
import unittest

from analysis.classification import classify
from analysis.state import ROOT, read_state
from analysis.weekly import _classification_resolver


EXPECTED = {
    "LignQn-dm1J1G-9gg9nL": "イイネイヌ／ガメノデス",
    "G8D48Y-zv8NwB-8xxcxc": "イイネイヌ／ガメノデス",
    "PQnLQL-22y9kM-L9gnnH": "メガシャンデラex",
    "MXEXpy-iMAlL5-MREEyp": "リーリエのピッピex",
    "fk5dvF-uFL153-VFVv5b": "リーリエのピッピex",
    "8c8GJx-ProOTe-xDY8cx": "ゲンガーex",
    "VVF5kk-FnqL86-vVbwdw": "ダイゴのメタグロスex",
    "gLQnLN-KKvHAx-NPL9L9": "ダイゴのメタグロスex",
    "yMRpyM-IXUgTP-XRpR22": "ダイゴのメタグロスex",
    "kVkbvF-ATJpJV-dFVvVb": "ダイゴのメタグロスex",
    "9NLH9n-jt9ZRC-HgLngL": "メガサーナイトex",
    "9Q9nLL-g9Xcy6-gnnn9g": "メガサーナイトex",
    "9gHg9P-0JqUgO-ngLnLL": "スピアーex",
    "yR22MM-OKjPlw-yRMUpp": "スピアーex",
    "kb5Vkk-m3KKCW-bkFFVF": "スピアーex",
    "8xxaG8-HSawPO-c8c48x": "スピアーex",
    "MEMpSy-kcZDpv-pyRXyU": "イワパレス",
    "4D8xxG-IMHmg5-8Jcccc": "イワパレス",
    "x8GcDJ-mdT2QI-xGxD88": "イワパレス",
    "xY8G8G-NlVng5-D8xDGc": "オーガポン みどりのめんex／メガニウム",
    "VVfkFF-QZxXh4-kkFbVV": "ホップのオーロット",
    "KDxc88-cofo1y-8x8xDG": "ロケット団のワナイダー",
    "pRyX2S-BopFBH-SMyEMy": "メガスターミーex",
    "QP9gQL-ALec2M-LLLiLn": "メガユキメノコex",
    "ngPNLi-oBWxm2-9LLNnn": "マリルリ",
    "VVFVwV-V16Plr-bFk5kV": "メガグソクムシャex",
    "9LnLgN-5oIWOm-LHL6gg": "メガアブソルex",
    "8x8Dcx-6APRrg-GGcx8D": "イルカマンex",
    "8YJD8x-0roB5P-c8xxGc": "ゲッコウガex／メガユキメノコex",
    "84x8JG-v6lZ3r-c8caDx": "ゲッコウガex／メガユキメノコex",
    "9HgnLn-A0RiPX-nL9QNL": "ゲッコウガex／メガユキメノコex",
    "MXMRME-32mWVA-MpRyyy": "メガミミロップex／メガユキメノコex",
    "kkkFdF-tTlzGU-V1Vkbk": "メガミミロップex／メガユキメノコex",
    "JDKG8c-OCPcoZ-xc8GGx": "メガミミロップex／メガユキメノコex",
    "x8cGGc-kaCjcE-x848xx": "メガミミロップex／メガユキメノコex",
    "ggLLQn-DeN1VJ-9nQLn9": "メガミミロップex／メガユキメノコex",
    "R2MMyM-kIjjZw-UXyyMp": "メガミミロップex／メガユキメノコex",
    "GY8cx8-KvIVxj-88xYDx": "ヒビキのバクフーン",
    "RyU3yp-7CGSKz-pMMSU2": "メガライボルトex／シビビール",
}

AMBIGUOUS_REMAIN_UNKNOWN = {
    # Thick Alakazam + Dragapult lines: do not guess the main parent.
    "9nNPLg-imA98a-g9LnLn",
    "RyMMXM-64WDJT-XpyMER",
    "c8a4JJ-0J0dVa-xJcxGD",
    "VFVVVw-CdZbCP-VFdbkk",
    # Mixed attackers / bullets without a sufficiently supported parent.
    "5VkFw1-pFvupb-5FbfFV",
    "xGxxYK-oVVnC6-Yc488G",
    "nL9ngN-1vn6yT-g9LN6L",
    "9nLiQL-p7lwk7-LggLn9",
    "VVVVbv-HiXHF5-FVdk5F",
    "kkVVkv-pHXBPq-VFF5Fb",
    "xc8GxK-Yntd0W-8xcGca",
    "9nnNLL-ZkuVdv-gLNLLn",
    "MyXXy2-rPVzzy-MMpRyy",
}


class W04ClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        city = json.loads((ROOT / "data/city_db.json").read_text(encoding="utf-8"))
        cls.decks = json.loads((ROOT / "data/city_decks.json").read_text(encoding="utf-8"))["decks"]
        cls.index = json.loads((ROOT / "data/index.json").read_text(encoding="utf-8"))
        cls.sidecar = read_state(ROOT / "data/analysis/deck_classifications.json")
        cls.w04_top8 = {
            p["deck_code"]
            for event in city["events"]
            if "2026-10-12" <= event.get("date", "") <= "2026-10-18"
            for p in event.get("placements", [])
            if 1 <= p.get("rank", 100) <= 8 and p.get("deck_code")
        }

    def test_expected_w04_parents_are_source_anchored(self):
        for code, parent in EXPECTED.items():
            with self.subTest(code=code):
                self.assertIn(code, self.w04_top8)
                deck = self.decks[code]
                self.assertEqual(deck["total_cards"], 60)
                self.assertEqual(sum(card["count"] for card in deck["cards"]), 60)
                result = classify(code, deck, self.index["source_updated_at"])
                self.assertEqual(result["classification_status"], "classified")
                self.assertEqual(result["parent_archetype"], parent)

    def test_selected_ambiguous_w04_cases_remain_unknown(self):
        for code in AMBIGUOUS_REMAIN_UNKNOWN:
            with self.subTest(code=code):
                self.assertIn(code, self.w04_top8)
                result = classify(code, self.decks[code], self.index["source_updated_at"])
                self.assertIsNone(result["parent_archetype"])
                self.assertEqual(result["classification_status"], "unknown")

    def test_exact_remaining_unknown_set_under_weekly_resolver(self):
        resolve = _classification_resolver(
            self.sidecar, self.decks, self.index["source_updated_at"])
        unknown = {code for code in self.w04_top8 if not resolve(code)["parent"]}
        self.assertEqual(unknown, AMBIGUOUS_REMAIN_UNKNOWN)
        self.assertEqual(len(unknown), 13)


if __name__ == "__main__":
    unittest.main()
