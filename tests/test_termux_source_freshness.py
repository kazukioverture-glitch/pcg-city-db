import unittest
from datetime import datetime, timedelta, timezone

from collector.sync import source_health


class AndroidSourceFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)

    def test_stale_snapshot_is_reported_even_if_http_transport_succeeds(self):
        city = {"updated_at": "2026-10-10T22:48:50+09:00",
                "events": [{"date": "2026-10-10"}]}
        status = source_health(city, self.now)
        self.assertTrue(status["source_stale_24h"])
        self.assertEqual(status["latest_observed_event_date"], "2026-10-10")
        self.assertGreater(status["source_age_hours"], 24)

    def test_recent_timestamp_is_not_labelled_stale(self):
        city = {"updated_at": "2026-10-12T11:00:00+09:00", "events": []}
        status = source_health(city, self.now)
        self.assertFalse(status["source_stale_24h"])
        self.assertEqual(status["source_age_hours"], 1.0)
        self.assertIsNone(status["latest_observed_event_date"])

    def test_unknown_source_timestamp_remains_unknown_not_fresh(self):
        city = {"updated_at": "bad-date", "events": [{"date": "2026-10-11"}]}
        status = source_health(city, self.now)
        self.assertTrue(status["source_stale_24h"])
        self.assertIsNone(status["source_age_hours"])

    def test_source_health_never_changes_original_snapshot(self):
        city = {"updated_at": "2026-10-10T22:48:50+09:00",
                "events": [{"date": "2026-10-09", "placements": [1]}]}
        before = repr(city)
        source_health(city, self.now)
        self.assertEqual(repr(city), before)


if __name__ == "__main__":
    unittest.main()
