"""Temporary W04 classification probe.

Prints unresolved W04 Top8 deck Pokemon lines from fixed repository data.
No state writes.
"""
import json
import unittest

from analysis.cards import section_type
from analysis.state import ROOT, read_state
from analysis.weekly import _classification_resolver


class W04ClassificationProbe(unittest.TestCase):
    def test_print_unresolved_w04_top8(self):
        city = json.loads((ROOT / "data/city_db.json").read_text(encoding="utf-8"))
        decks = json.loads((ROOT / "data/city_decks.json").read_text(encoding="utf-8"))["decks"]
        index = json.loads((ROOT / "data/index.json").read_text(encoding="utf-8"))
        sidecar = read_state(ROOT / "data/analysis/deck_classifications.json")
        resolve = _classification_resolver(sidecar, decks, index["source_updated_at"])

        rows = []
        for event in city["events"]:
            if not ("2026-10-12" <= event.get("date", "") <= "2026-10-18"):
                continue
            for placement in event.get("placements", []):
                if 1 <= placement.get("rank", 100) <= 8 and placement.get("deck_code"):
                    rows.append((event, placement))

        unresolved = []
        for event, placement in rows:
            code = placement["deck_code"]
            result = resolve(code)
            if result["parent"]:
                continue
            deck = decks.get(code)
            if not deck:
                unresolved.append({
                    "deck_code": code,
                    "event_id": event.get("event_id"),
                    "date": event.get("date"),
                    "rank": placement.get("rank"),
                    "category": event.get("category"),
                    "missing_deck": True,
                })
                continue
            pokemon = [
                {"name": c.get("name"), "card_id": c.get("card_id"), "count": c.get("count")}
                for c in deck.get("cards", [])
                if section_type(c.get("section")) == "pokemon"
            ]
            unresolved.append({
                "deck_code": code,
                "event_id": event.get("event_id"),
                "date": event.get("date"),
                "rank": placement.get("rank"),
                "category": event.get("category"),
                "pokemon": pokemon,
            })

        print("W04_CLASSIFICATION_PROBE " + json.dumps({
            "source_updated_at": index.get("source_updated_at"),
            "top8_rows": len(rows),
            "unresolved_count": len(unresolved),
            "unresolved": unresolved,
        }, ensure_ascii=False, separators=(",", ":")))

        self.assertGreater(len(rows), 0)


if __name__ == "__main__":
    unittest.main()
