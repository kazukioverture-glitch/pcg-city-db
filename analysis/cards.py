"""Explicit card identity/type checks, separate from the existing 60-card gate."""

import re

SECTION_TYPES = {
    "ポケモン": "pokemon", "ポケモンのどうぐ": "tool", "グッズ": "item",
    "サポート": "supporter", "スタジアム": "stadium", "エネルギー": "energy",
}


def section_type(section):
    if not isinstance(section, str):
        return None
    match = re.fullmatch(r"\s*(ポケモンのどうぐ|ポケモン|グッズ|サポート|スタジアム|エネルギー)\s*(?:\(\d+\))?\s*", section)
    return SECTION_TYPES[match.group(1)] if match else None


def validate_deck(deck, master=None):
    """Return structured ERROR/WARN issues; absent master never implies validation.

    Master names/aliases must explicitly include collector display strings (which
    may contain set/number suffixes). Never infer identity by stripping substrings.
    """
    issues = []

    def issue(severity, code, index=None):
        issues.append({"severity": severity, "code": code, "card_index": index})

    if deck.get("total_cards") != 60:
        issue("ERROR", "INVALID_TOTAL_CARDS")
    cards = deck.get("cards")
    if not isinstance(cards, list) or not cards:
        issue("ERROR", "MISSING_CARDS")
        return issues
    counts_valid = all(isinstance(c, dict) and type(c.get("count")) is int
                       and c["count"] > 0 for c in cards)
    if not counts_valid:
        issue("ERROR", "INVALID_CARD_COUNT")
    elif sum(c["count"] for c in cards) != 60:
        issue("ERROR", "CARD_COUNT_SUM_NOT_60")
    if master is None:
        issue("WARN", "MASTER_UNAVAILABLE")
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            issue("ERROR", "INVALID_CARD", index)
            continue
        card_id, name = card.get("card_id"), card.get("name")
        if not isinstance(card_id, str) or not card_id.strip():
            issue("WARN", "CARD_ID_MISSING", index)
        if not isinstance(name, str) or not name.strip():
            issue("WARN", "CARD_NAME_MISSING", index)
        kind = section_type(card.get("section"))
        if kind is None:
            issue("WARN", "CARD_TYPE_UNKNOWN", index)
        if master is None:
            continue
        entry = master.get(card_id) if isinstance(card_id, str) else None
        if entry is None:
            issue("WARN", "CARD_ID_NOT_IN_MASTER", index)
            continue
        if name not in [entry["name"], *entry.get("aliases", [])]:
            issue("ERROR", "CARD_NAME_MISMATCH", index)
        if kind is not None and kind != entry["card_type"]:
            issue("ERROR", "CARD_TYPE_MISMATCH", index)
    return issues
