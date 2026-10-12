"""Publish a versioned W03 INTERIM report using captured public results only.

No mutation of City DB, previous FINAL, forecast log or answer-index.
Run only on GitHub Actions, with a complete repository checkout.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .state import ROOT
from .week_calendar import CALENDAR_ID, week_period, week_event_ids
from .weekly import build_weekly_report, render_markdown, audit_report


def main() -> int:
    repo = Path(ROOT).resolve()
    source_paths = {
        "city": repo / "data/city_db.json",
        "decks": repo / "data/city_decks.json",
        "classifications": repo / "data/analysis/deck_classifications.json",
        "index": repo / "data/index.json",
    }
    raw = {k: path.read_bytes() for k, path in source_paths.items()}
    documents = {k: json.loads(b) for k, b in raw.items()}
    city, decks, classes, index = (documents[k] for k in
                                   ("city", "decks", "classifications", "index"))
    if city.get("updated_at") != index.get("source_updated_at"):
        raise ValueError("Unmatched City DB and index timestamps")
    if len(city.get("events", [])) != index.get("event_count"):
        raise ValueError("Unmatched event count")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    week_id = "W03"
    start, end = week_period(week_id)
    event_ids = week_event_ids(city, week_id)
    if not event_ids:
        raise ValueError("No W03 results; cannot generate INTERIM")
    events = [e for e in city["events"] if e["event_id"] in set(event_ids)]
    observed_max_day = max(e["date"] for e in events)
    cutoff = index["source_updated_at"]
    now = datetime.now(timezone.utc).isoformat()
    metadata = {
        "schema_version": "1.2.2", "kind": "weekly_snapshot",
        "week_id": week_id, "analysis_stage": "INTERIM",
        "snapshot_id": "interim-" + head[:12] + "-" + week_id.lower(),
        "period_start": start.isoformat() + "T00:00:00+09:00",
        "period_end": end.isoformat() + "T23:59:59+09:00",
        "cutoff_datetime": cutoff, "retrieved_at": cutoff,
        "git_commit_sha": head, "git_dirty": False,
        "event_ids": event_ids, "references": [],
    }
    prev_start, prev_end = week_period("W02")
    report = build_weekly_report(
        metadata, city, decks, classes, category="オープン", region="愛知県",
        compare_start=prev_start.isoformat(), compare_end=prev_end.isoformat(),
        generated_at=now)
    hashes = {str(path.relative_to(repo)): hashlib.sha256(raw[k]).hexdigest()
              for k, path in source_paths.items()}
    report["trend"]["historical_basis"] = (
        "recomputed_from_same_pinned_git_revision_captured_city_data")
    report["provenance"].update({
        "calendar_id": CALENDAR_ID,
        "restatement_kind": "versioned_interim_preview_not_final",
        "source_files_sha256": hashes,
        "source_event_data_updated_at": cutoff,
        "last_observed_event_day": observed_max_day,
        "full_week_coverage_verified": False,
        "scheduled_event_coverage": "not_assessed",
    })
    report["limitations"].append(
        "W03 INTERIM. The data source is last updated at " + cutoff +
        "; full scheduled-event and Top16 coverage have not been independently "
        "verified, and missing results must not be counted as zero.")
    report["audit"] = audit_report(report)
    if report["audit"]["status"] == "FAIL":
        raise RuntimeError("W03 audit FAIL")
    scope = report["current_week"]["national"]["analysis"]
    top16 = scope["top16_context"]
    summary = {
        "calendar_id": CALENDAR_ID,
        "week_id": week_id,
        "stage": "INTERIM",
        "week_start": start.isoformat(), "week_end": end.isoformat(),
        "source_updated_at": cutoff,
        "last_observed_event_day": observed_max_day,
        "input_commit": head, "input_sha256": hashes,
        "result_events_open": scope["coverage"]["result_events"],
        "top8_rows_open": scope["coverage"]["captured_top8_rows"],
        "top8_deck_lists_known": scope["coverage"]["known_top8_deck_lists"],
        "top16_complete_events_open": top16["coverage"]["complete_top16_events"],
        "top16_rows_complete_open": top16["coverage"]["captured_top16_rows"],
        "top16_matched_top8_open": top16["coverage"]["captured_matched_top8_rows"],
        "aichi_events": report["current_week"]["regional"]["analysis"]["coverage"]["result_events"],
        "aichi_top8_rows": report["current_week"]["regional"]["analysis"]["coverage"]["captured_top8_rows"],
        "audit": report["audit"]["status"],
        "full_week_coverage_verified": False,
        "legacy_final_mutated": False,
    }
    base = (repo / "data/analysis/weekly_previews" / CALENDAR_ID
            / head[:12] / week_id)
    base.mkdir(parents=True, exist_ok=False)
    (base / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = (
        "> **W03 INTERIM**: 10月11日分のオープンTop8は捕捉しましたが、"
        "Top16補完と全開催カテゴリの検収は未完了です。"
        "旧W02 FINALと予測原文には変更を加えていません。"
        f" DB最終更新: {cutoff}。\n\n" + render_markdown(report)
    )
    markdown = markdown.replace(
        "同じFINAL入力の過去週データを再計算。",
        "同一Git版で直前の月〜日を再計算。"
    ).replace(
        "snapshot内60枚リストからのpost-snapshot派生分析を含む。",
        "Git固定版の公開60枚リストから分類を再計算。")
    (base / "report.md").write_text(markdown, encoding="utf-8")
    (base / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
