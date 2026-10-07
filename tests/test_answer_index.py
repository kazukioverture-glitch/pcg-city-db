import json
import tempfile
import unittest
from pathlib import Path

from analysis.answer_index import (
    build_deck_index, find_latest_final_snapshot, previous_week_period)


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

    def test_previous_week_period_is_exactly_seven_days_back(self):
        metadata = {
            "schema_version": "1.2.2",
            "period_start": "2026-10-07T00:00:00+09:00",
            "period_end": "2026-10-13T23:59:59+09:00",
        }
        self.assertEqual(
            previous_week_period(metadata),
            ("2026-09-30", "2026-10-06"))

    def test_latest_final_snapshot_uses_period_end_not_directory_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "data/analysis/weekly_snapshots"
            for week, end, snapshot in (
                    ("W09", "2026-10-06T23:59:59+09:00", "older"),
                    ("W03", "2026-10-13T23:59:59+09:00", "newer")):
                directory = base / week / "FINAL" / snapshot
                directory.mkdir(parents=True)
                (directory / "metadata.json").write_text(json.dumps({
                    "schema_version": "1.2.2",
                    "kind": "weekly_snapshot",
                    "snapshot_id": snapshot,
                    "week_id": week,
                    "analysis_stage": "FINAL",
                    "period_start": "2026-10-07T00:00:00+09:00",
                    "period_end": end,
                    "cutoff_datetime": end,
                }), encoding="utf-8")
            directory, metadata = find_latest_final_snapshot(root)
            self.assertEqual(metadata["snapshot_id"], "newer")
            self.assertEqual(directory.name, "newer")


if __name__ == "__main__":
    unittest.main()
