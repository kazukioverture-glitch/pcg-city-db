import unittest

from analysis.answer_index import build_deck_index


def card(card_id, name, count=1):
    return {"card_id": card_id, "name": name, "count": count, "section": "ポケモン (60)"}


def deck(code, cards):
    total = sum(c["count"] for c in cards)
    return {"deck_code": code, "total_cards": 60,
            "cards": cards + [{"card_id": "E", "name": "基本エネルギー",
                               "count": 60-total, "section": "エネルギー"}]}


class AnswerIndexTests(unittest.TestCase):
    def test_compact_rows_preserve_rank_region_and_classification(self):
        metadata = {
            "schema_version": "1.2.2", "kind": "weekly_snapshot",
            "snapshot_id": "snap", "week_id": "W02", "analysis_stage": "FINAL",
            "period_start": "2026-09-30T00:00:00+09:00",
            "period_end": "2026-10-06T23:59:59+09:00",
            "event_ids": ["A", "J"],
        }
        city = {"events": [
            {"event_id": "A", "date": "2026-10-01", "category": "オープン",
             "venue_prefecture": "愛知県",
             "placements": [{"rank": 1, "deck_code": "D1"},
                            {"rank": 2, "deck_code": "D2"}]},
            {"event_id": "J", "date": "2026-10-02", "category": "ジュニア",
             "venue_prefecture": "愛知県",
             "placements": [{"rank": 1, "deck_code": "D1"}]},
        ]}
        decks = {"decks": {
            "D1": deck("D1", [card("x", "X")]),
            "D2": deck("D2", [card("y", "Y")]),
        }}
        classifications = {"kind": "deck_classifications", "records": [
            {"deck_code": "D1", "parent_archetype": "A",
             "variant_tags": ["v型"], "classification_status": "classified"},
        ]}
        result = build_deck_index(
            metadata, city, decks, classifications,
            generated_at="2026-10-07T00:00:00+00:00")
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["valid_60_count"], 2)
        self.assertEqual(result["rows"][0]["rank"], 1)
        self.assertEqual(result["rows"][0]["venue_prefecture"], "愛知県")
        self.assertEqual(result["rows"][0]["parent_archetype"], "A")
        self.assertEqual(result["rows"][0]["variant_tags"], ["v型"])
        self.assertIsNone(result["rows"][1]["parent_archetype"])
        self.assertTrue(result["rows"][1]["valid_60"])


if __name__ == "__main__":
    unittest.main()
