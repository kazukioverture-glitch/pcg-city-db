import copy
import json
import tempfile
import unittest
from pathlib import Path

from analysis.classification import IDENTITIES, RULES, VERSION, classify, classify_range

NOW = "2026-10-07T02:00:00+09:00"


def deck(**counts):
    cards = []
    for name, count in counts.items():
        card_id, aliases = next(iter(IDENTITIES["cards"][name].items()))
        cards.append(dict(card_id=card_id, name=aliases[0], count=count, section="ポケモン (10)"))
    cards.append(dict(card_id="energy", name="基本エネルギー",
                      count=60-sum(counts.values()), section="エネルギー"))
    return dict(deck_code="code", total_cards=60, cards=cards)


class ClassificationTests(unittest.TestCase):
    def test_rules_are_japan_specific_and_external_taxonomy_is_reference_only(self):
        self.assertEqual(VERSION, "JP-2026-W02-v1")
        self.assertEqual(RULES["market"], "JP")
        self.assertFalse(RULES["external_taxonomy_reference"]["rules_imported"])
        self.assertFalse(RULES["external_taxonomy_reference"]["card_pool_equivalent"])
        self.assertTrue(RULES["policy"]["unknown_allowed"])

    def test_engine_cards_are_not_parents_by_presence_alone(self):
        for counts in (
            {"オーガポン みどりのめんex": 4},
            {"メガガルーラex": 4},
            {"オーガポン みどりのめんex": 4, "メガガルーラex": 3},
        ):
            with self.subTest(counts=counts):
                result = classify("code", deck(**counts), NOW)
                self.assertIsNone(result["parent_archetype"])
                self.assertEqual(result["classification_status"], "unknown")

    def test_main_attacker_parent_with_engine_variant(self):
        cases = [
            ({"メガレックウザex": 2, "メガガルーラex": 3},
             "メガレックウザex", ["メガガルーラex採用"]),
            ({"カミツオロチex": 2, "オーガポン みどりのめんex": 4},
             "カミツオロチex", ["オーガポン みどりのめんex型"]),
            ({"オリーヴァex": 2, "オーガポン みどりのめんex": 4},
             "オリーヴァex", ["オーガポン みどりのめんex型"]),
            ({"メガフシギバナex": 2, "オーガポン みどりのめんex": 4},
             "メガフシギバナex", ["オーガポン みどりのめんex型"]),
            ({"タケルライコex": 2, "オーガポン みどりのめんex": 4},
             "タケルライコex", ["オーガポン みどりのめんex型"]),
            ({"ヤドキング": 2, "メガガルーラex": 2},
             "ヤドキング", ["メガガルーラex採用"]),
        ]
        for counts, parent, tags in cases:
            with self.subTest(parent=parent):
                result = classify("code", deck(**counts), NOW)
                self.assertEqual(result["parent_archetype"], parent)
                self.assertEqual(result["variant_tags"], tags)
                self.assertEqual(result["classification_status"], "classified")

    def test_dragapult_support_tags_are_nonexclusive(self):
        d = deck(**{"ドラパルトex": 2, "ノココッチ": 2, "ヨノワール": 1})
        self.assertEqual(
            classify("code", d, NOW)["variant_tags"],
            ["ノココッチ型", "ヨノワール型"],
        )
        d["cards"][1]["name"] = "ノココッチex"
        self.assertEqual(classify("code", d, NOW)["variant_tags"], ["ヨノワール型"])

    def test_equal_priority_main_attacker_collision_stays_unknown(self):
        result = classify(
            "code",
            deck(**{"メガレックウザex": 2, "タケルライコex": 2}),
            NOW,
        )
        self.assertIsNone(result["parent_archetype"])
        self.assertEqual(result["classification_status"], "unknown")
        evidence = json.loads(result["evidence"])
        self.assertEqual(evidence["reason"], "parent_collision_equal_priority")

    def test_printings_sum_and_alias_mismatch(self):
        d = deck(**{"ドラパルトex": 1})
        card_id, aliases = list(IDENTITIES["cards"]["ドラパルトex"].items())[1]
        d["cards"].insert(
            1, dict(card_id=card_id, name=aliases[0], count=1, section="ポケモン"))
        d["cards"][-1]["count"] -= 1
        self.assertEqual(classify("code", d, NOW)["parent_archetype"], "ドラパルトex")
        d["cards"][1]["name"] = "別のカード"
        self.assertIsNone(classify("code", d, NOW)["parent_archetype"])

    def test_invalid_deck_and_no_input_mutation(self):
        d = deck(**{"ドラパルトex": 3})
        original = copy.deepcopy(d)
        classify("code", d, NOW)
        self.assertEqual(d, original)
        d["cards"][-1]["count"] = 1
        self.assertEqual(classify("code", d, NOW)["classification_status"], "unknown")

    def test_atomic_batch_idempotence_migration_missing_source_and_manual_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            city, decks, output = [
                root/name for name in ("city.json", "decks.json", "classifications.json")]
            city.write_text(json.dumps({
                "events": [
                    {"date": "2026-09-23", "placements": [{"deck_code": "code"}]},
                    {"date": "2026-10-07", "placements": [{"deck_code": "missing"}]},
                ]
            }))
            decks.write_text(json.dumps({
                "decks": {"code": deck(**{"ドラパルトex": 2})}
            }))

            def run(end="2026-10-06"):
                return classify_range(
                    city, decks, output, "2026-09-23", end, timestamp=NOW)

            first = run()
            self.assertEqual(first["selected_decks"], 1)
            self.assertEqual(first["classifier_version"], VERSION)
            baseline = output.read_bytes()
            self.assertEqual(run()["changed"], 0)
            self.assertEqual(output.read_bytes(), baseline)

            document = json.loads(baseline)
            document["records"][0]["classifier_version"] = "city-classifier-v1"
            output.write_text(json.dumps(document))
            self.assertEqual(run()["changed"], 1)
            migrated = json.loads(output.read_text())
            self.assertEqual(migrated["records"][0]["classifier_version"], VERSION)

            with self.assertRaisesRegex(ValueError, "Missing source"):
                run("2026-10-07")

            document = json.loads(output.read_text())
            document["records"][0]["classifier_version"] = "manual"
            output.write_text(json.dumps(document))
            manual = output.read_bytes()
            with self.assertRaisesRegex(ValueError, "manual record"):
                run()
            self.assertEqual(output.read_bytes(), manual)

            with self.assertRaisesRegex(ValueError, "immutable"):
                classify_range(
                    city, decks, root/"weekly_snapshots"/"copy.json",
                    "2026-09-23", "2026-10-06")


if __name__ == "__main__":
    unittest.main()
