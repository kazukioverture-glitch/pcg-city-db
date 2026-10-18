"""Tag the structural profiles that materially affect an averaged 60-card deck.

Derived annotations only. Never alters the original tournament/deck collection,
historic FINAL snapshots, or forecast logs.
"""
import argparse
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

from analysis.dhelmise_groups import pokemon_count

PROFILES = (
    "ミカルゲ採用・ヤバソチャ厚め",
    "ミカルゲ非採用・ヤバソチャ厚め",
    "ヤバソチャ少数・ノココッチex併用",
    "ヤバソチャ少数・通常ノココッチ",
    "ヤバソチャ少数・ノココッチなし",
    "フーディン併用",
    "ヤバソチャ・フーディン非採用",
    "ミカルゲ採用・例外構築",
)
# A known data-quality caveat in the source: some item/tool names have
# erroneously been placed inside a Pokemon section by the source parser.
NON_POKEMON_NAMES = {
    "ふうせん", "ラッキーメット", "ブレイブバングル",
    "ハンディサーキュレーター", "マキシマムベルト(ACE SPEC)",
    "ヒーローマント(ACE SPEC)", "リーリエのしんじゅ",
}
CARD_SUFFIX = re.compile(r"[A-Z][A-Za-z0-9-]*[0-9]+/[A-Za-z0-9-]+")


def norm(name):
    return CARD_SUFFIX.sub("", str(name))


def profile(cards):
    spiritomb = pokemon_count(cards, "ミカルゲ")
    yabasoucha = pokemon_count(cards, "ヤバソチャ")
    chadesu = pokemon_count(cards, "チャデス")
    alakazam = pokemon_count(cards, "フーディン")
    dudunsparce = pokemon_count(cards, "ノココッチ")
    dudunsparce_ex = pokemon_count(cards, "ノココッチex")
    if spiritomb:
        category = PROFILES[0] if yabasoucha >= 3 else PROFILES[7]
    elif yabasoucha >= 3:
        category = PROFILES[1]
    elif yabasoucha:
        category = PROFILES[2] if dudunsparce_ex else (
            PROFILES[3] if dudunsparce else PROFILES[4])
    elif alakazam:
        category = PROFILES[5]
    else:
        category = PROFILES[6]
    mislabeled = sorted({
        norm(card.get("name"))
        for card in cards
        if str(card.get("section", "")).startswith("ポケモン")
        and norm(card.get("name")) in NON_POKEMON_NAMES
    })
    return {
        "average_profile": category,
        "spiritomb_count": spiritomb,
        "chadesu_count": chadesu,
        "yabasoucha_count": yabasoucha,
        "dudunsparce_count": dudunsparce,
        "dudunsparce_ex_count": dudunsparce_ex,
        "alakazam_count": alakazam,
        "section_mismatch_tool_names": mislabeled,
    }


def _atomic_write(path, document):
    path = Path(path)
    content = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists() and path.read_bytes() == content:
        return
    handle, temporary = tempfile.mkstemp(prefix=".dhelmise-profile-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as fd:
            fd.write(content)
            fd.flush()
            os.fsync(fd.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _counts(rows):
    count = Counter(r["average_profile"] for r in rows)
    return {name: count.get(name, 0) for name in PROFILES}


def build(root):
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    pending = []
    all_rows = []
    week_reports = []
    for item in manifest["weeks"]:
        path = root / item["dhelmise_lists_path"].replace("data/deck_library/", "")
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = doc["rows"]
        if len(rows) != item["dhelmise_rows"]:
            raise ValueError("Row count disagrees with manifest: " + str(path))
        if doc["source_decks_sha256"] != manifest["source_decks_sha256"]:
            raise ValueError("Stale source decks hash: " + str(path))
        for r in rows:
            cards = r["cards"]
            if r["total_cards"] != 60 or sum(c["count"] for c in cards) != 60:
                raise ValueError("Invalid 60-card deck: " + r["deck_code"])
            r.update(profile(cards))
        all_rows.extend(rows)
        week_reports.append({
            "week_id": item["week_id"],
            "total": len(rows),
            "profiles": _counts(rows),
            "spiritomb_adoption": sum(r["spiritomb_count"] > 0 for r in rows),
        })
        pending.append((path, doc))
    report = {
        "schema_version": "dhelmise-average-profiles-v1",
        "source_city_sha256": manifest["source_city_sha256"],
        "source_decks_sha256": manifest["source_decks_sha256"],
        "calendar_id": manifest["calendar_id"],
        "source_updated_at": manifest["source_updated_at"],
        "unit": "recorded_top16_placements; may reuse same deck code",
        "total": len(all_rows),
        "unique_deck_codes": len({r["deck_code"] for r in all_rows}),
        "profiles": _counts(all_rows),
        "spiritomb_adoption": sum(r["spiritomb_count"] > 0 for r in all_rows),
        "suspected_wrong_card_sections": sum(
            bool(r["section_mismatch_tool_names"]) for r in all_rows),
        "weeks": week_reports,
        "note": (
            "These are mutually exclusive deck-structure labels, not deck win rates. "
            "The core data may have item/tool cards mislabeled as Pokemon by the source. "
            "Normalize card identities and sections before averaging card totals."
        ),
    }
    if sum(report["profiles"].values()) != len(all_rows):
        raise ValueError("Variant profiles are not exhaustive")
    for path, doc in pending:
        _atomic_write(path, doc)
    _atomic_write(root / "dhelmise_profiles.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", default="data/deck_library")
    args = parser.parse_args()
    report = build(args.library)
    print(json.dumps({
        "total": report["total"], "profiles": report["profiles"],
        "spiritomb": report["spiritomb_adoption"],
        "suspected_section_errors": report["suspected_wrong_card_sections"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
