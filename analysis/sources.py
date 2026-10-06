"""Structured independent external-source analysis records."""

from pathlib import Path

from .state import ROOT, read_json, read_state, write_state

ANALYSIS_FIELDS = {
    "published_at", "target_week", "language", "translation_used",
    "video_visuals_verified", "confirmed_facts", "author_analysis",
    "analysis_basis", "predictions", "caveats", "tracking_signals",
}


def append_source_analysis(record_path, output=None):
    """Append one independently analysed source; never fetch or inspect DB here."""
    record = read_json(record_path)
    base_required = {
        "source_id", "source_type", "url", "content_sha256", "processed_at",
        "status", "snapshot_ids", "notes",
    }
    allowed = base_required | ANALYSIS_FIELDS
    if set(record) != allowed:
        missing = sorted(allowed - set(record))
        extra = sorted(set(record) - allowed)
        raise ValueError(f"source analysis fields mismatch; missing={missing}, extra={extra}")
    if record["status"] != "processed":
        raise ValueError("independent source analysis must be recorded as processed")
    for name in ("confirmed_facts", "author_analysis", "analysis_basis",
                 "predictions", "caveats", "tracking_signals"):
        if not isinstance(record[name], list) or any(not isinstance(v, str) or not v.strip()
                                                     for v in record[name]):
            raise ValueError(f"{name} must be a list of non-empty strings")
    if record["target_week"] is not None and (not isinstance(record["target_week"], str)
                                               or not record["target_week"].strip()):
        raise ValueError("target_week must be null or non-empty")
    output = Path(output or ROOT / "data/analysis/processed_sources.json")
    document = read_state(output)
    if document["kind"] != "processed_sources":
        raise ValueError("output must be processed_sources")
    if any(r["source_id"] == record["source_id"] for r in document["records"]):
        raise ValueError("source_id already exists; processed analyses are immutable")
    document["records"].append(record)
    write_state(output, document)
    return {"source_id": record["source_id"], "record_count": len(document["records"])}
