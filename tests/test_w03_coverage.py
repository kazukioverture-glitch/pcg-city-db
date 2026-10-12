import unittest
from datetime import date

from analysis.w03_coverage import audit_week_coverage


class WeekCoverageAuditTests(unittest.TestCase):
    def test_missing_schedule_has_unknown_category_not_open_zero(self):
        city = {"events": [
            {"event_id": "1", "date": "2026-10-11", "category": "オープン",
             "placements": [{"rank": i} for i in range(1, 9)],
             "top16_observed_rows": 8, "top16_checked_at": "2026-10-12T10:00:00+09:00"},
            {"event_id": "2", "date": "2026-10-10", "category": "ジュニア",
             "placements": [{"rank": 1}]},
        ]}
        ledger = {"records": [
            {"event_id": "1", "event_date": "2026-10-11", "category": "オープン"},
            {"event_id": "2", "event_date": "2026-10-10", "category": "ジュニア"},
            {"event_id": "3", "event_date": "2026-10-11", "category": None},
            {"event_id": "4", "event_date": "2026-10-11", "category": "オープン"},
            {"event_id": "5", "event_date": "2026-10-12", "category": None},
        ]}
        got = audit_week_coverage(city, ledger, date(2026, 10, 5), date(2026, 10, 11))
        self.assertEqual(got["scheduled_only_all_categories"], 2)
        self.assertEqual(got["scheduled_only_unknown_category"], 1)
        self.assertEqual(got["scheduled_only_known_category"], 1)
        self.assertEqual(got["observed_result_events_category"], 1)
        self.assertEqual(got["observed_top8_rows_category"], 8)
        self.assertEqual(got["top16_incomplete_result_events_category"], 1)
        self.assertFalse(got["full_week_coverage_verified"])

    def test_complete_top16_requires_observed_and_captured_eight(self):
        city = {"events": [
            {"event_id": "a", "date": "2026-10-11", "category": "オープン",
             "placements": [{"rank": i} for i in range(1, 17)],
             "top16_observed_rows": 8, "top16_checked_at": "2026-10-12T10:00:00+09:00"},
            {"event_id": "b", "date": "2026-10-11", "category": "オープン",
             "placements": [{"rank": i} for i in range(1, 12)],
             "top16_observed_rows": 8, "top16_checked_at": "2026-10-12T10:00:00+09:00"},
        ]}
        got = audit_week_coverage(city, {"records": []}, date(2026, 10, 5), date(2026, 10, 11))
        self.assertEqual(got["top16_complete_result_events_category"], 1)
        self.assertEqual(got["top16_incomplete_result_events_category"], 1)
        self.assertEqual(got["observed_daily_results_category"]["2026-10-11"]["top16_complete_events"], 1)

    def test_duplicate_event_ids_rejected(self):
        city = {"events": [
            {"event_id": "1", "date": "2026-10-11"},
            {"event_id": "1", "date": "2026-10-11"},
        ]}
        with self.assertRaises(ValueError):
            audit_week_coverage(city, {"records": []}, date(2026, 10, 5), date(2026, 10, 11))


if __name__ == "__main__":
    unittest.main()
