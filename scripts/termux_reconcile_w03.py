"""One-time, bounded official-result reconciliation through 2027 S1 W03.

Runs on Android/Termux, where the official API was confirmed reachable.
Keeps the manifest fixed across batches and publishes only via validated sync.
Does not modify FINAL snapshots or forecast documents.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector.sync_official_api import discover_recent_city_events
from collector.validate_snapshot import read, validate_snapshot

ROOT = Path(__file__).resolve().parents[1]
START, END = "2026-09-21", "2026-10-11"
BATCH_SIZE = 12
JST = timezone(timedelta(hours=9))


def write_report(path, data):
    destination = path.with_suffix(".json.part")
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    destination.replace(path)


def main():
    if os.environ.get("TERMUX_BASE_URL"):
        raise RuntimeError("Run on Android with TERMUX_BASE_URL unset; not on a GitHub runner")
    if not (ROOT / "scripts/termux_refresh.sh").is_file():
        raise RuntimeError("Missing Termux refresh script")
    city = read(ROOT / "data/city_db.json")
    decks = read(ROOT / "data/city_decks.json")
    validate_snapshot(city, decks)
    root_tmp = ROOT / ".tmp"
    root_tmp.mkdir(exist_ok=True)
    report_path = root_tmp / "w03_official_reconcile_report.json"
    manifest_path = root_tmp / "w03_official_discovery_manifest.json"
    if report_path.exists():
        report_path.unlink()
    print("Official discovery for W01-W03:", START, "through", END, flush=True)
    rows = discover_recent_city_events(START, 100)
    scoped = [row for row in rows if START <= row["date"] <= END]
    if not scoped:
        raise RuntimeError("Official discovery returned no W01-W03 events; not claiming success")
    if len({row["event_id"] for row in scoped}) != len(scoped):
        raise RuntimeError("Official discovery contains duplicate event identities")
    # All result candidates are pinned exactly once before any detail pagination.
    write_report(manifest_path, scoped)
    failures, unpublished, checked = {}, [], set()
    batch_reports = []
    summary = {
        "stage": "INTERIM", "calendar_id": "2027-s1-mon-sun-v1",
        "scope_start": START, "scope_end": END,
        "discovered_event_count": len(scoped),
        "started_at": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "official event_search and event_result_detail_search",
        "batches": batch_reports, "status": "IN_PROGRESS",
    }
    write_report(report_path, summary)

    for offset in range(0, len(scoped), BATCH_SIZE):
        expected = {row["event_id"] for row in scoped[offset:offset+BATCH_SIZE]}
        environment = os.environ.copy()
        environment["CITY_OFFICIAL_DISCOVERY_FILE"] = str(manifest_path)
        environment["CITY_OFFICIAL_BATCH_OFFSET"] = str(offset)
        environment["CITY_OFFICIAL_BATCH_SIZE"] = str(BATCH_SIZE)
        environment.pop("TERMUX_BASE_URL", None)
        print(f"Official detail batch {offset//BATCH_SIZE+1}: events {offset+1}-{min(offset+BATCH_SIZE,len(scoped))}/{len(scoped)}", flush=True)
        result = subprocess.run(["bash", str(ROOT / "scripts/termux_refresh.sh"), "once"],
                                cwd=ROOT, env=environment, check=False)
        if result.returncode:
            summary["status"] = "FAIL"
            summary["error"] = f"Official collection transaction failed in batch offset {offset}"
            write_report(report_path, summary)
            raise RuntimeError(summary["error"])
        audit_file = root_tmp / "sync/official/official_audit.json"
        if not audit_file.exists():
            raise RuntimeError("Missing official detail audit; cannot certify batch")
        audit = read(audit_file)
        observations = audit.get("events", [])
        batch_errors = audit.get("fetch_errors", {})
        covered = {str(row.get("event_id")) for row in observations} | set(batch_errors)
        if (covered != expected or audit.get("selected_event_count") != len(expected)):
            summary["status"] = "FAIL"
            summary["error"] = f"Official audit did not account for every event in batch {offset}"
            write_report(report_path, summary)
            raise RuntimeError(summary["error"])
        checked.update(covered)
        failures.update(batch_errors)
        unpublished.extend(str(row["event_id"]) for row in observations
                           if row.get("status") == "result unpublished/empty")
        deferred = audit.get("top16_deferred_codes", [])
        batch_reports.append({
            "offset": offset, "checked_events": len(covered),
            "fetch_error_ids": sorted(batch_errors),
            "unpublished_or_empty_ids": sorted(
                str(row["event_id"]) for row in observations
                if row.get("status") == "result unpublished/empty"),
            "deferred_top16_decks": len(deferred),
        })
        if deferred:
            failures[f"batch_offset_{offset}_top16"] = f"{len(deferred)} Top16 lists deferred"
        validate_snapshot(read(ROOT / "data/city_db.json"), read(ROOT / "data/city_decks.json"),
                          city, decks)
        write_report(report_path, summary)

    final_city = read(ROOT / "data/city_db.json")
    final_decks = read(ROOT / "data/city_decks.json")
    counts = validate_snapshot(final_city, final_decks, city, decks)
    summary.update(
        finished_at=datetime.now(JST).isoformat(timespec="seconds"),
        checked_event_count=len(checked),
        unpublished_or_empty_ids=sorted(set(unpublished)),
        fetch_errors=failures,
        official_results_complete=None,
        source_updated_at=final_city.get("updated_at"),
        validated_counts=counts,
        status="PASS" if not failures and not unpublished and len(checked)==len(scoped) else "WARN",
    )
    write_report(report_path, summary)
    print("W03 RECONCILIATION RESULT: " + json.dumps(
        {k: summary[k] for k in ("status", "discovered_event_count",
                                  "checked_event_count", "source_updated_at",
                                  "validated_counts")}, ensure_ascii=False), flush=True)
    if failures:
        print("Official fetch errors/deferred lists: " + json.dumps(failures, ensure_ascii=False), flush=True)
    if unpublished:
        print(f"Unpublished or empty official results: {len(set(unpublished))} events", flush=True)
    print("W03 official audit:", report_path, flush=True)
    return 0 if summary["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
