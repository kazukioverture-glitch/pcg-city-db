"""Top16 collection/analysis contract, offline and independent of the official API."""
import unittest
from datetime import date
from unittest.mock import patch

from analysis.weekly import _scope_analysis, _top16_context
from collector.sync_official_api import result_event_from_api, select_event_candidates
from collector.merge_termux_snapshot import merge_snapshot


def placement(rank):
    return {
        "rank": rank, "point": 25, "player_id": str(rank),
        "name": f"player-{rank}", "deck_id": None,
        "area": "愛知県", "show_profile": False,
    }


def event_with_rows(count, *, observed=8, checked=True):
    return {
        "event_id": "100", "date": "2026-10-08",
        "category": "オープン", "venue_prefecture": "愛知県",
        "placements": [
            {"rank": rank, "deck_code": None} for rank in range(1, count + 1)
        ],
        "top16_observed_rows": observed,
        "top16_checked_at": "2026-10-08T23:00:00+09:00" if checked else None,
    }


class ResultPaginationTests(unittest.TestCase):
    def test_fetches_sixteen_without_including_rank_seventeen(self):
        calls = []

        def fake_fetch(_url, params):
            offset = params["offset"]
            calls.append(offset)
            rows = [placement(i) for i in range(offset + 1, min(24, offset + 8) + 1)]
            return {
                "code": 200,
                "event": {"eventDate": {"date": "2026-10-08"}, "league": "オープン"},
                "results": rows, "count": 24,
            }

        with patch("collector.sync_official_api.fetch_json", side_effect=fake_fetch):
            result = result_event_from_api("100")
        self.assertEqual([p["rank"] for p in result["placements"]], list(range(1, 17)))
        self.assertEqual(result["top16_observed_rows"], 8)
        self.assertEqual(calls, [0, 8, 16])

    def test_does_not_skip_recent_event_or_repeat_completed_old(self):
        sources = [
            {"event_id": "recent", "date": "2026-10-09"},
            {"event_id": "old-incomplete", "date": "2026-10-01"},
            {"event_id": "old-complete", "date": "2026-09-30"},
        ]
        stored = {
            "old-incomplete": event_with_rows(8),
            "old-complete": event_with_rows(16),
        }
        stored["old-incomplete"]["top16_observed_rows"] = 8
        stored["old-complete"]["top16_observed_rows"] = 8
        selected = select_event_candidates(sources, stored, today=date(2026, 10, 9), limit=2)
        self.assertEqual([e["event_id"] for e in selected], ["recent", "old-incomplete"])
        self.assertEqual(select_event_candidates(sources, stored, today=date(2026, 10, 9), limit=1)[0]["event_id"], "recent")


class AnalysisTests(unittest.TestCase):
    def test_top16_subset_keeps_original_top8_metrics(self):
        event = event_with_rows(16)
        resolve = lambda code: {"parent": "ドラパルトex", "status": "classified",
                                "source": "test", "variant_tags": []}
        scope = _scope_analysis([event], {}, resolve, None)
        self.assertEqual(scope["coverage"]["captured_top8_rows"], 8)
        extra = scope["top16_context"]
        self.assertEqual(extra["coverage"]["captured_top16_rows"], 16)
        self.assertEqual(extra["coverage"]["captured_matched_top8_rows"], 8)
        metric = extra["top16_to_top8"][0]["observed_top16_to_top8"]
        self.assertEqual((metric["numerator"], metric["denominator"]), (8, 16))
        self.assertIsNotNone(metric["ci95_wilson"])
        self.assertTrue(metric["small_sample_warning"])

    def test_partial_top16_does_not_emit_a_misleading_share(self):
        event = event_with_rows(15)
        resolve = lambda _: {"parent": None, "status": "unknown", "source": "test"}
        context = _top16_context([event], {}, resolve, None)
        self.assertEqual(context["status"], "insufficient_coverage")
        self.assertEqual(context["coverage"]["excluded_events"], 1)
        self.assertEqual(context["top16_composition"], [])

    def test_top8_only_historical_week_is_not_silently_relabelled_top16(self):
        event = event_with_rows(8, checked=False)
        context = _top16_context([event], {}, lambda _: {}, None)
        self.assertEqual(context["status"], "insufficient_coverage")
        self.assertEqual(context["coverage"]["complete_top16_events"], 0)

class MergeTests(unittest.TestCase):
    def test_newer_top8_feed_preserves_prior_top16_rows_and_coverage(self):
        old_placements = [
            {"rank": r, "player_id": str(r), "deck_code": None}
            for r in range(1, 17)
        ]
        event = event_with_rows(16)
        event["placements"] = old_placements
        event["placement_count"] = 16
        event["collected_at"] = "2026-10-08T23:00:00+09:00"
        newer = {
            **event,
            "placements": old_placements[:8],
            "placement_count": 8,
            "collected_at": "2026-10-09T08:00:00+09:00",
        }
        for key in ("top16_observed_rows", "top16_checked_at", "top16_captured_rows"):
            newer.pop(key, None)
        city_base = {"season": "2027-S1", "season_start": "2026-09-26"}
        old_city = {**city_base, "updated_at": "2026-10-08T23:00:00+09:00", "events": [event]}
        new_city = {**city_base, "updated_at": "2026-10-09T08:00:00+09:00", "events": [newer]}
        deck = {"total_cards": 60, "cards": [{"count": 60}], "usages": []}
        old_decks = {"updated_at": old_city["updated_at"], "decks": {"a": deck}}
        new_decks = {"updated_at": new_city["updated_at"], "decks": {"a": deck}}
        merged, _ = merge_snapshot(old_city, old_decks, new_city, new_decks)
        result = merged["events"][0]
        self.assertEqual(result["placement_count"], 16)
        self.assertEqual(result["top16_observed_rows"], 8)
        self.assertEqual(result["top16_captured_rows"], 8)
        self.assertEqual(result["top16_checked_at"], event["top16_checked_at"])


if __name__ == "__main__":
    unittest.main()
