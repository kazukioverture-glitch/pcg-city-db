"""Produce the modal 52-card reference and its strict 90%-agreement subset.

A consensus reference is not a playable optimum and not necessarily one deck
actually used. Read-only with respect to the collector DB and FINAL snapshots.
"""
import argparse
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

PROFILES = {
    "ヤバソチャ少数・ノココッチex併用",
    "ヤバソチャ少数・通常ノココッチ",
}
PRINT = re.compile(r"[A-Z][A-Za-z0-9-]*[0-9]+/[A-Za-z0-9-]+$")
TYPES = ("ポケモン", "グッズ", "ポケモンのどうぐ",
         "サポート", "スタジアム", "エネルギー")


def logical_name(card):
    return PRINT.sub("", card["name"])


def card_group(card):
    label = card["section"].split(" (", 1)[0]
    if label not in TYPES:
        raise ValueError("Unexpected card category " + str(label))
    return label


def deck_counts(cards):
    names, types = Counter(), defaultdict(set)
    for card in cards:
        name = logical_name(card)
        names[name] += card["count"]
        types[name].add(card_group(card))
    if sum(names.values()) != 60:
        raise ValueError("Deck is not exactly 60 cards")
    return names, types


def rounded(v):
    return round(v, 6)


def consensus(rows, source):
    if not rows:
        raise ValueError("No Dhelmise common-engine lists")
    for row in rows:
        if row["average_profile"] not in PROFILES:
            raise ValueError("Unexpected deck profile")
    decks = []
    type_observations = defaultdict(set)
    for row in rows:
        counts, types = deck_counts(row["cards"])
        if row["total_cards"] != 60:
            raise ValueError("Incorrect total_cards")
        decks.append((row, counts))
        for name, groups in types.items():
            type_observations[name].update(groups)
    n = len(decks)
    byname = sorted({name for _, deck in decks for name in deck})
    all_cards = []
    for name in byname:
        votes = Counter(deck.get(name, 0) for _, deck in decks)
        adopter_count = n - votes.get(0, 0)
        if adopter_count < n * .9:
            continue
        nonzero = [(copies, amount) for copies, amount in votes.items() if copies > 0]
        modal_copies, modal_count = max(nonzero, key=lambda pair: (pair[1], pair[0]))
        all_cards.append({
            "card": name,
            "category": sorted(type_observations[name])[0],
            "mode_copies": modal_copies,
            "adopters": adopter_count,
            "adoption_rate": rounded(adopter_count / n),
            "matching_mode": modal_count,
            "modal_agreement": rounded(modal_count / n),
            "average_copies": rounded(sum(deck.get(name, 0) for _, deck in decks) / n),
            "count_distribution": {
                str(i): votes.get(i, 0) for i in range(5)
            },
        })
    all_cards.sort(key=lambda x: (
        TYPES.index(x["category"]), -x["mode_copies"], x["card"]))
    strict = [c for c in all_cards if c["modal_agreement"] >= .9]
    reference = {c["card"]: c["mode_copies"] for c in all_cards}
    positions = sum(reference.values())
    if positions >= 60:
        raise ValueError("No variable slots remain")
    mismatch = []
    for row, deck in decks:
        absent_slots = sum(max(0, num - deck.get(name, 0))
                           for name, num in reference.items())
        exact_cards = sum(deck.get(name, 0) == num
                          for name, num in reference.items())
        mismatch.append({
            "top4": row["rank"] <= 3,
            "complete_ref": absent_slots == 0,
            "shortfall": absent_slots,
            "exact_card_lines": exact_cards,
        })
    histogram = Counter(entry["shortfall"] for entry in mismatch)
    summary = {
        "schema_version": "dhelmise-consensus-baseline-v1",
        "scope": "Dhelmise light-Yabasoucha + standard Dudunsparce, regardless of ex",
        "source_city_sha256": source["source_city_sha256"],
        "source_decks_sha256": source["source_decks_sha256"],
        "source_updated_at": source["source_updated_at"],
        "calendar_id": source["calendar_id"],
        "unit": "recorded_top16_placements; repeated deck codes count as repeated results",
        "population": n,
        "unique_deck_codes": len({row["deck_code"] for row in rows}),
        "adoption_threshold": 0.9,
        "strict_modal_agreement_threshold": 0.9,
        "reference": {
            "card_count": len(all_cards),
            "copy_count": positions,
            "nominal_remainder_to_60": 60 - positions,
            "cards": all_cards,
        },
        "strict_modal_core": {
            "card_count": len(strict),
            "copy_count": sum(c["mode_copies"] for c in strict),
            "cards": [c["card"] for c in strict],
        },
        "reference_coverage": {
            "decks_with_all_reference_copies": sum(x["complete_ref"] for x in mismatch),
            "average_shortfall": rounded(
                sum(x["shortfall"] for x in mismatch) / n),
            "shortfall_histogram": {
                str(i): histogram.get(i, 0)
                for i in range(max(histogram) + 1)
            },
        },
        "interpretation": (
            "The 52-card modal template is a comparator, not a 52-card "
            "subset actually present in most decklists. Card copies differ "
            "by a few slots. Do not infer card efficacy without controls."
        ),
    }
    return summary


def build(root):
    root = Path(root)
    source = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for week in source["weeks"]:
        path = root / week["dhelmise_lists_path"].replace("data/deck_library/", "")
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc["source_decks_sha256"] != source["source_decks_sha256"]:
            raise ValueError("Stale deck library " + str(path))
        rows.extend(row for row in doc["rows"] if row["average_profile"] in PROFILES)
    report = consensus(rows, source)
    destination = root / "dhelmise_consensus.json"
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if not destination.exists() or destination.read_text(encoding="utf-8") != content:
        fd, temporary = tempfile.mkstemp(prefix=".dhelmise-consensus-", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="data/deck_library")
    args = parser.parse_args()
    report = build(args.library)
    print(json.dumps({
        "population": report["population"],
        "reference": report["reference"]["copy_count"],
        "strict": report["strict_modal_core"]["copy_count"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
