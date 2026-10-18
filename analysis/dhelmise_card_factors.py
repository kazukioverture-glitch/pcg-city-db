"""Exploratory card deviations and Top4 outcomes relative to the consensus baseline.

Crucial limit: Top16-conditional advancement is not entrants' win rate or
causal proof. Multiple hypotheses were selected after inspecting these data.
Keep the baseline from ALL known lists, but evaluate rank only in complete
16-slot events. Never mutate original tournament results or FINAL records.
"""
import argparse
import json
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from analysis.dhelmise_performance import fisher_two_sided, wilson95, is_complete_event

PROFILES = {
    "ヤバソチャ少数・ノココッチex併用",
    "ヤバソチャ少数・通常ノココッチ",
}
SUFFIX = re.compile(r"[A-Z][A-Za-z0-9-]*[0-9]+/[A-Za-z0-9-]+$")
# These hypotheses are DATA-INFORMED (exploratory), not preregistered tests.
FEATURES = (
    ("キチキギスex採用", "キチキギスex", "present", 1),
    ("シェイミ採用", "シェイミ", "present", 1),
    ("グラビティーマウンテン採用", "グラビティーマウンテン", "present", 1),
    ("スペシャルレッドカード採用", "スペシャルレッドカード", "present", 1),
    ("ノココッチex採用", "ノココッチex", "present", 1),
    ("ラティアスex採用", "ラティアスex", "present", 1),
    ("ジャミングタワー採用", "ジャミングタワー", "present", 1),
    ("ジュペッタ4枚", "ジュペッタ", "at_least", 4),
    ("ボスの指令4枚", "ボスの指令", "at_least", 4),
    ("夜のタンカ3枚以上", "夜のタンカ", "at_least", 3),
    ("なかよしポフィン2枚以上", "なかよしポフィン", "at_least", 2),
    ("リーリエのピッピex2枚", "リーリエのピッピex", "at_least", 2),
)


def counts(cards):
    found = Counter()
    for card in cards:
        found[SUFFIX.sub("", card["name"])] += card["count"]
    if sum(found.values()) != 60:
        raise ValueError("A sampled deck was not 60 cards")
    return found


def top4(entry):
    return entry["rank"] <= 3


def bh_adjust(items):
    """Benjamini-Hochberg adjusted q values in input order."""
    indexed = sorted(enumerate(items), key=lambda x: x[1])
    result = [None] * len(indexed)
    q = 1.0
    for rank_index in range(len(indexed) - 1, -1, -1):
        index, p = indexed[rank_index]
        q = min(q, p * len(indexed) / (rank_index + 1))
        result[index] = round(q, 6)
    return result


def _summ(rows):
    n = len(rows)
    hits = sum(top4(r) for r in rows)
    return {
        "n": n,
        "top4": hits,
        "rate": round(hits / n, 6) if n else None,
        "wilson95": wilson95(hits, n),
    }


def _feature(entry, spec):
    _, name, mode, n = spec
    have = entry["_counts"].get(name, 0)
    if mode == "present":
        return have > 0
    if mode == "at_least":
        return have >= n
    raise ValueError("Unknown feature mode")


def _contingency(rows, predicate):
    yes = [r for r in rows if predicate(r)]
    no = [r for r in rows if not predicate(r)]
    a, b = sum(top4(r) for r in yes), sum(not top4(r) for r in yes)
    c, d = sum(top4(r) for r in no), sum(not top4(r) for r in no)
    return {
        "adopted": _summ(yes),
        "not_adopted": _summ(no),
        "rate_difference": round(a / len(yes) - c / len(no), 6)
        if yes and no else None,
        "fisher_two_sided": round(fisher_two_sided(a, b, c, d), 6)
        if yes and no else None,
    }


