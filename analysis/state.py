"""Validated state storage and immutable, self-contained input snapshots."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
STATE_FILES = {
    "event_ledger": "event_ledger.json",
    "deck_classifications": "deck_classifications.json",
    "processed_sources": "processed_sources.json",
    "research_requests": "research_requests.json",
    "forecast_log": "forecast_log.json",
}
DEFAULT_INPUTS = ["data/city_db.json", "data/city_decks.json", "data/index.json"]
FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=(ValueError, TypeError))
def _timestamp(value):
    # jsonschema's optional RFC3339 dependency may be absent. Enforce offsets here.
    if not isinstance(value, str):
        return True
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})", value):
        return False
    return datetime.fromisoformat(value.upper().replace("Z", "+00:00")).tzinfo is not None


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate(document):
    schema = read_json(ROOT / "schemas/analysis-state.schema.json")
    Draft202012Validator(schema, format_checker=FORMATS).validate(document)
    kind = document["kind"]
    records = document.get("records", [])
    key = {"event_ledger": "event_id", "deck_classifications": "deck_code",
           "processed_sources": "source_id", "research_requests": "request_id",
           "forecast_log": "forecast_id"}.get(kind)
    if key and len({r[key] for r in records}) != len(records):
        raise ValueError(f"Duplicate {key}")
    weeks = ([document["target_week"]] if kind == "weekly_snapshot" and document["schema_version"] == "1.2.1" else
             [r["target_week"] for r in records] if kind == "forecast_log" else [])
    for target_week in weeks:
        if re.fullmatch(r"\d{4}-W\d{2}", target_week):
            year, week = map(int, target_week.split("-W"))
            date.fromisocalendar(year, week, 1)
        elif kind == "weekly_snapshot":
            raise ValueError("Legacy snapshots require an ISO week")
    if kind == "event_ledger":
        for record in records:
            for observation in record["observations"]:
                known = observation["top8_decks_known"]
                retrieved = observation["top8_slots_retrieved"]
                if known is not None and retrieved is not None and known > retrieved:
                    raise ValueError("Known decks exceed retrieved Top8 slots")
    if kind == "weekly_snapshot":
        if document["schema_version"] == "1.2.2":
            start, end, cutoff = [datetime.fromisoformat(document[k].upper().replace("Z", "+00:00"))
                                  for k in ("period_start", "period_end", "cutoff_datetime")]
            if start > end or cutoff < start:
                raise ValueError("Require period_start <= period_end and cutoff_datetime >= period_start")
        refs = [r["path"] for r in document["references"]]
        stored = [r["snapshot_path"] for r in document["references"]]
        if len(set(refs)) != len(refs) or len(set(stored)) != len(stored):
            raise ValueError("Duplicate snapshot reference")
        for path in refs + stored:
            safe_relative(path)
    return document


def safe_relative(value):
    # Portable repository-relative paths, including on Windows.
    if (not value or "\\" in value or ":" in value or value.startswith("/")
            or any(p in ("", ".", "..") for p in value.split("/"))):
        raise ValueError(f"Unsafe relative path: {value}")
    return Path(value)


def _encode(document):
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_state(path, document):
    """Atomic replacement for mutable state only; snapshots use exclusive creation."""
    validate(document)
    if document["kind"] == "weekly_snapshot":
        raise ValueError("Use create_snapshot; snapshot metadata cannot be overwritten")
    path = Path(path)
    if path.exists():
        previous = read_state(path)
        if previous["kind"] != document["kind"]:
            raise ValueError("Cannot replace a different state kind")
    if path.exists() and document["kind"] == "event_ledger":
        current = {r["event_id"]: r for r in document["records"]}
        for record in previous["records"]:
            history = record["observations"]
            if (record["event_id"] not in current or
                    current[record["event_id"]]["observations"][:len(history)] != history):
                raise ValueError("Ledger history cannot be removed or rewritten")

    if path.exists() and document["kind"] == "processed_sources":
        current = {r["source_id"]: r for r in document["records"]}
        for record in previous["records"]:
            if record["source_id"] not in current:
                raise ValueError("Processed source history cannot be removed")
            if record.get("status") == "processed" and current[record["source_id"]] != record:
                raise ValueError("Processed source analysis is immutable; use a new source_id")
    if path.exists() and document["kind"] == "forecast_log":
        current = {r["forecast_id"]: r for r in document["records"]}
        for record in previous["records"]:
            # Protect legacy forecasts as well as new formal records. Results are
            # appended separately; never relabel LATE/POST as PRE after the fact.
            if current.get(record["forecast_id"]) != record:
                raise ValueError("Forecast content is immutable; use a new forecast_id")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_encode(document))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_state(path):
    return validate(read_json(path))


def import_legacy(city):
    """Import only explicit facts. Historical fetch/publication counts stay unknown."""
    records = []
    for event in city["events"]:
        records.append({
            "event_id": event["event_id"], "event_date": event.get("date"),
            "category": event.get("category"), "venue_name": None,
            "venue_prefecture": None, "event_status": "unknown",
            "observations": [],
            "legacy_source": "data/city_db.json",
            "players": [{"player_id": p.get("player_id"),
                         "player_prefecture": p.get("prefecture")}
                        for p in event.get("placements", [])],
        })
    return validate({"schema_version": "1.2.1", "kind": "event_ledger", "records": records})


def append_observation(ledger, event_id, observation):
    """Preserve previous publication facts even if today's fetch fails."""
    # Copy before validation so an invalid observation cannot corrupt caller state.
    result = json.loads(json.dumps(ledger))
    for record in result["records"]:
        if record["event_id"] == event_id:
            record["observations"].append(observation)
            return validate(result)
    raise KeyError(event_id)


