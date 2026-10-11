"""Regression checks for Monday-Sunday restatement without old FINAL mutation."""
import unittest
from datetime import date

from analysis.week_calendar import (CALENDAR_ID, SEASON_START, week_event_ids,
                                    week_for_event, week_period)


class CalendarTests(unittest.TestCase):
    def test_first_week_is_partial_season_start_and_sunday_end(self):
        self.assertEqual(CALENDAR_ID, "2027-s1-mon-sun-v1")
        self.assertEqual(SEASON_START.isoformat(), "2026-09-26")
        self.assertEqual(tuple(d.isoformat() for d in week_period("W01")),
                         ("2026-09-21", "2026-09-27"))
        self.assertEqual(tuple(d.isoformat() for d in week_period("W02")),
                         ("2026-09-28", "2026-10-04"))
        self.assertEqual(tuple(d.isoformat() for d in week_period("W03")),
                         ("2026-10-05", "2026-10-11"))

    def test_boundary_moves_old_monday_tuesday_into_following_week(self):
        for day, week in (("2026-09-26", "W01"), ("2026-09-27", "W01"),
                          ("2026-09-28", "W02"), ("2026-09-29", "W02"),
                          ("2026-10-04", "W02"), ("2026-10-05", "W03"),
                          ("2026-10-06", "W03"), ("2026-10-11", "W03"),
                          ("2026-10-12", "W04")):
            with self.subTest(day=day):
                self.assertEqual(week_for_event(day), week)
                self.assertEqual(week_for_event(date.fromisoformat(day)), week)
        with self.assertRaises(ValueError):
            week_for_event("2026-09-25")
        for invalid in ("W00", "W1", "2026-W39", "W01/..", "W100"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                week_period(invalid)

    def test_event_ids_are_week_scoped_but_do_not_filter_category(self):
        city = {"events": [
            {"event_id": "a", "date": "2026-09-27", "category": "オープン"},
            {"event_id": "c", "date": "2026-09-28", "category": "ジュニア"},
            {"event_id": "b", "date": "2026-09-29", "category": "オープン"},
            {"event_id": "d", "date": "2026-10-05", "category": "シニア"},
        ]}
        self.assertEqual(week_event_ids(city, "W01"), ["a"])
        self.assertEqual(week_event_ids(city, "W02"), ["b", "c"])
        self.assertEqual(week_event_ids(city, "W03"), ["d"])
        city["events"].append({"event_id": "c", "date": "2026-09-30"})
        with self.assertRaises(ValueError):
            week_event_ids(city, "W02")


if __name__ == "__main__":
    unittest.main()
