import copy
import unittest

from analysis.weekly import (UNCLASSIFIED, audit_report, build_weekly_report,
                             discover_card_identities, render_markdown, wilson95)


def card(card_id, name, count=1):
    return {"card_id": card_id, "name": name, "count": count, "section": "ポケモン (60)"}


def deck(code, cards):
    total = sum(c["count"] for c in cards)
    return {"deck_code": code, "total_cards": 60,
            "cards": cards + [{"card_id": "E", "name": "基本エネルギー",
                               "count": 60-total, "section": "エネルギー"}]}


def event(eid, day, prefecture, codes, category="オープン"):
    ranks = [1, 2, 3, 3, 5, 5, 5, 5][:len(codes)]
    return {"event_id": eid, "date": day, "category": category,
            "venue_prefecture": prefecture,
            "placements": [{"rank": rank, "deck_code": code}
                           for rank, code in zip(ranks, codes)]}


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {
            "schema_version": "1.2.2", "kind": "weekly_snapshot",
            "snapshot_id": "snap", "week_id": "W02", "analysis_stage": "FINAL",
            "period_start": "2026-09-30T00:00:00+09:00",
            "period_end": "2026-10-06T23:59:59+09:00",
            "cutoff_datetime": "2026-10-07T01:00:00+09:00",
            "retrieved_at": "2026-10-07T01:00:00+09:00",
            "created_at": "2026-10-06T16:00:00+00:00",
            "git_commit_sha": "0"*40, "git_dirty": False,
            "event_ids": ["C1", "C2", "J1"], "references": [],
        }
        codes = [f"A{i}" for i in range(8)] + [f"B{i}" for i in range(8)]
        self.decks = {"decks": {}}
        for i, code in enumerate(codes):
            self.decks["decks"][code] = deck(code, [card("watch", "監視カード", 2 if i % 2 == 0 else 1)])
        prev = [f"P{i}" for i in range(8)]
        for code in prev:
            self.decks["decks"][code] = deck(code, [card("other", "別カード")])
        self.city = {"events": [
            event("C1", "2026-10-01", "愛知県", codes[:8]),
            event("C2", "2026-10-02", "東京都", codes[8:]),
            event("J1", "2026-10-03", "愛知県", codes[:8], category="ジュニア"),
            event("P1", "2026-09-25", "愛知県", prev),
        ]}
        records = []
        for code in codes[:6] + prev[:4]:
            records.append({"deck_code": code, "parent_archetype": "A",
                            "variant_tags": ["v型"] if code.endswith("0") else [],
                            "classification_status": "classified"})
        for code in codes[8:12] + prev[4:]:
            records.append({"deck_code": code, "parent_archetype": "B",
                            "variant_tags": [], "classification_status": "classified"})
        self.classifications = {"kind": "deck_classifications", "records": records}
        self.watch = {"version": "watch-cards-v1",
                      "cards": [{"label": "監視カード",
                                 "identities": [{"card_id": "watch", "name": "監視カード"}]}]}

    def report(self):
        return build_weekly_report(
            self.metadata, self.city, self.decks, self.classifications,
            category="オープン", region="愛知県",
            compare_start="2026-09-23", compare_end="2026-09-29",
            watch_config=self.watch, generated_at="2026-10-07T00:00:00+00:00")

    def test_current_region_and_trend_are_separate(self):
        before = copy.deepcopy((self.city, self.decks, self.classifications))
        report = self.report()
        self.assertEqual(report["current_week"]["national"]["analysis"]["coverage"]["result_events"], 2)
        self.assertEqual(report["current_week"]["regional"]["analysis"]["coverage"]["result_events"], 1)
        self.assertEqual(report["trend"]["national"]["previous_coverage"]["result_events"], 1)
        self.assertEqual(report["current_week"]["regional"]["selection_basis"], "venue_prefecture")
        self.assertEqual((self.city, self.decks, self.classifications), before)

    def test_composition_conversion_ci_and_small_sample(self):
        report = self.report()
        national = report["current_week"]["national"]["analysis"]
        comp = {x["parent_archetype"]: x for x in national["stage_composition"]["top8"]}
        self.assertEqual(comp["A"]["count"], 6)
        self.assertEqual(comp["B"]["count"], 4)
        self.assertEqual(comp[UNCLASSIFIED]["count"], 6)
        row = next(x for x in national["conversion"] if x["parent_archetype"] == "A")
        metric = row["conversion"]["top8_to_top4"]
        self.assertEqual(metric["denominator"], 6)
        self.assertIsNotNone(metric["ci95_wilson"])
        self.assertTrue(metric["small_sample_warning"])
        self.assertEqual(len(wilson95(1, 2)), 2)
        coverage = national["classification_coverage_top8"]
        self.assertEqual(coverage["valid_60_deck_lists"], 16)
        self.assertEqual(coverage["parent_classified_among_valid_60_lists"]["numerator"], 10)
        self.assertEqual(report["provenance"]["classifier_market"], "JP")

    def test_watch_card_exact_identity(self):
        report = self.report()
        watch = report["current_week"]["national"]["analysis"]["card_analysis_top8"]["watch_cards"]
        self.assertEqual(watch["known_deck_lists"], 16)
        self.assertEqual(watch["cards"][0]["adopting_lists"], 16)
        self.decks["decks"]["A0"]["cards"][0]["card_id"] = "different"
        report = self.report()
        watch = report["current_week"]["national"]["analysis"]["card_analysis_top8"]["watch_cards"]
        self.assertEqual(watch["cards"][0]["adopting_lists"], 15)

    def test_discovery_does_not_merge_identities(self):
        self.decks["decks"]["A0"]["cards"][0]["card_id"] = "watch2"
        found = discover_card_identities(self.decks, ["監視"])
        ids = {x["card_id"] for x in found["queries"]["監視"]}
        self.assertEqual(ids, {"watch", "watch2"})

    def test_audit_and_no_usage_rate_term(self):
        report = self.report()
        self.assertNotEqual(report["audit"]["status"], "FAIL")
        broken = copy.deepcopy(report)
        broken["usage_rate"] = 0.5
        self.assertEqual(audit_report(broken)["status"], "FAIL")

    def test_comparison_rejects_overlapping_and_reversed_periods(self):
        for start, end in (("2026-09-29", "2026-09-30"),
                           ("2026-10-01", "2026-10-02"),
                           ("2026-09-29", "2026-09-23")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                build_weekly_report(self.metadata, self.city, self.decks, self.classifications,
                                    compare_start=start, compare_end=end)
        broken = self.report()
        broken["trend"]["comparison_period"]["end"] = "2026-09-30"
        self.assertEqual(audit_report(broken)["status"], "FAIL")

    def test_text_preserves_provenance_metrics_and_small_sample_warning(self):
        text = render_markdown(self.report())
        for value in ("post-snapshot", "snapshot_sidecar_when_compatible_else_post_snapshot_derivation",
                      "recomputed_from_same_FINAL_snapshot_captured_city_data",
                      "top8_to_top4", "top4_to_top2", "top2_to_champion",
                      "Wilson 95%CI", "少数標本", "愛知県 各段階", "採用リスト数",
                      "2026-09-23", "2026-09-29"):
            self.assertIn(value, text)


if __name__ == "__main__":
    unittest.main()
