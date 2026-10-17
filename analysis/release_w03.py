"""Explicitly approved W03 captured-results FINAL; not a claim of national coverage."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from collector.validate_snapshot import validate_snapshot
from .answer_index import materialize_answer_index
from .state import ROOT, create_snapshot, verify_snapshot
from .w03_coverage import audit_week_coverage
from .week_calendar import CALENDAR_ID, week_event_ids, week_period
from .weekly import analyze_snapshot, audit_report, render_markdown

def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main():
    root = Path(ROOT)
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Release requires a clean checkout")
    git("fetch", "--quiet", "origin", "main")
    protected = ["data/city_db.json", "data/city_decks.json", "data/index.json",
                 "data/analysis/event_ledger.json", "data/analysis/deck_classifications.json",
                 "data/analysis/forecast_log.json"]
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "origin/main", "--", *protected],
                      cwd=root).returncode:
        raise ValueError("main input changed: refresh release branch first")
    head = git("rev-parse", "HEAD")
    files = {"city": "data/city_db.json", "decks": "data/city_decks.json",
             "index": "data/index.json", "ledger": "data/analysis/event_ledger.json"}
    raw = {k: (root / v).read_bytes() for k,v in files.items()}
    city, decks, index, ledger = [json.loads(raw[k]) for k in files]
    for key, value in validate_snapshot(city, decks).items():
        if index.get(key) != value:
            raise ValueError("Index mismatch: " + key)
    if city.get("updated_at") != index.get("source_updated_at"):
        raise ValueError("Source timestamps differ")
    for key in ("city", "decks"):
        if hashlib.sha256(raw[key]).hexdigest() != index[key+"_sha256"]:
            raise ValueError("Source SHA256 mismatch")
    cutoff = datetime.fromisoformat(index["source_updated_at"])
    if cutoff.tzinfo is None or cutoff > datetime.now(timezone.utc):
        raise ValueError("Bad source time")
    start, end = week_period("W03")
    coverage = audit_week_coverage(city, ledger, start, end)
    missing = set(map(str, coverage["scheduled_only_event_ids"]))
    if not missing.issubset({"1115351"}):
        raise ValueError("Unapproved missing events: "+str(sorted(missing)))
    if coverage["observed_result_events_category"] < 73 or coverage["observed_top8_rows_category"] < 580:
        raise ValueError("W03 Top8 results decreased below verified baseline")
    if coverage["top16_complete_result_events_category"] < 72:
        raise ValueError("Top16 complete comparison events decreased")
    if coverage["full_week_coverage_verified"]:
        raise ValueError("Unexpected complete-coverage assertion")
    if (root/"data/analysis/weekly_snapshots/W03/FINAL").exists():
        raise FileExistsError("W03 FINAL already exists; refuse overwrite")
    snapshot_id = "observed-"+head[:12]+"-w03"
    snap = create_snapshot(root, event_ids=week_event_ids(city,"W03"),
                           retrieved_at=cutoff.isoformat(), snapshot_id=snapshot_id,
                           week_id="W03", analysis_stage="FINAL",
                           period_start=start.isoformat()+"T00:00:00+09:00",
                           period_end=end.isoformat()+"T23:59:59+09:00",
                           cutoff_datetime=cutoff.isoformat())
    metadata = verify_snapshot(snap)
    if metadata["git_dirty"] or metadata["git_commit_sha"] != head:
        raise ValueError("Snapshot provenance invalid")
    previous_start, previous_end = week_period("W02")
    report = analyze_snapshot(snap, category="オープン", region="愛知県",
                              compare_start=previous_start.isoformat(),
                              compare_end=previous_end.isoformat())
    report["coverage_gate"] = coverage
    report["provenance"].update(calendar_id=CALENDAR_ID, full_week_coverage_verified=False,
                                  final_scope="captured_published_results_only",
                                  accepted_missing_event_ids=sorted(missing),
                                  missing_event_policy="user_approved_2026-10-17")
    report["trend"]["historical_basis"] = "recomputed_from_same_pinned_git_revision_captured_city_data"
    report["limitations"].append("FINAL freezes captured results, not all scheduled events. Schedule-only 1115351, if still missing, is excluded from the denominator; it is not a zero or cancellation.")
    report["audit"] = audit_report(report)
    scope = report["current_week"]["national"]["analysis"]
    total = scope["coverage"]["captured_top8_rows"]
    if report["audit"]["status"] == "FAIL":
        raise ValueError("Analysis audit failed")
    if total != coverage["observed_top8_rows_category"]:
        raise ValueError("Top8 count mismatch")
    if scope["coverage"]["known_top8_deck_lists"] != total:
        raise ValueError("Unknown Top8 deck lists")
    unknown = scope["classification_coverage_top8"]["unclassified_count"]
    if unknown > 2:
        raise ValueError("Unclassified rows increased")
    top16 = scope["top16_context"]["coverage"]["complete_top16_events"]
    if top16 != coverage["top16_complete_result_events_category"]:
        raise ValueError("Top16 subset mismatch")
    summary = {"calendar_id": CALENDAR_ID, "week_id": "W03", "stage": "FINAL",
               "release_scope": "captured_published_results_only",
               "full_week_coverage_verified": False, "source_commit": head,
               "snapshot_id": snapshot_id, "source_updated_at": cutoff.isoformat(),
               "accepted_schedule_only_event_ids": sorted(missing),
               "top16_excluded_event_ids_by_reason": coverage["top16_excluded_event_ids_by_reason"],
               "result_events_open": scope["coverage"]["result_events"],
               "top8_rows_open": total,
               "top8_60_lists_known": scope["coverage"]["known_top8_deck_lists"],
               "top16_complete_events_open": top16,
               "unclassified_top8_rows_open": unknown, "audit_status": report["audit"]["status"],
               "period_start": start.isoformat(), "period_end": end.isoformat()}
    out = root/"data/analysis/weekly_final_reports"/CALENDAR_ID/snapshot_id/"W03"
    if out.exists():
        raise FileExistsError(out)
    save(out/"summary.json", summary)
    save(out/"report.json", report)
    save(out/"coverage_audit.json", coverage)
    (out/"report.md").write_text(render_markdown(report, include_internal=False),encoding="utf-8")
    ix = materialize_answer_index(snap, category="オープン", region="愛知県",
                                   compare_start=previous_start.isoformat(),
                                   compare_end=previous_end.isoformat())
    if ix["row_count"] != total:
        raise ValueError("Answer index count mismatch")
    deck_rows = json.loads((root/ix["decks"]).read_text(encoding="utf-8"))
    if deck_rows["valid_60_count"] != total:
        raise ValueError("Top8 has invalid 60-card list")
    paths = [str(snap.relative_to(root)), str(out.relative_to(root)),
             "data/analysis/answer_index/W03/FINAL",
             "data/analysis/answer_index/current.json"]
    git("add", "--", *paths)
    changed = git("diff", "--cached", "--name-only").splitlines()
    allowed = ("data/analysis/weekly_snapshots/W03/FINAL/",
               "data/analysis/weekly_final_reports/",
               "data/analysis/answer_index/W03/FINAL/")
    if not changed or any(p != "data/analysis/answer_index/current.json" and
                          not p.startswith(allowed) for p in changed):
        raise ValueError("Release stages protected paths")
    git("commit", "-m", "Release W03 captured-results FINAL with documented missing event")
    print(json.dumps({"status":"FINAL_COMMITTED","commit":git("rev-parse","HEAD"),
                      "summary":summary,"answer_index":ix},ensure_ascii=False))

if __name__ == "__main__":
    main()
