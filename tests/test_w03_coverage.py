import unittest
from datetime import date

from analysis.w03_coverage import audit_week_coverage
from analysis.weekly import audit_report


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

    def test_excluded_top16_causes_do_not_assume_small_attendance_or_fetch_failure(self):
        def event(event_id, observed, captured, checked):
            return {
                "event_id": event_id, "date": "2026-10-11", "category": "オープン",
                "placements": ([{"rank": i} for i in range(1, 9)]
                               + [{"rank": i} for i in range(9, 9 + captured)]),
                "top16_observed_rows": observed,
                "top16_checked_at": "2026-10-12T10:00:00+09:00" if checked else None,
            }
        city = {"events": [
            event("not-checked", 0, 0, False),
            event("only-four-source-rows", 4, 4, True),
            event("fetch-pending", 8, 4, True),
            event("complete", 8, 8, True),
        ]}
        got = audit_week_coverage(city, {"records": []}, date(2026, 10, 5), date(2026, 10, 11))
        reasons = got["top16_excluded_reason_counts"]
        self.assertEqual(got["top16_complete_result_events_category"], 1)
        self.assertEqual(got["top16_incomplete_result_events_category"], 3)
        self.assertEqual(reasons["not_checked"], 1)
        self.assertEqual(reasons["source_fewer_than_eight_rows_reason_unknown"], 1)
        self.assertEqual(reasons["observed_rows_not_captured"], 1)
        self.assertEqual(reasons["observation_inconsistent_or_unknown"], 0)
        self.assertEqual(sum(reasons.values()), 3)
        self.assertEqual(
            got["top16_excluded_event_ids_by_reason"]["source_fewer_than_eight_rows_reason_unknown"],
            ["only-four-source-rows"],
        )
        self.assertIn("actual entrants", got["top16_exclusion_reason_caveat"])

    def test_full_week_unverified_never_passes_audit(self):
        # All other audited dimensions pass; coverage alone must keep WARN.
        report = {
            "current_week": {
                "national": {
                    "analysis": {
                        "conversion": [],
                        "classification_coverage_top8": {"unclassified_count": 0},
                        "card_analysis_top8": {"watch_cards": {"status": "configured"}},
                    },
                },
            },
            "trend": {"status": "not_requested"},
            "external_analysis": {"status": "available"},
            "coverage_gate": {
                "full_week_coverage_verified": False,
                "scheduled_only_all_categories": 12,
            },
        }
        result = audit_report(report)
        self.assertEqual(result["status"], "WARN")
        self.assertIn("FULL_WEEK_COVERAGE_UNVERIFIED",
                      [finding["code"] for finding in result["findings"]])
        report["coverage_gate"]["full_week_coverage_verified"] = True
        self.assertEqual(audit_report(report)["status"], "PASS")
        del report["coverage_gate"]
        self.assertEqual(audit_report(report)["status"], "PASS")

    def test_duplicate_event_ids_rejected(self):
        city = {"events": [
            {"event_id": "1", "date": "2026-10-11"},
            {"event_id": "1", "date": "2026-10-11"},
        ]}
        with self.assertRaises(ValueError):
            audit_week_coverage(city, {"records": []}, date(2026, 10, 5), date(2026, 10, 11))


if __name__ == "__main__":
    unittest.main()