def stratified_cmh(rows, predicate, stratifier):
    """Exploratory Cochran-Mantel-Haenszel normal approximation.

    Controls only for declared discrete strata; it does not identify
    independent causal effects, and sparse strata can remain unstable.
    """
    strata = defaultdict(list)
    for r in rows:
        strata[stratifier(r)].append(r)
    residual, variance, odds_num, odds_den, informative = 0.0, 0.0, 0.0, 0.0, 0
    for group in strata.values():
        a = sum(predicate(r) and top4(r) for r in group)
        b = sum(predicate(r) and not top4(r) for r in group)
        c = sum(not predicate(r) and top4(r) for r in group)
        d = len(group) - a - b - c
        n, selected, successes = a + b + c + d, a + b, a + c
        if n > 1 and 0 < selected < n and 0 < successes < n:
            residual += a - selected * successes / n
            variance += (selected * (n - selected) * successes *
                         (n - successes) / (n * n * (n - 1)))
            informative += 1
        if n:
            odds_num += a * d / n
            odds_den += b * c / n
    z = residual / math.sqrt(variance) if variance else None
    return {
        "strata": len(strata),
        "informative_strata": informative,
        "common_odds_ratio": round(odds_num / odds_den, 6) if odds_den else None,
        "normal_approx_two_sided_p": round(math.erfc(abs(z) / math.sqrt(2)), 6)
        if z is not None else None,
        "method": "CMH without continuity correction; sparse-data exploratory",
    }


def _week_summary(rows, predicate, weeks):
    return {
        w: _contingency([r for r in rows if r["_week"] == w], predicate)
        for w in weeks
    }


def analyze(top16, dhelmise, baseline, manifest, weeks):
    source_sha = manifest["source_decks_sha256"]
    if (baseline["source_decks_sha256"] != source_sha or
            baseline["source_city_sha256"] != manifest["source_city_sha256"]):
        raise ValueError("Baseline/source provenance mismatch")
    complete = defaultdict(list)
    for row in top16:
        complete[row["event_id"]].append(row)
    eligible_events = {
        event_id for event_id, records in complete.items()
        if is_complete_event(records)
    }
    universe = [dict(r) for r in dhelmise if r["average_profile"] in PROFILES]
    for row in universe:
        row["_counts"] = counts(row["cards"])
        row["_week"] = row.get("_week_source")
        if row["_week"] is None:
            raise ValueError("Unexpected date outside known weeks")
    if set(weeks) != {r["_week"] for r in universe}:
        raise ValueError("Cohort weeks mismatch")
    eligible = [r for r in universe if r["event_id"] in eligible_events]
    refs = baseline["reference"]["cards"]
    if baseline["population"] != len(universe):
        raise ValueError("Baseline and sampled population do not match")
    shortfalls = []
    deviations = []
    for r in eligible:
        shortfalls.append(sum(
            max(0, c["mode_copies"] - r["_counts"].get(c["card"], 0))
            for c in refs
        ))
    for c in refs:
        name, modal = c["card"], c["mode_copies"]
        subsets = {
            "below_modal": [r for r in eligible if r["_counts"].get(name, 0) < modal],
            "at_modal": [r for r in eligible if r["_counts"].get(name, 0) == modal],
            "above_modal": [r for r in eligible if r["_counts"].get(name, 0) > modal],
        }
        deviations.append({
            "card": name,
            "modal_copies": modal,
            "adoption_rate_all_decks": c["adoption_rate"],
            "modal_agreement_all_decks": c["modal_agreement"],
            "results": {key: _summ(value) for key, value in subsets.items()},
        })
    tests = []
    for spec in FEATURES:
        predicate = lambda r, s=spec: _feature(r, s)
        overall = _contingency(eligible, predicate)
        tests.append({
            "hypothesis": spec[0],
            "card": spec[1],
            "feature": {"mode": spec[2], "count": spec[3]},
            "overall": overall,
            "by_week": _week_summary(eligible, predicate, weeks),
            "adjusted_for_week": stratified_cmh(eligible, predicate, lambda r: r["_week"]),
            "adjusted_for_week_and_dudunsparce_ex": stratified_cmh(
                eligible, predicate, lambda r: (
                    r["_week"], r["_counts"].get("ノココッチex", 0) > 0)),
        })
    ps = [item["overall"]["fisher_two_sided"] for item in tests]
    qvalues = bh_adjust([p if p is not None else 1 for p in ps])
    for item, q in zip(tests, qvalues):
        item["bh_adjusted_q"] = q if item["overall"]["fisher_two_sided"] is not None else None

    joint = []
    for kichi in (False, True):
        for shaymin in (False, True):
            g = [
                r for r in eligible
                if (r["_counts"].get("キチキギスex", 0) > 0) == kichi
                and (r["_counts"].get("シェイミ", 0) > 0) == shaymin
            ]
            joint.append({
                "kichi_adopted": kichi,
                "shaymin_adopted": shaymin,
                "results": _summ(g),
            })
    shortfall_bins = defaultdict(list)
    for r, count in zip(eligible, shortfalls):
        shortfall_bins[str(count)].append(r)
    shortfall_rows = {
        str(k): _summ(shortfall_bins[str(k)])
        for k in sorted({int(x) for x in shortfall_bins})
    }
    return {
        "schema_version": "dhelmise-card-factors-exploratory-v1",
        "source_city_sha256": manifest["source_city_sha256"],
        "source_decks_sha256": source_sha,
        "source_updated_at": manifest["source_updated_at"],
        "population": {
            "reference_decks": len(universe),
            "rank_complete_events": len(eligible_events),
            "eligible_decks": len(eligible),
            "top4": sum(top4(r) for r in eligible),
            "unique_deck_codes": len({r["deck_code"] for r in eligible}),
        },
        "reference_cards": baseline["reference"]["card_count"],
        "reference_slots": baseline["reference"]["copy_count"],
        "strict_core_slots": baseline["strict_modal_core"]["copy_count"],
        "reference_card_deviations": deviations,
        "reference_shortfall_results": shortfall_rows,
        "exploratory_feature_tests": tests,
        "joint_hypothesis_kichi_shaymin": joint,
        "warnings": [
            "The decklists selected for these hypotheses informed which hypotheses were tested: statistical significance is exploratory, not confirmatory.",
            "The primary outcome is Top4 conditional on an already recorded Top16 placement, not the win probability of event entrants.",
            "Multiple card effects are coupled by the fixed 60-card composition; individual card presence cannot be interpreted as independent treatment.",
            "Players, matchups, skill, other card choices and event clustering were not fully adjusted.",
            "W04 may include partially collected events; only events with complete sixteen rank placements contribute to outcome comparisons.",
            "All 4 weeks are considered. Validation must use newly observed weeks or randomized controlled deck-swaps.",
        ],
    }


