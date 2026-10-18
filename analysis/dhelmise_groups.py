"""Rebuild coarse Dhelmise deck group labels from the stored full 60-card lists.

Safe derived data only: never edit city_db, city_decks, historical snapshots,
classifier sidecars, or prediction logs.
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path


GROUPS = ("ヤバソチャ採用型", "ヤバソチャ・フーディン非採用型", "フーディン併用型")
ENGINES = ("ノココッチex併用型", "ノココッチ型（exなし）", "ノココッチ非採用")
PRINTING_SUFFIX = re.compile(r"[A-Z][A-Za-z0-9-]*[0-9]+/[A-Za-z0-9-]+")


def pokemon_count(cards, name):
    total = 0
    for card in cards:
        if not str(card.get("section", "")).startswith("ポケモン ("):
            continue
        actual = card.get("name")
        if not isinstance(actual, str) or not actual.startswith(name):
            continue
        suffix = actual[len(name):]
        if not suffix or PRINTING_SUFFIX.fullmatch(suffix):
            total += card["count"]
    return total


def group_labels(cards):
    if pokemon_count(cards, "ヤバソチャ"):
        group = GROUPS[0]
    elif pokemon_count(cards, "フーディン"):
        group = GROUPS[2]
    else:
        group = GROUPS[1]
    if pokemon_count(cards, "ノココッチex"):
        engine = ENGINES[0]
    elif pokemon_count(cards, "ノココッチ"):
        engine = ENGINES[1]
    else:
        engine = ENGINES[2]
    return group, engine


def counts(rows, key, labels):
    found = Counter(row[key] for row in rows)
    return {label: found[label] for label in labels}


def build(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    all_rows, weeks = [], []
    for entry in manifest["weeks"]:
        week = entry["week_id"]
        path = directory / "dhelmise" / (week + ".json")
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["rows"]
        for row in rows:
            cards = row["cards"]
            if row["total_cards"] != 60 or sum(c["count"] for c in cards) != 60:
                raise ValueError("Invalid 60 card list: " + str(row["deck_code"]))
            group, engine = group_labels(cards)
            row["archetype_group"] = group
            row["dunsparce_engine_group"] = engine
        if len(rows) != entry["dhelmise_rows"]:
            raise ValueError("Manifest/library row mismatch: " + week)
        all_rows.extend(rows)
        weeks.append({
            "week_id": week,
            "total": len(rows),
            "archetype_group_counts": counts(rows, "archetype_group", GROUPS),
            "dunsparce_engine_group_counts": counts(rows, "dunsparce_engine_group", ENGINES),
        })
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": "city-dhelmise-groups-v1",
        "season": manifest["season"],
        "source_updated_at": manifest["source_updated_at"],
        "source_city_sha256": manifest["source_city_sha256"],
        "source_decks_sha256": manifest["source_decks_sha256"],
        "unit": "result_placements_not_unique_deck_codes",
        "total": len(all_rows),
        "unique_deck_codes": len({r["deck_code"] for r in all_rows}),
        "archetype_group_counts": counts(all_rows, "archetype_group", GROUPS),
        "dunsparce_engine_group_counts": counts(all_rows, "dunsparce_engine_group", ENGINES),
        "weeks": weeks,
        "caveat": "Structural Pokemon card groupings only, not win rates or complete Top16 coverage.",
    }
    target = directory / "dhelmise_groups.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="data/deck_library")
    args = parser.parse_args()
    report = build(args.library)
    print(json.dumps({
        "total": report["total"],
        "groups": report["archetype_group_counts"],
        "engines": report["dunsparce_engine_group_counts"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
