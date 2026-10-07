"""Conservative, rule-driven deck classification; no collector or snapshot writes."""

import hashlib
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from .cards import section_type, validate_deck
from .state import read_json, read_state, write_state

IDENTITIES = read_json(Path(__file__).with_name("classification_cards.json"))
RULES = read_json(Path(__file__).with_name("archetype_rules.json"))
VERSION = RULES["version"]
LEGACY_AUTO_VERSION_PREFIXES = ("city-classifier-", "JP-")


def _validate_rules():
    if RULES.get("market") != "JP":
        raise ValueError("Archetype rules must explicitly target the JP card pool")
    reference = RULES.get("external_taxonomy_reference") or {}
    if reference.get("rules_imported") is not False:
        raise ValueError("External taxonomy rules must not be imported as JP truth")
    parents = RULES.get("parents")
    if not isinstance(parents, list) or not parents:
        raise ValueError("Archetype rules require a non-empty parents list")
    ids = set()
    names = set()
    for parent in parents:
        if not isinstance(parent.get("id"), str) or not parent["id"] or parent["id"] in ids:
            raise ValueError("Parent rule ids must be unique non-empty strings")
        if not isinstance(parent.get("name"), str) or not parent["name"] or parent["name"] in names:
            raise ValueError("Parent rule names must be unique non-empty strings")
        if type(parent.get("priority")) is not int:
            raise ValueError("Parent priority must be an integer")
        ids.add(parent["id"])
        names.add(parent["name"])
        _validate_requirements(parent.get("requires"), f"parent:{parent['id']}")
        variants = parent.get("variants", [])
        if not isinstance(variants, list):
            raise ValueError("Parent variants must be a list")
        labels = set()
        for variant in variants:
            label = variant.get("label")
            if not isinstance(label, str) or not label or label in labels:
                raise ValueError(f"Variant labels must be unique within parent {parent['id']}")
            labels.add(label)
            _validate_requirements(variant.get("requires"), f"variant:{parent['id']}:{label}")


def _validate_requirements(requirements, where):
    if not isinstance(requirements, list) or not requirements:
        raise ValueError(f"{where} requires at least one card requirement")
    for requirement in requirements:
        if set(requirement) != {"card", "min_count"}:
            raise ValueError(f"{where} requirement must contain card and min_count only")
        card = requirement["card"]
        minimum = requirement["min_count"]
        if card not in IDENTITIES["cards"]:
            raise ValueError(f"{where} references unknown logical card: {card}")
        if type(minimum) is not int or minimum <= 0:
            raise ValueError(f"{where} min_count must be a positive integer")


_validate_rules()


def _logical_counts(deck):
    counts = Counter()
    for card in deck["cards"]:
        if section_type(card.get("section")) != "pokemon":
            continue
        card_id = card.get("card_id")
        display_name = card.get("name")
        for logical_name, aliases_by_id in IDENTITIES["cards"].items():
            if display_name in aliases_by_id.get(card_id, []):
                counts[logical_name] += card["count"]
    return counts


def _matches(counts, requirements):
    return all(counts[requirement["card"]] >= requirement["min_count"]
               for requirement in requirements)


def _is_auto_classifier_version(value):
    return isinstance(value, str) and value.startswith(LEGACY_AUTO_VERSION_PREFIXES)


def classify(code, deck, timestamp):
    digest = hashlib.sha256(json.dumps(
        deck, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    record = dict(
        deck_code=code,
        parent_archetype=None,
        variant_tags=[],
        classification_status="unknown",
        classified_at=timestamp,
        classifier_version=VERSION,
        evidence=None,
    )
    evidence = {
        "deck_sha256": digest,
        "rules_version": VERSION,
        "market": RULES["market"],
        "card_pool_basis": RULES["card_pool_basis"],
        "external_rules_imported": RULES["external_taxonomy_reference"]["rules_imported"],
        "counts": {},
        "matched_parent_rules": [],
        "suppressed_parent_rules": [],
        "matched_variants": [],
        "reason": None,
    }
    if deck.get("deck_code") != code or any(
            issue["severity"] == "ERROR" for issue in validate_deck(deck)):
        evidence["reason"] = "invalid_deck"
    else:
        counts = _logical_counts(deck)
        evidence["counts"] = dict(sorted(counts.items()))
        matched = [parent for parent in RULES["parents"]
                   if _matches(counts, parent["requires"])]
        evidence["matched_parent_rules"] = [
            {"id": parent["id"], "name": parent["name"], "priority": parent["priority"]}
            for parent in matched
        ]
        if not matched:
            evidence["reason"] = "outside_supported_parent_rules"
        else:
            highest = max(parent["priority"] for parent in matched)
            top = [parent for parent in matched if parent["priority"] == highest]
            evidence["suppressed_parent_rules"] = [
                {"id": parent["id"], "name": parent["name"], "priority": parent["priority"]}
                for parent in matched if parent["priority"] < highest
            ]
            if len(top) != 1:
                evidence["reason"] = "parent_collision_equal_priority"
            else:
                selected = top[0]
                record["parent_archetype"] = selected["name"]
                variants = [
                    variant["label"] for variant in selected.get("variants", [])
                    if _matches(counts, variant["requires"])
                ]
                record["variant_tags"] = variants
                record["classification_status"] = "classified"
                evidence["matched_variants"] = variants
                evidence["reason"] = (
                    "unique_highest_priority_rule"
                    if len(matched) > 1 else "unique_parent_rule"
                )
    record["evidence"] = json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return record


def classify_range(city_path, decks_path, output, start, end, *, timestamp=None):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    if start > end:
        raise ValueError("Require start <= end")
    city, decks = read_json(city_path), read_json(decks_path)["decks"]
    codes = set()
    for event in city["events"]:
        if event.get("date") and start <= date.fromisoformat(event["date"]) <= end:
            codes.update(
                placement["deck_code"] for placement in event.get("placements", [])
                if placement.get("deck_code")
            )
    missing = codes - decks.keys()
    if missing:
        raise ValueError(f"Missing source decks: {len(missing)}")
    output = Path(output)
    if "weekly_snapshots" in output.resolve().parts:
        raise ValueError("Snapshot inputs are immutable; use a separate output")
    document = read_state(output) if output.exists() else dict(
        schema_version="1.2.1", kind="deck_classifications", records=[])
    if document["kind"] != "deck_classifications":
        raise ValueError("Output must be deck_classifications")
    records = {record["deck_code"]: record for record in document["records"]}
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    changed = 0
    for code in sorted(codes):
        new = classify(code, decks[code], timestamp)
        old = records.get(code)
        if old:
            old_version = old.get("classifier_version")
            if old_version != VERSION and not _is_auto_classifier_version(old_version):
                raise ValueError(
                    f"Refuse to overwrite another classifier/manual record: {code}")
        if old and all(old.get(key) == value for key, value in new.items()
                       if key != "classified_at"):
            continue
        records[code] = new
        changed += 1
    if changed:
        document["records"] = [records[key] for key in sorted(records)]
        write_state(output, document)
    return {
        "selected_decks": len(codes),
        "changed": changed,
        "classifier_version": VERSION,
        "statuses": dict(Counter(
            records[code]["classification_status"] for code in codes)),
    }
