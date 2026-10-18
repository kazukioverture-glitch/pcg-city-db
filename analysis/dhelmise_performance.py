"""Reproducible Top16-conditional tournament results by Dhelmise averaging cohort.

Reference population: only tournaments with the exact published 1/2/3/5/9
pattern (16 placements). Partial events do not make Top8 seem stronger.
This cannot estimate win rate among all tournament entrants.
"""
import argparse
import json
import math
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

GROUPS = (
    "ミカルゲ採用・ヤバソチャ厚め",
    "ミカルゲ非採用・ヤバソチャ厚め",
    "ヤバソチャ少数・ノココッチex併用",
    "ヤバソチャ少数・通常ノココッチ",
    "ヤバソチャ少数・ノココッチなし",
    "フーディン併用",
    "ヤバソチャ・フーディン非採用",
    "ミカルゲ採用・例外構築",
)
EXPECTED = {1: 1, 2: 1, 3: 2, 5: 4, 9: 8}
METRICS = {
    "top8": lambda r: r["rank"] <= 5,
    "top4": lambda r: r["rank"] <= 3,
    "final": lambda r: r["rank"] <= 2,
    "champion": lambda r: r["rank"] == 1,
}
Z95 = 1.959963984540054


def is_complete_event(event_rows):
    return len(event_rows) == 16 and Counter(r["rank"] for r in event_rows) == EXPECTED


def wilson95(successes, observations):
    if observations <= 0:
        return None
    p = successes / observations
    z2 = Z95 ** 2
    denom = 1 + z2 / observations
    mid = (p + z2 / (2 * observations)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / observations + z2 / (4 * observations ** 2)) / denom
    return [round(max(0, mid - half), 6), round(min(1, mid + half), 6)]


def fisher_two_sided(a, b, c, d):
    """Fisher exact two-sided, with probabilities <= observed table probability."""
    if any(v < 0 for v in (a, b, c, d)):
        raise ValueError("Negative count")
    n, left, success = a + b + c + d, a + b, a + c
    if n == 0 or left == 0 or left == n:
        return 1.0
    lower, upper = max(0, left - (n - success)), min(left, success)
    denominator = math.comb(n, left)
    pmf = lambda x: (math.comb(success, x) * math.comb(n - success, left - x)) / denominator
    observed_p = pmf(a)
    return min(1.0, sum(pmf(x) for x in range(lower, upper + 1) if pmf(x) <= observed_p * (1 + 1e-10)))


def row_stats(rows):
    n = len(rows)
    return {
        "placements": n,
        "unique_deck_codes": len({r["deck_code"] for r in rows}),
        "ranks": {str(r): sum(int(x["rank"] == r) for x in rows) for r in EXPECTED},
        "metrics": {
            name: {
                "count": sum(bool(pred(r)) for r in rows),
                "rate": round(sum(bool(pred(r)) for r in rows) / n, 6) if n else None,
                "wilson_95": wilson95(sum(bool(pred(r)) for r in rows), n),
            }
            for name, pred in METRICS.items()
        },
    }


