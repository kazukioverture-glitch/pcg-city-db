"""Regression tests for official collection delegated to Android/Termux."""
import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collector.validate_snapshot import validate_snapshot, write_candidate

sync_module = importlib.import_module("collector.sync")


class OfficialDelegationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        data = self.root / "data"
        (data / "analysis").mkdir(parents=True)
        self.city = {
            "season": "2027-S1", "season_start": "2026-09-26",
            "updated_at": "2026-10-12T22:35:52+09:00",
            "events": [{"event_id": "test-1", "date": "2026-10-12",
                        "placements": [{"rank": 1, "player_id": "p1",
                                        "deck_code": "test-deck"}]}],
        }
        self.decks = {
            "season": "2027-S1", "updated_at": "2026-10-12T22:35:52+09:00",
            "decks": {"test-deck": {"total_cards": 60, "cards": [{"count": 60}],
                                     "usages": []}},
        }
        write_candidate(data, copy.deepcopy(self.city), copy.deepcopy(self.decks))
        (data / "analysis/event_ledger.json").write_text("{}", encoding="utf-8")

    def download(self, success=True):
        def fake_download_pair(url, directory):
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            for name, value in (("city_db", self.city), ("city_decks", self.decks)):
                (directory / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
            return {name: {"success": success}
                    for name in ("city-db.json", "city-decks.json")}
        return fake_download_pair

    def run_sync(self, success=True):
        with (patch.object(sync_module, "download_pair", side_effect=self.download(success)),
              patch.object(sync_module, "run_official",
                           side_effect=AssertionError("GitHub direct fetch forbidden")) as official,
              patch.object(sync_module, "sync_ledger"),
              patch.object(sync_module, "read_state"),
              patch.object(sync_module.subprocess, "run") as subprocess_run):
            subprocess_run.return_value.returncode = 0
            if not success:
                with self.assertRaisesRegex(RuntimeError, "Neither source succeeded"):
                    sync_module.sync(root=self.root, base_url="https://example.test")
                official.assert_not_called()
                return None
            report = sync_module.sync(root=self.root, base_url="https://example.test")
            official.assert_not_called()
            subprocess_run.assert_called_once()
            return report

    def test_termux_feed_skips_direct_official_api(self):
        report = self.run_sync()
        self.assertTrue(report["termux_accepted"])
        self.assertTrue(report["published"])
        self.assertFalse(report["official_accepted"])
        self.assertFalse(report["official_complete"])
        self.assertEqual(report["official_source_mode"], "termux")
        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["placement_count"], 1)
        self.assertEqual(report["valid_60_count"], 1)

    def test_failed_feed_preserves_all_stored_files(self):
        data = self.root / "data"
        names = ("city_db.json", "city_decks.json", "index.json",
                 "analysis/event_ledger.json")
        before = {name: (data / name).read_bytes() for name in names}
        self.run_sync(success=False)
        self.assertEqual(before, {name: (data / name).read_bytes() for name in names})

    def test_validator_rejects_invalid_cards_and_duplicate_events(self):
        broken = copy.deepcopy(self.decks)
        broken["decks"]["test-deck"]["cards"][0]["count"] = 59
        with self.assertRaisesRegex(ValueError, "Invalid 60-card deck"):
            validate_snapshot(self.city, broken)
        duplicate = copy.deepcopy(self.city)
        duplicate["events"].append(copy.deepcopy(duplicate["events"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate event identity"):
            validate_snapshot(duplicate, self.decks)


if __name__ == "__main__":
    unittest.main()
