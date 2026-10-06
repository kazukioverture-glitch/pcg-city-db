"""Bridge collected result feeds/list links to append-only event observations.

This adapter records source acquisition, not a fresh fetch of every result page.
Missing events or failed feed downloads never imply cancellation/non-publication.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import ValidationError

from .state import import_legacy, read_json, read_state, validate, write_state


def sync_ledger(ledger_path, *, city_path=None, decks_path=None, fetch_status,
                coverage_scope, source_url, observed_at=None, parse_status=None):
    ledger_path = Path(ledger_path)
    ledger = read_state(ledger_path) if ledger_path.exists() else {
        "schema_version": "1.2.2", "kind": "event_ledger", "records": []}
    if ledger["kind"] != "event_ledger":
        raise ValueError("Expected event ledger")
    ledger["schema_version"] = "1.2.2"
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    source_sha = None
    updated_at = None
    city = None
    notes = "Source acquisition only; official result page completeness is not certified."
    if fetch_status == "success" and parse_status not in ("failed", "not_parsed"):
        try:
            content = Path(city_path).read_bytes()
            source_sha = hashlib.sha256(content).hexdigest()
            city = json.loads(content)
            if (not isinstance(city, dict) or not isinstance(city.get("events"), list)
                    or not city["events"]):
                raise ValueError("Missing/non-list events")
            # Validate IDs/dates before merging any event into the stored ledger.
            if not all(isinstance(e, dict) for e in city["events"]):
                raise ValueError("Invalid event row")
            import_legacy({"events": [{**e, "placements": []} for e in city["events"]]})
            updated_at = city.get("updated_at")
        except (OSError, ValueError, TypeError, KeyError) as error:
            city = None
            parse_status = "failed"
            notes = f"Collected source could not be parsed: {type(error).__name__}"
        # jsonschema's ValidationError is handled explicitly, without catching
        # unrelated programming failures.
        except ValidationError:
            city = None
            parse_status = "failed"
            notes = "Collected event metadata failed schema validation."
    if fetch_status != "success":
        parse_status = "not_parsed"
        notes = "Source acquisition failed/not attempted; no inference about official publication."
    elif parse_status == "failed":
        notes = "Source parse/validation failed; previous facts retained."

    decks = None
    if decks_path is not None and fetch_status == "success" and city is not None:
        try:
            value = read_json(decks_path)
            decks = value.get("decks") if isinstance(value, dict) else None
            if not isinstance(decks, dict):
                decks = None
        except (OSError, ValueError):
            pass

    current = {record["event_id"]: record for record in ledger["records"]}
    added = 0
    if city is None:
        # A feed/list failure affects only events previously observed from that
        # source. Do not mark scheduled ledger-only events as attempted.
        events = [{"event_id": record["event_id"]} for record in ledger["records"]
                  if (any(o["source_url"] == source_url for o in record["observations"])
                      or (coverage_scope == "collection_feed" and record["legacy_source"] == "data/city_db.json"))]
    else:
        events = city["events"]
    for event in events:
        event_id = event["event_id"]
        if event_id not in current:
            record = import_legacy({"events": [{**event, "placements": []}]})["records"][0]
            record["legacy_source"] = str(city_path) if city_path else None
            ledger["records"].append(record)
            current[event_id] = record
        record = current[event_id]
        if city is not None:
            for source_key, destination_key in (("date", "event_date"), ("category", "category"),
                                                 ("venue_name", "venue_name"), ("venue_prefecture", "venue_prefecture")):
                if event.get(source_key) is not None:
                    record[destination_key] = event[source_key]
            # Only explicit event_status is accepted; never infer held/cancelled.
            if event.get("event_status") is not None:
                record["event_status"] = event["event_status"]
            if isinstance(event.get("placements"), list):
                record["players"] = [{"player_id": p.get("player_id"), "player_prefecture": p.get("prefecture")}
                                     for p in event["placements"] if isinstance(p, dict)]
        previous = [o for o in record["observations"] if o["source_url"] == source_url]
        publication = next((o["publication_status"] for o in reversed(previous)
                            if o["publication_status"] != "unknown"), "unknown")
        parsed = parse_status or "success"
        retrieved = known = None
        if city is not None:
            placements = event.get("placements")
            if coverage_scope == "result_list":
                # A result link was observed on the official published-results list.
                publication = "published" if event.get("result_url") else "unknown"
            elif isinstance(placements, list):
                valid = [p for p in placements if isinstance(p, dict)
                         and type(p.get("rank")) is int and p["rank"] > 0]
                if valid:
                    publication = "published"
                parsed = "success" if len(valid) == len(placements) else "partial"
                top8 = [p for p in valid if p["rank"] <= 8]
                # More than 8 tied rows need explicit upstream coverage metadata.
                if len(top8) <= 8:
                    retrieved = len(top8)
                    if decks is not None:
                        known = sum(isinstance(decks.get(p.get("deck_code")), dict)
                                    and decks[p["deck_code"]].get("total_cards") == 60
                                    for p in top8 if isinstance(p.get("deck_code"), str))
            else:
                parsed = "partial"
        observation = dict(observed_at=observed_at, source_url=source_url,
                           publication_status=publication, fetch_status=fetch_status,
                           parse_status=parsed, coverage_scope=coverage_scope,
                           published_rank_slots_total=None, top8_slots_published=None,
                           top8_slots_retrieved=retrieved, top8_decks_known=known, notes=notes,
                           source_sha256=source_sha, source_updated_at=updated_at)
        # Each collection attempt is recorded. Explicit upstream publication
        # counts are not reconstructed from the number of parsed rows.
        record["observations"].append(observation)
        added += 1
    validate(ledger)
    write_state(ledger_path, ledger)
    return added



def sync_schedule_snapshot(ledger_path, snapshot_path, observed_at=None):
    """Merge only changed next-day schedule/venue facts into the event ledger."""
    ledger_path = Path(ledger_path)
    snapshot_path = Path(snapshot_path)
    content = snapshot_path.read_bytes()
    payload = json.loads(content)
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise ValueError("Schedule snapshot events must be a list")
    source_url = payload.get("source")
    source_updated_at = payload.get("updated_at")
    target_date = payload.get("target_date")
    if not isinstance(source_url, str) or not source_url:
        raise ValueError("Schedule snapshot source is required")
    if not isinstance(source_updated_at, str) or not source_updated_at:
        raise ValueError("Schedule snapshot updated_at is required")
    parsed_time = datetime.fromisoformat(source_updated_at.upper().replace("Z", "+00:00"))
    if parsed_time.tzinfo is None:
        raise ValueError("Schedule snapshot updated_at requires timezone")

    ledger = read_state(ledger_path) if ledger_path.exists() else {
        "schema_version": "1.2.2", "kind": "event_ledger", "records": []}
    if ledger["kind"] != "event_ledger":
        raise ValueError("Expected event ledger")
    ledger["schema_version"] = "1.2.2"
    current = {record["event_id"]: record for record in ledger["records"]}
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    source_sha = hashlib.sha256(content).hexdigest()
    seen = set()
    changed = 0

    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Invalid schedule event row")
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in seen:
            raise ValueError("Missing or duplicate schedule event_id")
        seen.add(event_id)
        event_date = event.get("date")
        venue_name = event.get("venue_name")
        venue_prefecture = event.get("venue_prefecture")
        if not isinstance(event_date, str) or not event_date:
            raise ValueError("Schedule event date is required")
        if venue_name is not None and (not isinstance(venue_name, str) or not venue_name.strip()):
            raise ValueError("Invalid venue_name")
        if venue_prefecture is not None and (not isinstance(venue_prefecture, str) or not venue_prefecture.strip()):
            raise ValueError("Invalid venue_prefecture")

        record = current.get(event_id)
        created = record is None
        if created:
            record = {
                "event_id": event_id,
                "event_date": event_date,
                "category": None,
                "venue_name": venue_name,
                "venue_prefecture": venue_prefecture,
                "event_status": "scheduled",
                "observations": [],
                "legacy_source": None,
                "players": [],
            }
            ledger["records"].append(record)
            current[event_id] = record

        before = (record.get("event_date"), record.get("venue_name"),
                  record.get("venue_prefecture"), record.get("event_status"))
        record["event_date"] = event_date
        if venue_name is not None:
            record["venue_name"] = venue_name.strip()
        if venue_prefecture is not None:
            record["venue_prefecture"] = venue_prefecture.strip()
        after = (record.get("event_date"), record.get("venue_name"),
                 record.get("venue_prefecture"), record.get("event_status"))

        if not created and before == after:
            continue
        record["observations"].append({
            "observed_at": observed_at,
            "source_url": source_url,
            "publication_status": "unknown",
            "fetch_status": "success",
            "parse_status": "success",
            "coverage_scope": "unknown",
            "published_rank_slots_total": None,
            "top8_slots_published": None,
            "top8_slots_retrieved": None,
            "top8_decks_known": None,
            "notes": f"Scheduled event/venue snapshot for {target_date or event_date}",
            "source_sha256": source_sha,
            "source_updated_at": source_updated_at,
        })
        changed += 1

    if changed:
        validate(ledger)
        write_state(ledger_path, ledger)
    return changed