def build_report(top16_rows, dhelmise_rows, manifest):
    events = defaultdict(list)
    for item in top16_rows:
        events[item["event_id"]].append(item)
    complete_events = {key for key, rows in events.items() if is_complete_event(rows)}
    if not complete_events:
        raise ValueError("No events with 16 complete placements")
    eligible = [r for r in dhelmise_rows if r["event_id"] in complete_events]
    for r in dhelmise_rows:
        if r["average_profile"] not in GROUPS:
            raise ValueError("Unknown averaging profile: " + str(r["average_profile"]))
        if r["total_cards"] != 60 or sum(c["count"] for c in r["cards"]) != 60:
            raise ValueError("Deck is not 60 cards: " + r["deck_code"])
    report_groups = {}
    for group in GROUPS:
        subset = [r for r in eligible if r["average_profile"] == group]
        others = [r for r in eligible if r["average_profile"] != group]
        s = row_stats(subset)
        n, m = len(subset), len(others)
        for metric in METRICS:
            a = s["metrics"][metric]["count"]
            c = sum(bool(METRICS[metric](r)) for r in others)
            raw_p = fisher_two_sided(a, n - a, c, m - c) if n else None
            s["metrics"][metric]["fisher_vs_other_groups"] = round(raw_p, 6) if raw_p is not None else None
            s["metrics"][metric]["bonferroni_p_7_groups"] = round(min(1.0, raw_p * 7), 6) if raw_p is not None else None
        sorted_rows = sorted(subset, key=lambda r: (r["rank"], r["date"], r["deck_code"]))
        s["representative_top_decks"] = [
            {
                "rank": r["rank"],
                "date": r["date"],
                "category": r["category"],
                "prefecture": r["venue_prefecture"],
                "event_id": r["event_id"],
                "deck_code": r["deck_code"],
                "deck_url": r["deck_url"],
            }
            for r in sorted_rows[:3]
        ]
        report_groups[group] = s
    normal = report_groups["ヤバソチャ少数・通常ノココッチ"]
    ex = report_groups["ヤバソチャ少数・ノココッチex併用"]
    p_pair = {}
    for metric in METRICS:
        a = normal["metrics"][metric]["count"]
        b = normal["placements"] - a
        c = ex["metrics"][metric]["count"]
        d = ex["placements"] - c
        p_pair[metric] = round(fisher_two_sided(a, b, c, d), 6)
    report = {
        "schema_version": "dhelmise-performance-v1",
        "source_city_sha256": manifest["source_city_sha256"],
        "source_decks_sha256": manifest["source_decks_sha256"],
        "source_updated_at": manifest["source_updated_at"],
        "calendar_id": manifest["calendar_id"],
        "scope": "Recorded Dhelmise Top16 decklists (rank 1 to 16), not all event entrants",
        "event_filter": "Only events with exactly 16 recorded rank slots: 1x1,2x1,3x2,5x4,9x8",
        "observed_events": len(events),
        "complete_16_slot_events": len(complete_events),
        "excluded_incomplete_events": len(events) - len(complete_events),
        "all_recorded_dhelmise": row_stats(dhelmise_rows),
        "eligible_complete_event_dhelmise": row_stats(eligible),
        "groups": report_groups,
        "predefined_pairwise_fisher_p": {
            "comparison": "ヤバソチャ少数・通常ノココッチ vs ヤバソチャ少数・ノココッチex併用",
            "two_sided_by_metric": p_pair,
        },
        "warnings": [
            "Only conditional advancement among observed Top16 decks; not an event entrant win rate.",
            "Incomplete event reports are removed to avoid stage-dependent selection bias.",
            "Some deck codes are repeated across tournaments: sampling unit is placement, not distinct decklists.",
            "Deck observations from the same event may be statistically dependent.",
            "Within-group card-level observations are exploratory; the report does not certify card-level causation.",
            "Weekly data may be incomplete or change; use source hashes when comparing snapshots.",
        ],
    }
    if sum(report_groups[g]["placements"] for g in GROUPS) != len(eligible):
        raise ValueError("Not all eligible decks assigned to exactly one group")
    return report


def build(root):
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    top, decks = [], []
    for row in manifest["weeks"]:
        for key, dest in (("top16_index_path", top), ("dhelmise_lists_path", decks)):
            doc = json.loads((root / row[key].replace("data/deck_library/", "")).read_text(encoding="utf-8"))
            if doc["source_city_sha256"] != manifest["source_city_sha256"] or doc["source_decks_sha256"] != manifest["source_decks_sha256"]:
                raise ValueError("Source SHA mismatch for " + row[key])
            dest.extend(doc["rows"])
    report = build_report(top, decks, manifest)
    path = root / "dhelmise_performance.json"
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        fd, temp = tempfile.mkstemp(prefix=".dhelmise-performance-", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="data/deck_library")
    args = parser.parse_args()
    report = build(args.library)
    print(json.dumps({
        "complete_events": report["complete_16_slot_events"],
        "decks": report["eligible_complete_event_dhelmise"]["placements"],
        "pairwise": report["predefined_pairwise_fisher_p"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
