"""2027 S1 City League weekly calendar: Monday through Sunday in JST.

This changes analysis windows only. It does not alter collection frequency,
historical immutable snapshots, or pre-recorded forecasts.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

CALENDAR_ID = "2027-s1-mon-sun-v1"
SEASON_START = date(2026, 9, 26)
W01_MONDAY = date(2026, 9, 21)


def week_period(week_id: str) -> tuple[date, date]:
    """Return both inclusive local event dates for a W01-style identifier."""
    if not isinstance(week_id, str) or not re.fullmatch(r"W[0-9]{2}", week_id):
        raise ValueError("Week ID must be W01 through W99")
    number = int(week_id[1:])
    if number < 1:
        raise ValueError("Week number must be positive")
    start = W01_MONDAY + timedelta(weeks=number - 1)
    return start, start + timedelta(days=6)


def week_for_event(day: date | str) -> str:
    """Return the season-relative week. Reject dates before the first event."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    if not isinstance(day, date) or day < SEASON_START:
        raise ValueError("Event precedes 2027 S1")
    number = (day - W01_MONDAY).days // 7 + 1
    if number > 99:
        raise ValueError("Week number exceeds the two-digit format")
    return f"W{number:02d}"


def week_event_ids(city: dict, week_id: str) -> list[str]:
    """Use real event dates; include all categories before regional filtering."""
    start, end = week_period(week_id)
    rows = [e for e in city.get("events", [])
            if e.get("date") and start <= date.fromisoformat(e["date"]) <= end]
    ids = [e["event_id"] for e in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate event IDs in source")
    return sorted(ids)
