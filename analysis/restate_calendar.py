"""Produce versioned Monday-Sunday W01/W02 *restatements* from one DB revision.

A restatement is NOT a FINAL immutable input snapshot. The original W02 FINAL
and all forecast records remain untouched. Output paths include the git SHA;
files are exclusively created and never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

from .state import ROOT, read_json
from .week_calendar import CALENDAR_ID, week_event_ids, week_period
from .weekly import audit_report, build_weekly_report, render_markdown


def _git_head(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _source(root: Path) -> tuple[dict, dict, dict, dict, dict]:
    paths = {
        "city": root / "data/city_db.json",
        "decks": root / "data/city_decks.json",
        "classifications": root / "data/analysis/deck_classifications.json",
        "index": root / "data/index.json",
    }
    raw = {name: path.read_bytes() for name, path in paths.items()}
    documents = {name: json.loads(body) for name, body in raw.items()}
    hashes = {str(paths[name].relative_to(root)): hashlib.sha256(body).hexdigest()
              for name, body in raw.items()}
    if documents["city"].get("updated_at") != documents["index"].get("source_updated_at"):
        raise ValueError("City and index source timestamps do not match")
    if len(documents["city"].get("events", [])) != documents["index"].get("event_count"):
        raise ValueError("City event count disagrees with index")
    return (documents["city"], documents["decks"], documents["classifications"],
            documents["index"], hashes)


def _json_bytes(document: dict) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def _exclusive(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as target:
        target.write(body)


def restate(root: Path, output_dir: Path, weeks: list[str],
            *, category: str = "オープン", region: str = "愛知県") -> dict:
    root = root.resolve()
    output_dir = output_dir.resolve()
    if output_dir.is_relative_to(root / "data/analysis/weekly_snapshots"):
        raise ValueError("Never write inside immutable snapshots")
    if output_dir.is_relative_to(root / "data/analysis/answer_index"):
        raise ValueError("Never overwrite the legacy answer index")

    head = _git_head(root)
    city, decks, classifications, index, hashes = _source(root)
    source_updated = index["source_updated_at"]
    source_day = date.fromisoformat(source_updated[:10])
    generated_at = datetime.now(timezone.utc).isoformat()
    selected = []
    summaries = []
    for week_id in weeks:
        start, end = week_period(week_id)
        if source_day <= end:
            raise ValueError(f"{week_id}: source date has not passed week end")
        ids = week_event_ids(city, week_id)
        if not ids:
            raise ValueError(f"{week_id}: no observed event results; cannot restate")
        previous = None
        if int(week_id[1:]) > 1:
            previous = week_period(f"W{int(week_id[1:])-1:02d}")
        metadata = {
            "schema_version": "1.2.2", "kind": "weekly_snapshot",
            "week_id": week_id, "analysis_stage": "RESTATED",
            "snapshot_id": f"restatement-{head[:12]}-{week_id.lower()}",
            "period_start": f"{start.isoformat()}T00:00:00+09:00",
            "period_end": f"{end.isoformat()}T23:59:59+09:00",
            "cutoff_datetime": source_updated, "retrieved_at": source_updated,
            "git_commit_sha": head, "git_dirty": False, "event_ids": ids,
            "references": [],
        }
        report = build_weekly_report(
            metadata, city, decks, classifications,
            category=category, region=region,
            compare_start=previous[0].isoformat() if previous else None,
            compare_end=previous[1].isoformat() if previous else None,
            generated_at=generated_at,
        )
        if previous:
            report["trend"]["historical_basis"] = ("recomputed_from_same_pinned_git_revision_captured_city_data")
        report["provenance"].update({
            "calendar_id": CALENDAR_ID,
            "source_files_sha256": hashes,
            "source_event_data_updated_at": source_updated,
            "restatement_kind": "git-versioned-derivative-not-immutable-FINAL-snapshot",
            "original_wednesday_tuesday_FINAL_preserved": True,
        })
        report["limitations"].append(
            "This RESTATED analysis uses captured published results at the pinned git revision. "
            "It is not a replacement for an immutable FINAL input snapshot or a count "
            "of all event participants. Top16 uses only complete observed event subsets.")
        report["audit"] = audit_report(report)
        if report["audit"]["status"] == "FAIL":
            raise ValueError(f"{week_id} audit failed: {report['audit']['findings']}")
        national = report["current_week"]["national"]["analysis"]
        top16 = national["top16_context"]
        coverage = national["coverage"]
        if top16["coverage"]["captured_matched_top8_rows"] > coverage["captured_top8_rows"]:
            raise ValueError("Top16 matched Top8 exceeds all Top8")
        if top16["coverage"]["captured_top16_rows"] < top16["coverage"]["captured_matched_top8_rows"]:
            raise ValueError("Top16 row count smaller than the matched Top8")
        summary = {
            "week_id": week_id, "start": start.isoformat(), "end": end.isoformat(),
            "result_events_open": coverage["result_events"],
            "captured_top8_open": coverage["captured_top8_rows"],
            "top16_complete_events_open": top16["coverage"]["complete_top16_events"],
            "captured_top16_in_complete_events_open": top16["coverage"]["captured_top16_rows"],
            "captured_matched_top8_open": top16["coverage"]["captured_matched_top8_rows"],
            "audit": report["audit"]["status"],
            "top16_status": top16["status"],
            "scheduled_event_coverage": "not_assessed",
        }
        summaries.append(summary)
        selected.append((week_id, report))

    base = output_dir / head[:12]
    if base.exists():
        raise FileExistsError(f"Immutable restatement revision already exists: {base}")
    manifest = {
        "schema_version": "calendar-restatement-v1",
        "calendar_id": CALENDAR_ID,
        "input_git_commit_sha": head,
        "source_updated_at": source_updated,
        "input_sha256": hashes,
        "generated_at": generated_at,
        "category": category, "region": region,
        "status": "restated_not_FINAL",
        "legacy_snapshot_mutated": False,
        "forecast_log_mutated": False,
        "weeks": summaries,
    }
    # Guard the complete revision directory against collisions before any output.
    base.mkdir(parents=True, exist_ok=False)
    for week_id, report in selected:
        _exclusive(base / week_id / "report.json", _json_bytes(report))
        heading = (
            f"> 再集計版 / {CALENDAR_ID} / source commit {head} / "
            "旧FINALはそのまま保存。全参加者使用率ではありません。\n\n"
        )
        markdown = render_markdown(report)
        markdown = markdown.replace(
            f"- snapshot: `{report['snapshot_id']}`",
            f"- 再集計ID: `{report['snapshot_id']}`（旧FINAL入力snapshotとは別物）")
        markdown = markdown.replace(
            "snapshot内60枚リストからのpost-snapshot派生分析を含む。",
            "固定Git版の60枚リストからの派生分析を含む。")
        markdown = markdown.replace(
            "W01当時の固定snapshotではなく、同じFINAL入力の過去週データを再計算。",
            "同一Git版の公開結果から、以前の月曜〜日曜を再計算。")
        _exclusive(base / week_id / "report.md",
                   (heading + markdown).encode("utf-8"))
    _exclusive(base / "manifest.json", _json_bytes(manifest))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data/analysis/weekly_restatements" / CALENDAR_ID)
    parser.add_argument("--weeks", nargs="+", default=["W01", "W02"])
    parser.add_argument("--category", default="オープン")
    parser.add_argument("--region", default="愛知県")
    args = parser.parse_args()
    result = restate(args.root, args.output_dir, args.weeks,
                     category=args.category, region=args.region)
    print(json.dumps({"calendar_id": result["calendar_id"],
                      "git": result["input_git_commit_sha"],
                      "weeks": result["weeks"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