def build(root):
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    baseline = json.loads((root / "dhelmise_consensus.json").read_text(encoding="utf-8"))
    t, d = [], []
    for w in manifest["weeks"]:
        for key, arr in (("top16_index_path", t), ("dhelmise_lists_path", d)):
            path = root / w[key].replace("data/deck_library/", "")
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc["source_decks_sha256"] != manifest["source_decks_sha256"] or doc["source_city_sha256"] != manifest["source_city_sha256"]:
                raise ValueError("Source provenance mismatch: " + str(path))
            for row in doc["rows"]:
                if key == "dhelmise_lists_path":
                    row = dict(row, _week_source=w["week_id"])
                arr.append(row)
    # Week identity is stored on each deck: do not derive week from a date.
    result = analyze(t, d, baseline, manifest, [w["week_id"] for w in manifest["weeks"]])
    destination = root / "dhelmise_card_factors.json"
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if not destination.exists() or destination.read_text(encoding="utf-8") != body:
        fd, tmp = tempfile.mkstemp(prefix=".dhelmise-factors-", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(body)
            os.replace(tmp, destination)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="data/deck_library")
    args = parser.parse_args()
    out = build(args.library)
    print(json.dumps({
        "n": out["population"]["eligible_decks"],
        "reference_slots": out["reference_slots"],
        "feature_hypotheses": len(out["exploratory_feature_tests"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
