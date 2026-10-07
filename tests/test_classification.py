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
        if name in {"お祭り会場", "ゼロの大空洞"}:
            section = "スタジアム"
        elif name == "テレパス超エネルギー":
            section = "エネルギー"
        elif name == "ワンダーパッチ":
            section = "グッズ"
        else:
            section = "ポケモン (10)"
        cards.append(dict(card_id=card_id, name=aliases[0], count=count, section=section))
    cards.append(dict(card_id="energy", name="基本エネルギー",
                      count=60-sum(counts.values()), section="エネルギー"))
    return dict(deck_code="code", total_cards=60, cards=cards)


class ClassificationTests(unittest.TestCase):
    def test_rules_are_versioned_japan_specific_and_external_reference_only(self):
        self.assertEqual(VERSION, "JP-2026-W02-v4")
        self.assertEqual(RULES["market"], "JP")
        self.assertFalse(RULES["external_taxonomy_reference"]["rules_imported"])
        self.assertFalse(RULES["external_taxonomy_reference"]["card_pool_equivalent"])
        self.assertTrue(RULES["policy"]["unknown_allowed"])

    def test_ogerpon_alone_is_not_parent_and_kangaskhan_is_low_priority_fallback(self):
        result = classify("code", deck(**{"オーガポン みどりのめんex": 4}), NOW)
        self.assertIsNone(result["parent_archetype"])
        self.assertEqual(result["classification_status"], "unknown")

        result = classify("code", deck(**{"メガガルーラex": 3}), NOW)
        self.assertEqual(result["parent_archetype"], "メガガルーラex")

        result = classify(
            "code", deck(**{"メガガルーラex": 3, "メガレックウザex": 2}), NOW)
        self.assertEqual(result["parent_archetype"], "メガレックウザex")
        evidence = json.loads(result["evidence"])
        self.assertIn("メガガルーラex",
                      [x["name"] for x in evidence["suppressed_parent_rules"]])

    def test_high_confidence_w02_rules_and_priority(self):
        cases = [
            ({"Nのゾロアークex": 3}, "Nのゾロアークex", []),
            ({"メガルカリオex": 2}, "メガルカリオex", []),
            ({"お祭り会場": 4, "バチンキー": 4, "カミッチュ": 4}, "おまつりおんど", ["カミッチュ型"]),
            ({"フーディン": 2, "ユンゲラー": 2}, "フーディン", []),
            ({"ジュペッタ": 2, "ダダリン": 3, "フーディン": 2, "ユンゲラー": 2},
             "ジュペッタ／ダダリン", []),
            ({"メタング": 3, "ゲノセクトex": 2, "メガドリュウズex": 2},
             "メタング／ゲノセクトex", ["メガドリュウズex型"]),
            ({"ゲッコウガex": 2, "メガゲッコウガex": 2},
             "メガゲッコウガex", ["ゲッコウガex採用"]),
            ({"メガサメハダーex": 2, "ストリンダー": 2},
             "メガサメハダーex", ["ストリンダー型"]),
            ({"シロナのガブリアスex": 2}, "シロナのガブリアスex", []),
            ({"マリィのオーロンゲex": 2}, "マリィのオーロンゲex", []),
            ({"ロケット団のワナイダー": 3, "ロケット団のミュウツーex": 1},
             "ロケット団のワナイダー", ["ロケット団のミュウツーex採用"]),
            ({"メガミミロップex": 2}, "メガミミロップex", []),
            ({"ソウブレイズex": 2}, "ソウブレイズex", []),
        ]
        for counts, parent, tags in cases:
            with self.subTest(parent=parent):
                result = classify("code", deck(**counts), NOW)
                self.assertEqual(result["parent_archetype"], parent)
                self.assertEqual(result["variant_tags"], tags)
                self.assertEqual(result["classification_status"], "classified")

    def test_festival_lead_parent_and_attacker_variants(self):
        cases = [
            ({"お祭り会場": 4, "バチンキー": 4, "カミッチュ": 4, "アズマオウ": 1},
             ["カミッチュ型"]),
            ({"お祭り会場": 4, "バチンキー": 4, "カミッチュ": 1, "アズマオウ": 3},
             ["アズマオウ型"]),
            ({"お祭り会場": 4, "バチンキー": 4, "カミッチュ": 2, "アズマオウ": 2},
             []),
        ]
        for counts, tags in cases:
            with self.subTest(counts=counts):
                result = classify("code", deck(**counts), NOW)
                self.assertEqual(result["parent_archetype"], "おまつりおんど")
                self.assertEqual(result["variant_tags"], tags)
                self.assertEqual(result["classification_status"], "classified")

        self.assertIsNone(classify(
            "code", deck(**{"お祭り会場": 4, "バチンキー": 2, "カミッチュ": 4}), NOW
        )["parent_archetype"])
        self.assertIsNone(classify(
            "code", deck(**{"お祭り会場": 2, "バチンキー": 4, "アズマオウ": 3}), NOW
        )["parent_archetype"])

    def test_bullet_and_rocket_honchkrow_rules(self):
        bullet = classify("code", deck(**{
            "オーガポン みどりのめんex": 4,
            "メガガルーラex": 2,
            "ニャースex": 2,
            "ラティアスex": 2,
            "リーリエのピッピex": 2,
        }), NOW)
        self.assertEqual(bullet["parent_archetype"], "メガガルーラ・オーガポンバレット")
        self.assertEqual(bullet["classification_status"], "classified")

        specific = classify("code", deck(**{
            "タケルライコex": 2,
            "オーガポン みどりのめんex": 4,
            "メガガルーラex": 2,
            "ニャースex": 2,
            "ラティアスex": 2,
            "リーリエのピッピex": 2,
        }), NOW)
        self.assertEqual(specific["parent_archetype"], "タケルライコex")

        honchkrow = classify("code", deck(**{
            "ロケット団のヤミカラス": 4,
            "ロケット団のドンカラス": 3,
        }), NOW)
        self.assertEqual(honchkrow["parent_archetype"], "ロケット団のドンカラス")
        self.assertEqual(honchkrow["classification_status"], "classified")

    def test_v4_new_parents_bullets_and_slowking_drakloak(self):
        cases = [
            ({"メガシャンデラex": 3}, "メガシャンデラex", []),
            ({"メガスターミーex": 2, "メガユキメノコex": 2},
             "メガスターミーex／メガユキメノコex", []),
            ({"ブリジュラスex": 3, "ノココッチ": 2},
             "ブリジュラスex", ["ノココッチ型"]),
            ({"イイネイヌ": 3, "ガメノデス": 2},
             "イイネイヌ／ガメノデス", []),
            ({"ドデカバシ": 3, "ケララッパ": 2},
             "ドデカバシ", []),
            ({"ホーホー": 3, "ヨルノズク": 2, "ゼロの大空洞": 3},
             "宝石バレット", []),
            ({"ミュウex": 3, "テレパス超エネルギー": 4, "ワンダーパッチ": 2},
             "ミュウバレット", []),
            ({"メガディアンシーex": 2, "ヨノワール": 2},
             "メガディアンシーex", ["ヨノワール型"]),
            ({"メガライボルトex": 3}, "メガライボルトex", []),
            ({"ブースターex": 2}, "ブースターex", []),
            ({"ホルード": 2}, "ホルード", []),
            ({"ヤドキング": 2, "ドラパルトex": 2, "ドロンチ": 4},
             "ヤドキング", ["ドロンチ型"]),
            ({"ドラパルトex": 2, "ドロンチ": 4, "ドデカバシ": 3},
             "ドラパルトex／ドデカバシ", []),
        ]
        for counts, parent, tags in cases:
            with self.subTest(parent=parent):
                result = classify("code", deck(**counts), NOW)
                self.assertEqual(result["parent_archetype"], parent)
                self.assertEqual(result["variant_tags"], tags)
                self.assertEqual(result["classification_status"], "classified")

        relaxed = classify("code", deck(**{
            "オーガポン みどりのめんex": 3,
            "メガガルーラex": 2,
            "ラティアスex": 2,
            "リーリエのピッピex": 2,
        }), NOW)
        self.assertEqual(relaxed["parent_archetype"], "メガガルーラ・オーガポンバレット")

    def test_v4_bullet_fallbacks_do_not_override_specific_parents(self):
        jewel = classify("code", deck(**{
            "ドデカバシ": 3,
            "ケララッパ": 2,
            "ホーホー": 3,
            "ヨルノズク": 2,
            "ゼロの大空洞": 3,
        }), NOW)
        self.assertEqual(jewel["parent_archetype"], "ドデカバシ")

        mew = classify("code", deck(**{
            "メガミミロップex": 2,
            "ミュウex": 3,
            "テレパス超エネルギー": 4,
            "ワンダーパッチ": 2,
        }), NOW)
        self.assertEqual(mew["parent_archetype"], "メガミミロップex")

    def test_original_parent_and_variant_rules_remain_deterministic(self):
        cases = [
            ({"ドラパルトex": 2, "ノココッチ": 2, "ヨノワール": 1},
             "ドラパルトex", ["ノココッチ型", "ヨノワール型"]),
            ({"カミツオロチex": 2, "オーガポン みどりのめんex": 4},
             "カミツオロチex", ["オーガポン みどりのめんex型"]),
            ({"オリーヴァex": 2, "オーガポン みどりのめんex": 4},
             "オリーヴァex", ["オーガポン みどりのめんex型"]),
            ({"メガフシギバナex": 2, "オーガポン みどりのめんex": 4},
             "メガフシギバナex", ["オーガポン みどりのめんex型"]),
            ({"タケルライコex": 2, "オーガポン みどりのめんex": 4},
             "タケルライコex", ["オーガポン みどりのめんex型"]),
        ]
        for counts, parent, tags in cases:
            with self.subTest(parent=parent):
                result = classify("code", deck(**counts), NOW)
                self.assertEqual(result["parent_archetype"], parent)
                self.assertEqual(result["variant_tags"], tags)

    def test_equal_priority_collision_stays_unknown(self):
        result = classify(
            "code", deck(**{"メガレックウザex": 2, "タケルライコex": 2}), NOW)
        self.assertIsNone(result["parent_archetype"])
        self.assertEqual(result["classification_status"], "unknown")
        self.assertEqual(json.loads(result["evidence"])["reason"],
                         "parent_collision_equal_priority")

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

    def test_atomic_batch_migrates_old_auto_version_but_preserves_manual(self):
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
            self.assertEqual(
                json.loads(output.read_text())["records"][0]["classifier_version"], VERSION)

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
