import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analysis.collection import sync_ledger, sync_schedule_snapshot
from analysis.state import read_state
from collector import schedule
from collector import sync_official_api as official


class ScheduleVenueTests(unittest.TestCase):
    def test_schedule_exact_day_minimal_fields_and_dynamic_offset(self):
        pages = [
            {"code": 200, "eventCount": 3, "event": [
                {"event_holding_id": 1, "event_date_params": "20261007",
                 "event_title": "シティリーグ2027 シーズン1 オープンリーグ",
                 "shop_name": "店舗A", "prefecture_name": "愛知県"},
                {"event_holding_id": 2, "event_date_params": "20261007",
                 "event_title": "別大会", "shop_name": "除外", "prefecture_name": "東京都"},
            ]},
            {"code": 200, "eventCount": 3, "event": [
                {"event_holding_id": 3, "event_date_params": "20261007",
                 "event_title": "シティリーグ2027 シーズン1 オープンリーグ",
                 "shop_name": "店舗B", "prefecture_name": "大阪府"},
            ]},
        ]
        calls = []
        def fake(_url, params):
            calls.append(dict(params))
            return pages.pop(0)
        with patch.object(schedule, "fetch_json", side_effect=fake):
            events = schedule.discover_schedule("2026-10-07")
        self.assertEqual([e["event_id"] for e in events], ["1", "3"])
        self.assertEqual(events[0]["venue_name"], "店舗A")
        self.assertEqual(events[0]["venue_prefecture"], "愛知県")
        self.assertEqual([c["offset"] for c in calls], [0, 2])
        self.assertTrue(all(c["start_date"] == c["end_date"] == "2026-10-07" for c in calls))

    def test_schedule_ledger_only_records_changes_and_preserves_player_region_separation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "ledger.json"
            snapshot = root / "schedule.json"
            snapshot.write_text(json.dumps({
                "source": official.EVENT_SEARCH_URL,
                "updated_at": "2026-10-06T20:00:00+09:00",
                "target_date": "2026-10-07",
                "event_count": 1,
                "events": [{"event_id": "1", "date": "2026-10-07",
                            "venue_name": "店舗A", "venue_prefecture": "愛知県"}],
            }), encoding="utf-8")
            self.assertEqual(sync_schedule_snapshot(ledger, snapshot), 1)
            self.assertEqual(sync_schedule_snapshot(ledger, snapshot), 0)
            record = read_state(ledger)["records"][0]
            self.assertEqual(record["event_status"], "scheduled")
            self.assertEqual(record["venue_prefecture"], "愛知県")
            self.assertEqual(len(record["observations"]), 1)

            city = root / "city.json"
            city.write_text(json.dumps({"updated_at": "2026-10-07T18:00:00+09:00", "events": [
                {"event_id": "1", "date": "2026-10-07", "category": "オープン",
                 "venue_name": "店舗A", "venue_prefecture": "愛知県",
                 "placements": [{"rank": 1, "player_id": "p1", "prefecture": "岐阜県"}]}
            ]}), encoding="utf-8")
            sync_ledger(ledger, city_path=city, fetch_status="success",
                        coverage_scope="collection_feed", source_url="https://example.invalid/feed")
            record = read_state(ledger)["records"][0]
            self.assertEqual(record["event_status"], "scheduled")
            self.assertEqual(record["venue_prefecture"], "愛知県")
            self.assertEqual(record["players"][0]["player_prefecture"], "岐阜県")

    def test_result_discovery_carries_shop_prefecture_and_does_not_skip_offsets(self):
        pages = [
            {"code": 200, "eventCount": 3, "event": [
                {"event_holding_id": 1, "event_date_params": "20261007", "event_type": 2,
                 "event_title": "シティリーグ2027 シーズン1", "leagueName": "オープン",
                 "shop_name": "店舗A", "prefecture_name": "愛知県"},
                {"event_holding_id": 2, "event_date_params": "20261006", "event_type": 2,
                 "event_title": "シティリーグ2027 シーズン1", "leagueName": "オープン",
                 "shop_name": "店舗B", "prefecture_name": "大阪府"},
            ]},
            {"code": 200, "eventCount": 3, "event": [
                {"event_holding_id": 3, "event_date_params": "20261005", "event_type": 2,
                 "event_title": "シティリーグ2027 シーズン1", "leagueName": "オープン",
                 "shop_name": "店舗C", "prefecture_name": "京都府"},
            ]},
        ]
        calls = []
        def fake(_url, params):
            calls.append(dict(params))
            return pages.pop(0)
        with patch.object(official, "fetch_json", side_effect=fake):
            events = official.discover_recent_city_events("2026-10-05", 5)
        self.assertEqual([c["offset"] for c in calls], [0, 2])
        by_id = {e["event_id"]: e for e in events}
        self.assertEqual(by_id["1"]["venue_name"], "店舗A")
        self.assertEqual(by_id["1"]["venue_prefecture"], "愛知県")


if __name__ == "__main__":
    unittest.main()
