"""Independent-window validation of the frozen Dhelmise baseline hypothesis.

Never refit or overwrite the W01-W04 training reference. W05-W08 is reserved
for out-of-training observations. This is observational, not causal evidence.
"""
import argparse
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from analysis.dhelmise_performance import fisher_two_sided, is_complete_event, wilson95
from analysis.dhelmise_card_factors import counts, stratified_cmh

def summary(items):
    n = len(items)
    passed = sum(r["rank"] <= 3 for r in items)
    return {
        "placements": n,
        "top4": passed,
        "top4_rate": round(passed / n, 6) if n else None,
        "wilson95": wilson95(passed, n),
        "unique_codes": len({r["deck_code"] for r in items}),
    }


def assess(training_protocol, manifest, ranks, dhelmise_rows):
    future = training_protocol["validation"]
    selected_weeks = future["weeks"]
    available_weeks = [r["week_id"] for r in manifest["weeks"]
                       if r["week_id"] in selected_weeks]
    events = defaultdict(list)
    for row in ranks:
        events[row["event_id"]].append(row)
    complete_events = {
        event for event, rowset in events.items()
        if is_complete_event(rowset)
    }
    decks = [
        dict(r, _counts=counts(r["cards"]))
        for r in dhelmise_rows
        if r["_week"] in selected_weeks
        and r["average_profile"] in future["population_profiles"]
        and r["event_id"] in complete_events
    ]
    treatment = [
        r for r in decks
        if r["_counts"].get("キチキギスex", 0) > 0
        and r["_counts"].get("シェイミ", 0) == 0
    ]
    control = [
        r for r in decks
        if r["_counts"].get("シェイミ", 0) > 0
        and r["_counts"].get("キチキギスex", 0) == 0
    ]
    treat, ctrl = summary(treatment), summary(control)
    p = None
    if treatment and control:
        a, c = treat["top4"], ctrl["top4"]
        p = round(fisher_two_sided(
            a, len(treatment) - a, c, len(control) - c), 6)
    compare = treatment + control
    cmh = stratified_cmh(
        compare,
        lambda r: r["_counts"].get("キチキギスex", 0) > 0,
        lambda r: (r["_week"], r["_counts"].get("ノココッチex", 0) > 0)
    )
    conditions = future["primary_comparison"]
    sufficient = (len(treatment) >= conditions["minimum_each_group"]
                  and len(control) >= conditions["minimum_each_group"])
    if not available_weeks:
        status = "not_started"
    elif set(available_weeks) != set(selected_weeks):
        status = "window_in_progress_descriptive_only"
    elif not sufficient:
        status = "insufficient_samples"
    else:
        status = "window_observed_subject_to_source_completeness_audit"
    observed_direction = (treat["top4_rate"] is not None and
                          ctrl["top4_rate"] is not None and
                          treat["top4_rate"] > ctrl["top4_rate"])
    threshold_met = (status == "window_observed_subject_to_source_completeness_audit"
                     and observed_direction and p is not None and p < conditions["alpha"])
    return {
        "schema_version": "dhelmise-holdout-v1",
        "training_source_decks_sha256": training_protocol["training"]["source_decks_sha256"],
        "training_base_copies_frozen": training_protocol["training"]["reference_copies"],
        "training_base_cards_frozen": training_protocol["training"]["modal_reference"],
        "new_source_decks_sha256": manifest["source_decks_sha256"],
        "new_source_updated_at": manifest["source_updated_at"],
        "validation_weeks": selected_weeks,
        "available_weeks": available_weeks,
        "status": status,
        "complete_event_count": len(complete_events),
        "eligible_common_engine_decks": summary(decks),
        "exclusive_comparison": {
            "kichi_only": treat,
            "shaymin_only": ctrl,
            "fisher_two_sided_p": p,
            "week_and_dudunsparce_ex_stratified": cmh,
            "sufficient_group_size": sufficient,
            "positive_direction_observed": observed_direction if sufficient else None,
            "exploratory_replication_criterion_met": bool(threshold_met),
        },
        "notes": [
            "Not an estimate of win rate among all tournament entrants.",
            "Out-of-training validation reduces overfitting but does not remove player, matchup or card-composition confounding.",
            "Future weekly published-event coverage may remain incomplete; do not declare confirmation without separate completeness audit.",
            "The training baseline and primary comparison come from an immutable protocol, not recalculated after future data.",
            "The controlled 59-card A/B swap requires independent head-to-head battle records.",
        ],
    }


def build(root, protocol_path):
    root = Path(root)
    protocol = json.loads(Path(protocol_path).read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    ranks, decks = [], []
    for week in manifest["weeks"]:
        if week["week_id"] not in protocol["validation"]["weeks"]:
            continue
        for key, target in (("top16_index_path", ranks),
                            ("dhelmise_lists_path", decks)):
            file_path = root / week[key].replace("data/deck_library/", "")
            doc = json.loads(file_path.read_text(encoding="utf-8"))
            if doc["source_city_sha256"] != manifest["source_city_sha256"] or doc["source_decks_sha256"] != manifest["source_decks_sha256"]:
                raise ValueError("Stale future observation " + str(file_path))
            for row in doc["rows"]:
                target.append(dict(row, _week=week["week_id"]))
    report = assess(protocol, manifest, ranks, decks)
    output = root / "dhelmise_holdout_v1.json"
    content = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if not output.exists() or output.read_text(encoding="utf-8") != content:
        fd, tmp = tempfile.mkstemp(prefix=".dhelmise-holdout-", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(tmp, output)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="data/deck_library")
    parser.add_argument("--protocol", default="data/research/dhelmise_hypothesis_v1.json")
    arguments = parser.parse_args()
    report = build(arguments.library, arguments.protocol)
    print(json.dumps({
        "status": report["status"],
        "available_weeks": report["available_weeks"],
        "decks": report["eligible_common_engine_decks"]["placements"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
