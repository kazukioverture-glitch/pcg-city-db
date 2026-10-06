import copy
import json
import tempfile
import unittest
from pathlib import Path

from analysis.classification import IDENTITIES, classify, classify_range

NOW = "2026-10-07T02:00:00+09:00"


def deck(**counts):
    cards = []
    for name, count in counts.items():
        card_id, aliases = next(iter(IDENTITIES["cards"][name].items()))
        cards.append(dict(card_id=card_id, name=aliases[0], count=count, section="ポケモン (10)"))
    cards.append(dict(card_id="energy", name="基本エネルギー", count=60-sum(counts.values()), section="エネルギー"))
    return dict(deck_code="code", total_cards=60, cards=cards)


class ClassificationTests(unittest.TestCase):
    def test_parent_thresholds_and_priority(self):
        for name, threshold in [("ドラパルトex", 2), ("メガガルーラex", 3), ("オーガポン みどりのめんex", 3)]:
            self.assertIsNone(classify("code", deck(**{name: threshold-1}), NOW)["parent_archetype"])
            self.assertEqual(classify("code", deck(**{name: threshold}), NOW)["parent_archetype"], name)
        r = classify("code", deck(**{"メガガルーラex": 3, "オーガポン みどりのめんex": 3}), NOW)
        self.assertEqual(r["parent_archetype"], "メガガルーラex")
        self.assertIsNone(classify("code", deck(**{"メガガルーラex": 3, "ドラパルトex": 2}), NOW)["parent_archetype"])

    def test_both_dragapult_tags_and_ex_exclusion(self):
        d = deck(**{"ドラパルトex": 2, "ノココッチ": 2, "ヨノワール": 1})
        self.assertEqual(classify("code", d, NOW)["variant_tags"], ["ノココッチ型", "ヨノワール型"])
        d["cards"][1]["name"] = "ノココッチex"
        self.assertEqual(classify("code", d, NOW)["variant_tags"], ["ヨノワール型"])

    def test_printings_sum_and_alias_mismatch(self):
        d = deck(**{"ドラパルトex": 1})
        card_id, aliases = list(IDENTITIES["cards"]["ドラパルトex"].items())[1]
        d["cards"].insert(1, dict(card_id=card_id, name=aliases[0], count=1, section="ポケモン"))
        d["cards"][-1]["count"] -= 1
        self.assertEqual(classify("code", d, NOW)["parent_archetype"], "ドラパルトex")
        d["cards"][1]["name"] = "別のカード"
        self.assertIsNone(classify("code", d, NOW)["parent_archetype"])

    def test_attackers_do_not_use_largest_count_or_single_tech(self):
        r = classify("code", deck(**{"メガガルーラex": 3, "メガレックウザex": 2, "ヒビキのホウオウex": 2}), NOW)
        self.assertEqual(r["variant_tags"], ["メガレックウザex型"])
        for attacker in ("カミツオロチex", "オリーヴァex", "メガフシギバナex"):
            r = classify("code", deck(**{"オーガポン みどりのめんex": 3, attacker: 2}), NOW)
            self.assertEqual(r["variant_tags"], [attacker+"型"])
        for counts in [{"メガレックウザex": 1}, {"メガレックウザex": 3, "ヤドキング": 2}, {}]:
            r = classify("code", deck(**{"メガガルーラex": 3, **counts}), NOW)
            self.assertEqual(r["classification_status"], "partial")
            self.assertEqual(r["variant_tags"], [])

    def test_invalid_deck_and_no_input_mutation(self):
        d = deck(**{"ドラパルトex": 3})
        original = copy.deepcopy(d)
        classify("code", d, NOW)
        self.assertEqual(d, original)
        d["cards"][-1]["count"] = 1
        self.assertEqual(classify("code", d, NOW)["classification_status"], "unknown")

    def test_atomic_batch_idempotence_missing_source_and_manual_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            city, decks, output = [root/n for n in ("city.json", "decks.json", "classifications.json")]
            city.write_text(json.dumps({"events": [{"date": "2026-09-23", "placements": [{"deck_code": "code"}]}, {"date": "2026-10-07", "placements": [{"deck_code": "missing"}]}]}))
            decks.write_text(json.dumps({"decks": {"code": deck(**{"ドラパルトex": 2})}}))
            def run(end="2026-10-06"):
                return classify_range(city, decks, output, "2026-09-23", end, timestamp=NOW)
            self.assertEqual(run()["selected_decks"], 1)
            baseline = output.read_bytes()
            self.assertEqual(run()["changed"], 0)
            self.assertEqual(output.read_bytes(), baseline)
            with self.assertRaisesRegex(ValueError, "Missing source"):
                run("2026-10-07")
            self.assertEqual(output.read_bytes(), baseline)
            document = json.loads(baseline)
            document["records"][0]["classifier_version"] = "manual"
            output.write_text(json.dumps(document))
            manual = output.read_bytes()
            with self.assertRaisesRegex(ValueError, "manual record"):
                run()
            self.assertEqual(output.read_bytes(), manual)
            with self.assertRaisesRegex(ValueError, "immutable"):
                classify_range(city, decks, root/"weekly_snapshots"/"copy.json", "2026-09-23", "2026-10-06")
