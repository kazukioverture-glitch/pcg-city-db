"""Bounded, resumable Top16 backfill using the existing safe sync transaction.

Run on the Android collector, where the official results API is reachable.
This does not modify historical analysis snapshots.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CITY = ROOT / "data/city_db.json"
REPORT = ROOT / ".tmp/sync/sync_report.json"


def progress(through_date: str) -> dict:
    city = json.loads(CITY.read_text(encoding="utf-8"))
    events = [e for e in city["events"] if e.get("date") and e["date"] <= through_date]
    pending = 0
    observed = 0
    captured = 0
    complete = 0
    checked = 0
    categories = Counter()
    for event in events:
        category = event.get("category") or "未確認"
        categories[category] += 1
        extra = sum(8 < int(p["rank"]) <= 16 for p in event.get("placements", []))
        source_observed = int(event.get("top16_observed_rows") or 0)
        has_checked = bool(event.get("top16_checked_at"))
        checked += int(has_checked)
        observed += source_observed
        captured += extra
        complete += int(source_observed >= 8 and source_observed <= extra)
        pending += int(not has_checked or source_observed > extra)
    return dict(events=len(events), checked=checked, pending=pending,
                observed_top9_16_rows=observed, captured_top9_16_rows=captured,
                complete_top16_events=complete, categories=dict(sorted(categories.items())))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--through-date", default=date.today().isoformat())
    parser.add_argument("--max-runs", type=int, default=85)
    parser.add_argument("--max-minutes", type=int, default=180)
    parser.add_argument("--stall-limit", type=int, default=3)
    args = parser.parse_args()
    date.fromisoformat(args.through_date)
    if args.max_runs <= 0 or args.max_minutes <= 0 or args.stall_limit <= 0:
        parser.error("max-runs, max-minutes, and stall-limit must be positive")

    os.chdir(ROOT)
    environment = os.environ.copy()
    environment.pop("TERMUX_BASE_URL", None)
    start = time.monotonic()
    before = progress(args.through_date)
    print("TOP16 START: " + json.dumps(before, ensure_ascii=False), flush=True)
    runs = 0
    stalled = 0
    failure = None
    while before["pending"] and runs < args.max_runs:
        if time.monotonic() - start >= args.max_minutes * 60:
            failure = "time budget reached"
            break
        runs += 1
        print(f"TOP16 PASS {runs}: {before['pending']} event(s) awaiting complete check",
              flush=True)
        result = subprocess.run([sys.executable, "-m", "collector.sync"],
                                cwd=ROOT, env=environment)
        if result.returncode:
            failure = f"collection exited with status {result.returncode}"
            break
        try:
            report = json.loads(REPORT.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            failure = f"missing/invalid audit report: {error}"
            break
        if not report.get("published") or not report.get("official_accepted"):
            failure = "official source not accepted; no further backfill attempted"
            break
        after = progress(args.through_date)
        print("TOP16 PROGRESS: " + json.dumps(after, ensure_ascii=False),
              flush=True)
        made_progress = (after["checked"] > before["checked"] or
                         after["captured_top9_16_rows"] > before["captured_top9_16_rows"] or
                         after["pending"] < before["pending"])
        stalled = 0 if made_progress else stalled + 1
        before = after
        if stalled >= args.stall_limit:
            failure = f"no additional validated rows after {stalled} passes"
            break

    completed = before["pending"] == 0
    print("TOP16 FINAL: " + json.dumps({
        **before, "passes": runs, "all_stored_events_checked": completed,
        "failure": failure, "note": "A checked event may publish Top8 only; never infer unpublished Top16 rows."
    }, ensure_ascii=False), flush=True)
    return 0 if completed else 2


if __name__ == "__main__":
    raise SystemExit(main())
