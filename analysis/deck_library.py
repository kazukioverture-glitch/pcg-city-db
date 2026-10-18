"""Indexed, read-only City League Top16 deck archive; raw DB remains canonical.

The archive is an independently generated cache. Existing FINAL snapshots,
city_db.json, city_decks.json and the collector are never modified here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path

FIRST_MONDAY = date(2026, 9, 21)


def _week_id(day: str) -> str:
    delta = (date.fromisoformat(day) - FIRST_MONDAY).days
    if delta < 0:
        raise ValueError("Event predates 2027-S1 Monday calendar: " + day)
    return "W%02d" % (delta // 7 + 1)


def _valid_60(deck: dict) -> bool:
    cards = deck.get("cards")
    return (
        deck.get("total_cards") == 60
        and isinstance(cards, list) and bool(cards)
        and all(isinstance(c, dict) and type(c.get("count")) is int
                and c["count"] > 0 for c in cards)
        and sum(c["count"] for c in cards) == 60
    )


def _identity_count(deck: dict, identities: dict, logical_name: str) -> int:
    """Match exact approved (card_id, display-name) pairs, not substrings."""
    known = identities["cards"][logical_name]
    return sum(
        card["count"]
        for card in deck["cards"]
        if str(card.get("card_id")) in known
        and card.get("name") in known[str(card.get("card_id"))]
        and str(card.get("section", "")).startswith("ポケモン")
    )


def build_library(city: dict, decks_document: dict, source_index: dict,
                  identities: dict) -> dict[str, dict]:
    """Pure transformation, returning repository-relative output files."""
    if city.get("season") != "2027-S1" or source_index.get("season") != "2027-S1":
        raise ValueError("Only verified 2027-S1 data is supported")
    decks = decks_document.get("decks", {})
    if not isinstance(decks, dict) or not isinstance(city.get("events"), list):
        raise ValueError("Invalid source structure")

    top16 = defaultdict(list)
    dhelmise = defaultdict(list)
    coverage = defaultdict(lambda: {
        "event_ids": set(), "confirmed_event_ids": set(),
        "row_count": 0, "valid_60_rows": 0, "missing_deck_rows": 0,
        "dhelmise_rows": 0, "banette_dhelmise_pattern_rows": 0,
    })
    for event in city["events"]:
        placements = event.get("placements", [])
        if not isinstance(placements, list):
            raise ValueError("Invalid event placements")
        if not placements:
            continue
        week = _week_id(event["date"])
        event_id = str(event["event_id"])
        captured_extra = sum(1 for p in placements if 8 < int(p["rank"]) <= 16)
        observed_extra = event.get("top16_observed_rows")
        confirmed = (
            bool(event.get("top16_checked_at"))
            and type(observed_extra) is int
            and observed_extra >= 8
            and captured_extra >= observed_extra
            and sum(1 for p in placements if 1 <= int(p["rank"]) <= 8) >= 8
        )
        coverage[week]["event_ids"].add(event_id)
        if confirmed:
            coverage[week]["confirmed_event_ids"].add(event_id)
        for i, placement in enumerate(placements):
            rank = int(placement["rank"])
            if not 1 <= rank <= 16:
                continue
            code = placement.get("deck_code") or None
            deck = decks.get(code) if code else None
            if code and deck is None:
                raise ValueError("Dangling deck code: " + code)
            if deck is not None and not _valid_60(deck):
                raise ValueError("Invalid 60-card deck: " + code)
            da_count = _identity_count(deck, identities, "ダダリン") if deck else 0
            ba_count = _identity_count(deck, identities, "ジュペッタ") if deck else 0
            pattern = da_count >= 3 and ba_count >= 2
            row = {
                "row_id": f"{event_id}:{i}",
                "event_id": event_id,
                "date": event["date"],
                "category": event.get("category"),
                "venue_prefecture": event.get("venue_prefecture"),
                "rank": rank,
                "deck_code": code,
                "deck_url": placement.get("deck_url") or (deck.get("deck_url") if deck else None),
                "valid_60": deck is not None,
                "top16_event_coverage": "confirmed_observation" if confirmed else "unverified_or_partial",
                "dhelmise_count": da_count,
                "banette_count": ba_count,
                "banette_dhelmise_pattern": pattern,
            }
            top16[week].append(row)
            stats = coverage[week]
            stats["row_count"] += 1
            stats["valid_60_rows"] += int(deck is not None)
            stats["missing_deck_rows"] += int(deck is None)
            stats["dhelmise_rows"] += int(da_count > 0)
            stats["banette_dhelmise_pattern_rows"] += int(pattern)
            if da_count:
                # One record per placement: do not silently deduplicate players
                # using the same code; preserve raw card IDs/printings verbatim.
                dhelmise[week].append({
                    **row,
                    "total_cards": deck["total_cards"],
                    "cards": deck["cards"],
                })

    output = {}
    weeks = []
    for week in sorted(top16):
        rows = sorted(top16[week], key=lambda r: (
            r["date"], r["event_id"], r["rank"], r["row_id"]))
        targets = sorted(dhelmise[week], key=lambda r: (
            r["date"], r["event_id"], r["rank"], r["row_id"]))
        index_path = f"top16/{week}.json"
        target_path = f"dhelmise/{week}.json"
        output[index_path] = {
            "schema_version": "city-top16-index-v1", "week_id": week,
            "source_city_sha256": source_index["city_sha256"],
            "source_decks_sha256": source_index["decks_sha256"],
            "rows": rows,
        }
        output[target_path] = {
            "schema_version": "city-dhelmise-lists-v1", "week_id": week,
            "selection": "known-identity card ダダリン >= 1; all categories; rank 1-16",
            "identity_dictionary_version": identities.get("version"),
            "source_city_sha256": source_index["city_sha256"],
            "source_decks_sha256": source_index["decks_sha256"],
            "rows": targets,
        }
        stat = coverage[week]
        weeks.append({
            "week_id": week,
            "top16_index_path": "data/deck_library/" + index_path,
            "dhelmise_lists_path": "data/deck_library/" + target_path,
            "observed_event_count": len(stat["event_ids"]),
            "confirmed_top16_observation_event_count": len(stat["confirmed_event_ids"]),
            **{k: stat[k] for k in (
                "row_count", "valid_60_rows", "missing_deck_rows",
                "dhelmise_rows", "banette_dhelmise_pattern_rows")},
        })
    output["manifest.json"] = {
        "schema_version": "city-deck-library-v1",
        "season": city["season"],
        "calendar_id": "2027-s1-mon-sun-v1",
        "source_updated_at": source_index.get("source_updated_at"),
        "source_city_sha256": source_index["city_sha256"],
        "source_decks_sha256": source_index["decks_sha256"],
        "identity_dictionary_version": identities.get("version"),
        "scope_note": (
            "Top16 means published ranks 1-16; unchecked/partial events are "
            "included as observed rows but must not be treated as complete coverage. "
            "Missing deck codes are retained in index, never imputed. "
            "Raw complete 60-card decks remain in data/city_decks.json."
        ),
        "weeks": weeks,
    }
    return output


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
    if path.exists() and path.read_bytes() == payload:
        return
    fd, tmp = tempfile.mkstemp(prefix=".deck-library-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load_verified(path: Path, expected_hash: str) -> dict:
    content = path.read_bytes()
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_hash:
        raise ValueError(f"Source hash mismatch: {path}")
    return json.loads(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="data/city_db.json")
    parser.add_argument("--decks", default="data/city_decks.json")
    parser.add_argument("--index", default="data/index.json")
    parser.add_argument("--identities", default="analysis/classification_cards.json")
    parser.add_argument("--output", default="data/deck_library")
    parser.add_argument("--query", choices=("top16", "dhelmise"))
    parser.add_argument("--week", help="W01, W02, etc. Required with --query.")
    parser.add_argument("--include-cards", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output)
    if args.query:
        if not args.week:
            parser.error("--query requires --week")
        path = output_dir / args.query / (args.week + ".json")
        document = json.loads(path.read_text(encoding="utf-8"))
        rows = document["rows"]
        if args.query == "dhelmise" and not args.include_cards:
            rows = [{k: v for k, v in row.items() if k != "cards"} for row in rows]
        print(json.dumps({"week_id": args.week, "rows": rows}, ensure_ascii=False, indent=2))
        return 0

    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    if index.get("status") != "ok":
        raise ValueError("Refuse non-ok source index")
    city = _load_verified(Path(args.city), index["city_sha256"])
    decks = _load_verified(Path(args.decks), index["decks_sha256"])
    if len(decks.get("decks", {})) != index.get("unique_deck_count"):
        raise ValueError("Deck count disagrees with index")
    identities = json.loads(Path(args.identities).read_text(encoding="utf-8"))
    if not all(name in identities.get("cards", {}) for name in ("ダダリン", "ジュペッタ")):
        raise ValueError("Missing approved ダダリン/ジュペッタ identities")
    files = build_library(city, decks, index, identities)
    for relative, content in files.items():
        if relative != "manifest.json":
            _atomic_json(output_dir / relative, content)
    _atomic_json(output_dir / "manifest.json", files["manifest.json"])
    manifest = files["manifest.json"]
    print(json.dumps({
        "weeks": len(manifest["weeks"]),
        "top16_rows": sum(x["row_count"] for x in manifest["weeks"]),
        "dhelmise_rows": sum(x["dhelmise_rows"] for x in manifest["weeks"]),
        "banette_dhelmise_pattern_rows": sum(
            x["banette_dhelmise_pattern_rows"] for x in manifest["weeks"]),
        "source_sha256": manifest["source_city_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
