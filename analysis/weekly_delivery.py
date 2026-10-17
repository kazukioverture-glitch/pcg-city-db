"""Deadline-oriented weekly derivatives. Offline; never creates FINAL snapshots."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess
import tempfile

from collector.validate_snapshot import validate_snapshot
from .state import ROOT
from .week_calendar import CALENDAR_ID, week_for_event, week_period, week_event_ids
from .weekly import build_weekly_report, render_markdown, audit_report
from .w03_coverage import audit_week_coverage

JST = timezone(timedelta(hours=9))


def latest_closed_week(now):
    if now.tzinfo is None:
        raise ValueError("Timezone required")
    day = now.astimezone(JST).date()
    return week_for_event(day - timedelta(days=day.weekday() + 1))


def missingness(city, ledger, week_id, scope, category="オープン"):
    """Conditional 8-slot sensitivity, NOT a bound on unobserved national events."""
    start, end = week_period(week_id)
    in_week = lambda e, key: start.isoformat() <= (e.get(key) or "") <= end.isoformat()
    events = [e for e in city["events"] if in_week(e, "date")]
    observed = {str(e["event_id"]) for e in events}
    uncertain = [str(e["event_id"]) for e in ledger["records"]
                 if in_week(e, "event_date") and str(e["event_id"]) not in observed
                 and e.get("category") in (None, category)]
    # Unknown-category observed events could also belong to this category.
    uncertain += [str(e["event_id"]) for e in events if e.get("category") is None]
    short = {}
    for event in events:
        if event.get("category") != category:
            continue
        n = sum(type(p.get("rank")) is int and 1 <= p["rank"] <= 8
                for p in event.get("placements", []))
        if n < 8:
            short[str(event["event_id"])] = 8 - n
    missing = len(uncertain) * 8 + sum(short.values())
    n = scope["coverage"]["captured_top8_rows"]
    unknown = scope["classification_coverage_top8"]["unclassified_count"]
    total = n + missing
    sensitivity = []
    for row in scope["stage_composition"]["top8"]:
        if row["parent_archetype"] == "未分類":
            continue
        count = row["count"]
        sensitivity.append({"parent_archetype": row["parent_archetype"],
                            "observed_count": count,
                            "conditional_share_min": count / total if total else None,
                            "conditional_share_max": (count + missing + unknown) / total if total else None})
    return {"national_missingness_upper_bound": None,
            "schedule_completeness_verified": False,
            "interpretation": "Recorded events only; assume at most 8 Top8 slots per event. "
                              "Not a confidence interval, missing-at-random assumption or national bound. "
                              "Short results may be legitimate. Unlisted events remain unbounded.",
            "uncertain_category_or_missing_event_ids": sorted(uncertain),
            "short_top8_potential_slots_by_event": short,
            "conditional_potential_missing_top8_slots": missing,
            "observed_top8_rows": n, "unclassified_observed_top8_rows": unknown,
            "conditional_uncertain_share_width_pp": 100 * (missing + unknown) / total if total else None,
            "archetype_sensitivity": sensitivity}


def render_public_markdown(report):
    """Reader-facing results; operational findings remain in JSON diagnostics."""
    return render_markdown(report, include_internal=False)


def generate(repo, output_root, week_id, now=None):
    repo = Path(repo).resolve()
    output_root = Path(output_root).resolve()
    # Output may never be directed at production or historical data.
    allowed = (repo / "data/analysis/weekly_deliveries").resolve()
    if output_root.is_relative_to(repo / "data") and output_root != allowed:
        raise ValueError("Use weekly_deliveries or a directory outside production data")
    now = now or datetime.now(JST)
    start, end = week_period(week_id)
    if now.tzinfo is None or now.astimezone(JST).date() <= end:
        raise ValueError("Only closed JST weeks can be delivered")
    names = {"city": "data/city_db.json", "decks": "data/city_decks.json",
             "index": "data/index.json", "ledger": "data/analysis/event_ledger.json",
             "classes": "data/analysis/deck_classifications.json"}
    raw = {k: (repo / p).read_bytes() for k, p in names.items()}
    docs = {k: json.loads(v) for k, v in raw.items()}
    city, decks, index = (docs[k] for k in ("city", "decks", "index"))
    counts = validate_snapshot(city, decks)
    if city.get("updated_at") != index.get("source_updated_at"):
        raise ValueError("Source timestamps disagree")
    for key, count in counts.items():
        if index.get(key) != count:
            raise ValueError("Index count mismatch: " + key)
    for name, key in (("city", "city_sha256"), ("decks", "decks_sha256")):
        if index.get(key) != hashlib.sha256(raw[name]).hexdigest():
            raise ValueError("Index SHA256 mismatch: " + key)
    source_time = datetime.fromisoformat(index["source_updated_at"])
    if source_time.tzinfo is None or source_time > now:
        raise ValueError("Invalid/future source timestamp")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo, text=True))
    if dirty:
        raise ValueError("Commit tracked changes before pinning weekly inputs")
    ids = week_event_ids(city, week_id)
    if not ids:
        raise ValueError("No captured weekly results; publish failure diagnostics, not fabricated results")
    hashes = {names[k]: hashlib.sha256(body).hexdigest() for k, body in raw.items()}
    metadata = {"schema_version": "1.2.2", "kind": "weekly_snapshot", "week_id": week_id, "analysis_stage": "INTERIM",
                "snapshot_id": f"delivery-{head[:12]}-{week_id}",
                "period_start": f"{start}T00:00:00+09:00",
                "period_end": f"{end}T23:59:59+09:00",
                "cutoff_datetime": source_time.isoformat(), "retrieved_at": source_time.isoformat(),
                "git_commit_sha": head, "git_dirty": False, "event_ids": ids, "references": []}
    previous = week_period(f"W{int(week_id[1:])-1:02d}") if week_id != "W01" else None
    report = build_weekly_report(metadata, city, decks, docs["classes"], category="オープン", region="愛知県",
                                 compare_start=previous[0].isoformat() if previous else None,
                                 compare_end=previous[1].isoformat() if previous else None,
                                 generated_at=now.isoformat())
    report["coverage_gate"] = audit_week_coverage(city, docs["ledger"], start, end)
    report["provenance"].update(calendar_id=CALENDAR_ID, source_files_sha256=hashes,
                                 full_week_coverage_verified=False)
    if previous:
        report["trend"]["historical_basis"] = "recomputed_from_same_pinned_git_revision_captured_city_data"
    report["audit"] = audit_report(report)
    if report["audit"]["status"] == "FAIL":
        raise ValueError("Weekly audit FAIL: " + json.dumps(report["audit"], ensure_ascii=False))
    scope = report["current_week"]["national"]["analysis"]
    if not scope["coverage"]["captured_top8_rows"]:
        raise ValueError("No open Top8 results")
    impact = missingness(city, docs["ledger"], week_id, scope)
    summary = {"calendar_id": CALENDAR_ID, "week_id": week_id,
               "period_start": str(start), "period_end": str(end),
               "delivery_status": "PUBLISHED_WITH_LIMITATIONS", "stage": "INTERIM",
               "audit_status": report["audit"]["status"], "input_commit": head,
               "input_sha256": hashes, "source_updated_at": source_time.isoformat(),
               "source_age_hours": (now-source_time).total_seconds()/3600,
               "source_stale_24h": (now-source_time).total_seconds() > 86400,
               "source_updated_after_week_end": source_time.astimezone(JST).date() > end,
               "full_week_coverage_verified": False, "mdm_external_axes": "not_assessed",
               "coverage": scope["coverage"], "missingness": impact}
    content = render_public_markdown(report)
    analyzer_revision = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", "analysis", "collector/validate_snapshot.py",
         "collector/merge_termux_snapshot.py"], cwd=repo, text=True).strip()
    generation = hashlib.sha256(json.dumps([hashes, analyzer_revision], sort_keys=True).encode()).hexdigest()[:12]
    summary["analyzer_revision"] = analyzer_revision
    summary["generation_id"] = generation
    base = output_root / CALENDAR_ID / week_id / generation
    if base.exists():
        existing = json.loads((base / "summary.json").read_text())
        if existing["input_sha256"] != hashes or not all((base / n).is_file() for n in ("report.json", "report.md")):
            raise ValueError("Incomplete or conflicting existing delivery")
        return base
    base.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".delivery-", dir=base.parent))
    try:
        for name, doc in (("report.json", report), ("summary.json", summary)):
            (temp / name).write_text(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        (temp / "report.md").write_text(content, encoding="utf-8")
        os.rename(temp, base)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="Default: latest closed JST week")
    parser.add_argument("--output-root", type=Path, default=ROOT / "data/analysis/weekly_deliveries")
    args = parser.parse_args()
    now = datetime.now(JST)
    week = args.week or latest_closed_week(now)
    print(generate(ROOT, args.output_root, week, now))


if __name__ == "__main__":
    main()
