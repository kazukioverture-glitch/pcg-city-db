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
