"""Compact, reusable answer artifacts derived from immutable FINAL snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .state import ROOT, read_json, verify_snapshot
from .weekly import (
    _classification_resolver,
    _filter_events,
    _placements,
    _valid_deck,
    analyze_snapshot,
)


def _json_bytes(document):
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".answer-index-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _repo_relative(path):
    resolved = Path(path).resolve()
    root = ROOT.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Answer index output must stay inside repository root")
    return resolved.relative_to(root).as_posix()


def build_deck_index(metadata, city, decks_document, classifications, *,
                     category="オープン", generated_at=None):
    """Build one compact Top8 row per captured result for fast follow-up queries."""
    if metadata.get("kind") != "weekly_snapshot" or metadata.get("schema_version") != "1.2.2":
        raise ValueError("answer deck index requires a v1.2.2 weekly snapshot")
    decks = decks_document.get("decks", {})
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    resolve = _classification_resolver(classifications, decks, generated_at)
    start, end = metadata["period_start"][:10], metadata["period_end"][:10]
    events = _filter_events(
        city, start, end, category, event_ids=metadata["event_ids"])
    rows = []
    for row in _placements(events, 8):
        classification = resolve(row["deck_code"])
        rows.append({
            "event_id": row["event_id"],
            "event_date": row["event_date"],
            "venue_prefecture": row["venue_prefecture"],
            "rank": row["rank"],
            "deck_code": row["deck_code"],
            "parent_archetype": classification["parent"],
            "variant_tags": classification["variant_tags"],
            "classification_status": classification["status"],
            "classification_source": classification["source"],
            "valid_60": _valid_deck(row["deck_code"], decks) is not None,
        })
    rows.sort(key=lambda r: (
        r["event_date"] or "", r["event_id"], r["rank"], r["deck_code"] or ""))
    return {
        "schema_version": "answer-decks-v1",
        "week_id": metadata["week_id"],
        "analysis_stage": metadata["analysis_stage"],
        "snapshot_id": metadata["snapshot_id"],
        "category": category,
        "row_scope": "captured_top8_rows_only",
        "row_count": len(rows),
        "valid_60_count": sum(r["valid_60"] for r in rows),
        "rows": rows,
    }


def materialize_answer_index(snapshot_dir, *, output_root=None,
                             category="オープン", region="愛知県",
                             compare_start=None, compare_end=None,
                             watch_config=None, external_sources=None,
                             generated_at=None):
    """Write reusable summary/deck artifacts plus a tiny current.json pointer."""
    snapshot_dir = Path(snapshot_dir)
    metadata = verify_snapshot(snapshot_dir)
    if metadata.get("schema_version") != "1.2.2":
        raise ValueError("answer index requires a v1.2.2 snapshot")
    if metadata.get("analysis_stage") != "FINAL":
        raise ValueError("Only FINAL snapshots may become the reusable answer index")

    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    report = analyze_snapshot(
        snapshot_dir, category=category, region=region,
        compare_start=compare_start, compare_end=compare_end,
        watch_config=watch_config, external_sources=external_sources,
        generated_at=generated_at)
    if report["audit"]["status"] == "FAIL":
        raise ValueError("Refuse to materialize answer index from audit FAIL report")

    def load(relative, required=True):
        path = snapshot_dir / "inputs" / relative
        if not path.exists():
            if required:
                raise FileNotFoundError(path)
            return None
        return read_json(path)

    city = load("data/city_db.json")
    decks = load("data/city_decks.json")
    classifications = load("data/analysis/deck_classifications.json", required=False)
    if classifications is None:
        classifications = {"kind": "deck_classifications", "records": []}
    deck_index = build_deck_index(
        metadata, city, decks, classifications,
        category=category, generated_at=generated_at)

    classifier_sha = report["provenance"]["classifier_sha256"]
    generation_id = f'{metadata["snapshot_id"]}-{classifier_sha[:12]}'
    output_root = Path(output_root or (ROOT / "data/analysis/answer_index"))
    output_resolved = output_root.resolve()
    snapshot_resolved = snapshot_dir.resolve()
    if output_resolved == snapshot_resolved or output_resolved.is_relative_to(snapshot_resolved):
        raise ValueError("Answer index cannot be written inside immutable snapshot inputs")

    generation_dir = output_root / metadata["week_id"] / metadata["analysis_stage"] / generation_id
    summary_path = generation_dir / "summary.json"
    decks_path = generation_dir / "decks.json"
    summary_bytes = _json_bytes(report)
    decks_bytes = _json_bytes(deck_index)
    _atomic_write(summary_path, summary_bytes)
    _atomic_write(decks_path, decks_bytes)

    current = {
        "schema_version": "answer-index-v1",
        "week_id": metadata["week_id"],
        "analysis_stage": metadata["analysis_stage"],
        "snapshot_id": metadata["snapshot_id"],
        "generation_id": generation_id,
        "category": category,
        "region": region,
        "report_generated_at": report["generated_at"],
        "audit_status": report["audit"]["status"],
        "classifier_version": report["provenance"]["classifier_version"],
        "classifier_sha256": classifier_sha,
        "summary_path": _repo_relative(summary_path),
        "decks_path": _repo_relative(decks_path),
        "summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
        "decks_sha256": hashlib.sha256(decks_bytes).hexdigest(),
        "snapshot_git_commit_sha": report["provenance"]["snapshot_git_commit_sha"],
        "snapshot_reference_hashes": report["provenance"]["snapshot_reference_hashes"],
        "fast_path_rule": "read_summary_then_decks_then_raw_then_external",
    }
    current_path = output_root / "current.json"
    _atomic_write(current_path, _json_bytes(current))
    return {
        "current": _repo_relative(current_path),
        "summary": current["summary_path"],
        "decks": current["decks_path"],
        "generation_id": generation_id,
        "row_count": deck_index["row_count"],
        "audit_status": current["audit_status"],
    }