def events_in_prefecture(ledger, prefecture):
    """Regional event selection uses the venue only. Null stays unassigned."""
    validate(ledger)
    return [r for r in ledger["records"] if r["venue_prefecture"] == prefecture]


def create_snapshot(root, target_week=None, event_ids=None, retrieved_at=None, files=None, snapshot_id=None,
                    *, week_id=None, period_start=None, period_end=None,
                    analysis_stage=None, cutoff_datetime=None):
    """Capture exact input bytes with HEAD and hashes; never replace a prior version.

    retrieved_at is the caller's actual acquisition time, not the snapshot creation time.
    Files may be dirty: git_dirty records this and the copied bytes are authoritative.
    """
    root = Path(root).resolve()
    custom = any(v is not None for v in (week_id, period_start, period_end, analysis_stage, cutoff_datetime))
    if custom and not all(v is not None for v in (week_id, period_start, period_end, analysis_stage, cutoff_datetime)):
        raise ValueError("Custom weeks require week_id, period_start, period_end, analysis_stage, cutoff_datetime")
    if custom and target_week is not None:
        raise ValueError("Use week_id or legacy target_week, not both")
    paths = list(DEFAULT_INPUTS if files is None else files)
    if files is None:
        paths += [f"data/analysis/{name}" for name in STATE_FILES.values()]
    snapshot_id = snapshot_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", snapshot_id):
        raise ValueError("Invalid snapshot ID")
    inputs = {}
    for relative in paths:
        path = (root / safe_relative(relative)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Input escapes repository")
        if relative in inputs:
            raise ValueError("Duplicate input path")
        inputs[relative] = path.read_bytes()
    # Membership is verified against captured inputs, never a later read.
    if "data/city_db.json" not in inputs:
        raise ValueError("Snapshot must include data/city_db.json")
    available = {e["event_id"] for e in json.loads(inputs["data/city_db.json"])["events"]}
    if "data/analysis/event_ledger.json" in inputs:
        ledger = validate(json.loads(inputs["data/analysis/event_ledger.json"]))
        if ledger["kind"] != "event_ledger":
            raise ValueError("Invalid captured event ledger")
        available.update(r["event_id"] for r in ledger["records"])
    if not set(event_ids or []) <= available:
        raise ValueError("Target event IDs missing from captured city DB and event ledger")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True).strip())
    document = {
        "schema_version": "1.2.1", "kind": "weekly_snapshot",
        "snapshot_id": snapshot_id, "target_week": target_week,
        "retrieved_at": retrieved_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event_ids": list(event_ids or []), "git_commit_sha": sha, "git_dirty": dirty,
        "references": [{"path": name, "snapshot_path": f"inputs/{name}",
                        "sha256": hashlib.sha256(content).hexdigest()}
                       for name, content in inputs.items()],
    }
    if custom:
        document.pop("target_week")
        document.update(schema_version="1.2.2", week_id=week_id, period_start=period_start,
                        period_end=period_end, analysis_stage=analysis_stage, cutoff_datetime=cutoff_datetime)
    validate(document)
    directory = root / "data/analysis/weekly_snapshots" / (week_id if custom else target_week)
    if custom:
        directory /= analysis_stage
    directory /= snapshot_id
    directory.mkdir(parents=True, exist_ok=False)
    # Metadata is published last: a failed capture has no valid manifest.
    for reference in document["references"]:
        path = directory / reference["snapshot_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(inputs[reference["path"]])
    with (directory / "metadata.json").open("xb") as handle:
        handle.write(_encode(document))
    return directory


def verify_snapshot(directory):
    directory = Path(directory).resolve()
    document = read_state(directory / "metadata.json")
    if document["kind"] != "weekly_snapshot":
        raise ValueError("Not a weekly snapshot")
    for reference in document["references"]:
        path = (directory / safe_relative(reference["snapshot_path"])).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Snapshot input escapes directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != reference["sha256"]:
            raise ValueError(f"SHA256 mismatch: {reference['path']}")
    return document
