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

    def test_repeated_accepted_feed_does_not_replace_unchanged_db(self):
        self.run_sync()
        report = self.run_sync()
        self.assertEqual(report['changed_files'], [])
        self.assertEqual(report['db_bytes_written'], 0)
        self.assertEqual(report['result_coverage_status'], 'not_verified')
        self.assertEqual(report['final_status'], 'not_evaluated')

    def test_stale_feed_is_visible_even_when_stored_db_is_newer(self):
        self.run_sync()
        self.city['updated_at'] = '2000-01-01T00:00:00+09:00'
        self.decks['updated_at'] = self.city['updated_at']
        report = self.run_sync()
        self.assertTrue(report['termux_source_health']['source_stale_24h'])
        self.assertEqual(report['termux_source_health']['source_updated_at'], self.city['updated_at'])
        self.assertNotEqual(report['source_updated_at'], self.city['updated_at'])

    def test_pinned_manifest_checks_exactly_one_batch(self):
        from collector import sync_official_api as official
        from unittest.mock import patch
        manifest = self.root / "manifest.json"
        candidates = [
            {"event_id": "batch-a", "date": "2026-10-05", "category": "オープン"},
            {"event_id": "batch-b", "date": "2026-10-06", "category": "オープン"},
        ]
        manifest.write_text(json.dumps(candidates), encoding="utf-8")
        old_paths = {name: getattr(official, name)
                     for name in ("DATA", "TMP", "CITY_PATH", "DECKS_PATH",
                                  "INDEX_PATH", "PARTIAL_CITY_PATH",
                                  "PARTIAL_DECKS_PATH", "CHANGED_FLAG")}
        args = ["sync_official_api", "--data-dir", str(self.root / "data"),
                "--tmp-dir", str(self.root / "audit"),
                "--discovery-file", str(manifest),
                "--batch-offset", "1", "--max-detail-events", "1"]
        with (patch.multiple(official, **old_paths),
              patch("sys.argv", args),
              patch.object(official, "discover_recent_city_events",
                           side_effect=AssertionError("Manifest must suppress re-discovery")),
              patch.object(official, "result_event_from_api", return_value=None) as result):
            self.assertEqual(official.main(), 0)
            result.assert_called_once_with("batch-b")
        audit = json.loads((self.root / "audit/official_audit.json").read_text())
        self.assertEqual(audit["discovered_event_count"], 2)
        self.assertEqual(audit["selected_event_count"], 1)
        self.assertEqual([r["event_id"] for r in audit["events"]], ["batch-b"])

    def test_one_time_reconciliation_driver_imports(self):
        import scripts.termux_reconcile_w03 as driver
        self.assertEqual(driver.END, "2026-10-11")
        self.assertEqual(driver.START, "2026-09-21")

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
