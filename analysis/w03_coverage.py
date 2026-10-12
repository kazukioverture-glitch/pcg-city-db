"""Evidence-only weekly coverage audit, independent of archetype classification.

Schedule-only events remain UNKNOWN, not cancellations, unpublished results or
Top8 zeros. The Top16 denominator consists solely of fully observed tournaments.
"""
from __future__ import annotations

from datetime import date


def _in_range(value, start, end):
    if not isinstance(value, str):
        return False
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return False
    return start <= day <= end


def audit_week_coverage(city, ledger, week_start, week_end, category="オープン"):
    """Return category-safe ledger vs observed-result discrepancies."""
    city_events = [
        e for e in city.get("events", [])
        if _in_range(e.get("date"), week_start, week_end)
    ]
    ledger_events = [
        e for e in ledger.get("records", [])
        if _in_range(e.get("event_date"), week_start, week_end)
    ]
    observed = {str(e["event_id"]): e for e in city_events}
    scheduled = {str(e["event_id"]): e for e in ledger_events}
    if len(observed) != len(city_events) or len(scheduled) != len(ledger_events):
        raise ValueError("Duplicate week event IDs")
    scheduled_only = [e for eid, e in scheduled.items() if eid not in observed]
    categorized = [e for e in city_events if e.get("category") == category]
    category_unknown = [e for e in scheduled_only if e.get("category") is None]
    category_known_missing = [e for e in scheduled_only if e.get("category") == category]
    top16_complete, top16_incomplete, captured_top8, day_stats = 0, 0, 0, {}
    # Exclusion is not proof of a missing result. Below-16 attendance and
    # selective official publication can produce fewer than eight extra ranks.
    reason_counts = {
        "not_checked": 0,
        "observed_rows_not_captured": 0,
        "source_fewer_than_eight_rows_reason_unknown": 0,
        "observation_inconsistent_or_unknown": 0,
    }
    excluded_event_ids_by_reason = {key: [] for key in reason_counts}
    for event in categorized:
        d = event["date"]
        counters = day_stats.setdefault(d, {"result_events": 0, "top8_rows": 0,
                                             "top16_complete_events": 0})
        counters["result_events"] += 1
        placements = event.get("placements") or []
        top8_count = sum(isinstance(p, dict) and type(p.get("rank")) is int
                         and 1 <= p["rank"] <= 8 for p in placements)
        extra_count = sum(isinstance(p, dict) and type(p.get("rank")) is int
                          and 9 <= p["rank"] <= 16 for p in placements)
        observed_extra = event.get("top16_observed_rows")
        checked = bool(event.get("top16_checked_at"))
        complete = (
            checked and type(observed_extra) is int and observed_extra >= 8
            and extra_count >= observed_extra
        )
        if not complete:
            if not checked:
                reason = "not_checked"
            elif type(observed_extra) is not int or observed_extra < 0:
                reason = "observation_inconsistent_or_unknown"
            elif extra_count < observed_extra:
                reason = "observed_rows_not_captured"
            elif observed_extra < 8:
                # Could reflect a small tournament or limited published ranks;
                # rank-row count is NOT a verified actual participant count.
                reason = "source_fewer_than_eight_rows_reason_unknown"
            else:
                reason = "observation_inconsistent_or_unknown"
            reason_counts[reason] += 1
            excluded_event_ids_by_reason[reason].append(str(event["event_id"]))
        counters["top8_rows"] += top8_count
        counters["top16_complete_events"] += int(complete)
        captured_top8 += top8_count
        top16_complete += int(complete)
        top16_incomplete += int(not complete)
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "category": category,
        "scheduled_and_result_ledger_event_ids": len(set(observed) | set(scheduled)),
        "observed_result_events_all_categories": len(city_events),
        "observed_result_events_category": len(categorized),
        "observed_top8_rows_category": captured_top8,
        "scheduled_only_all_categories": len(scheduled_only),
        "scheduled_only_unknown_category": len(category_unknown),
        "scheduled_only_known_category": len(category_known_missing),
        "scheduled_only_event_ids": sorted(e["event_id"] for e in scheduled_only),
        "top16_complete_result_events_category": top16_complete,
        "top16_incomplete_result_events_category": top16_incomplete,
        # Read this legacy key as comparison-excluded, not confirmed fetch failures.
        "top16_excluded_reason_counts": reason_counts,
        "top16_excluded_event_ids_by_reason": excluded_event_ids_by_reason,
        "top16_exclusion_reason_caveat": (
            "A source showing fewer than eight ranks 9-16 can reflect fewer than "
            "16 actual entrants, limited publication, or another cause. Neither "
            "published ranking row counts nor event capacity prove attendance."
        ),
        "observed_daily_results_category": dict(sorted(day_stats.items())),
        "scheduled_only_are_unknown_not_zero": True,
        "all_scheduled_events_checked": False,
        "full_week_coverage_verified": False,
    }
