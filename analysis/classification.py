"""Conservative, offline classification sidecar; no collector or snapshot writes."""

import hashlib
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from .cards import section_type, validate_deck
from .state import ROOT, read_json, read_state, write_state

IDENTITIES = read_json(Path(__file__).with_name("classification_cards.json"))
VERSION = IDENTITIES["version"]
PARENTS = {"ドラパルトex": 2, "メガガルーラex": 3, "オーガポン みどりのめんex": 3}
ATTACKERS = {
    "メガガルーラex": ("メガレックウザex", "ヤドキング", "タケルライコex", "ヒビキのホウオウex"),
    "オーガポン みどりのめんex": ("カミツオロチex", "オリーヴァex", "メガフシギバナex", "タケルライコex"),
}


def classify(code, deck, timestamp):
    digest = hashlib.sha256(json.dumps(deck, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    record = dict(deck_code=code, parent_archetype=None, variant_tags=[],
                  classification_status="unknown", classified_at=timestamp,
                  classifier_version=VERSION, evidence=None)
    evidence = dict(deck_sha256=digest, counts={}, reason=None)
    if deck.get("deck_code") != code or any(i["severity"] == "ERROR" for i in validate_deck(deck)):
        evidence["reason"] = "invalid_deck"
    else:
        counts = Counter()
        for card in deck["cards"]:
            if section_type(card.get("section")) != "pokemon":
                continue
            for name, aliases in IDENTITIES["cards"].items():
                if card.get("name") in aliases.get(card.get("card_id"), []):
                    counts[name] += card["count"]
        evidence["counts"] = dict(sorted(counts.items()))
        parents = [n for n, minimum in PARENTS.items() if counts[n] >= minimum]
        if "メガガルーラex" in parents and "オーガポン みどりのめんex" in parents:
            parents.remove("オーガポン みどりのめんex")
        if len(parents) == 1:
            parent = record["parent_archetype"] = parents[0]
            if parent == "ドラパルトex":
                record["variant_tags"] = [n + "型" for n in ("ノココッチ", "ヨノワール") if counts[n]]
                record["classification_status"] = "classified"
                evidence["reason"] = "parent_threshold; support_tags_not_main_attackers"
            else:
                # Candidate roles are explicit. Counts alone cannot settle a mixed
                # attacker list, so never select the largest count or a support ex.
                candidates = [n for n in ATTACKERS[parent] if counts[n]]
                # Ho-Oh is an energy engine in the supported Mega Rayquaza
                # structure; Talonflame is not an attacker-name classifier.
                if "メガレックウザex" in candidates and "ヒビキのホウオウex" in candidates:
                    candidates.remove("ヒビキのホウオウex")
                if len(candidates) == 1 and counts[candidates[0]] >= 2:
                    record["variant_tags"] = [candidates[0] + "型"]
                    record["classification_status"] = "classified"
                    evidence["reason"] = "single_supported_attacker_at_least_two; structural_rule_not_play_observation"
                else:
                    record["classification_status"] = "partial"
                    evidence["reason"] = "main_attacker_unresolved"
        else:
            evidence["reason"] = "parent_collision" if parents else "outside_supported_parents"
    record["evidence"] = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return record


def classify_range(city_path, decks_path, output, start, end, *, timestamp=None):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    if start > end:
        raise ValueError("Require start <= end")
    city, decks = read_json(city_path), read_json(decks_path)["decks"]
    codes = set()
    for event in city["events"]:
        if event.get("date") and start <= date.fromisoformat(event["date"]) <= end:
            codes.update(p["deck_code"] for p in event.get("placements", []) if p.get("deck_code"))
    # A missing source is a failed batch, never a reason to erase previous results.
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
    records = {r["deck_code"]: r for r in document["records"]}
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    changed = 0
    for code in sorted(codes):
        new = classify(code, decks[code], timestamp)
        old = records.get(code)
        if old and old.get("classifier_version") != VERSION:
            raise ValueError(f"Refuse to overwrite another classifier/manual record: {code}")
        if old and all(old[k] == v for k, v in new.items() if k != "classified_at"):
            continue
        records[code] = new
        changed += 1
    if changed:
        document["records"] = [records[k] for k in sorted(records)]
        write_state(output, document)
    return dict(selected_decks=len(codes), changed=changed,
                statuses=dict(Counter(records[c]["classification_status"] for c in codes)))
