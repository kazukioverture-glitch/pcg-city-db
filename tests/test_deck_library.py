"""Offline regression checks for Top16 deck library and exact card matching."""
import unittest

from analysis.deck_library import _identity_count, _valid_60, build_library


ALIASES = {
    "version": "test-ids",
    "cards": {
        "ダダリン": {"50308": ["ダダリン", "ダダリンM5088/081"]},
        "ジュペッタ": {"50251": ["ジュペッタ"]},
    },
}
INDEX = {
    "season": "2027-S1", "city_sha256": "city", "decks_sha256": "decks",
    "source_updated_at": "2026-10-18T07:00:00+09:00",
}


def deck(da=3, ba=2, *, bad_name=False):
    cards = [
        {"card_id": "50308", "name": "違う名前" if bad_name else "ダダリン",
         "section": "ポケモン (5)", "count": da},
        {"card_id": "50251", "name": "ジュペッタ",
         "section": "ポケモン (5)", "count": ba},
        {"card_id": "100", "name": "基本超エネルギー",
         "section": "エネルギー (55)", "count": 60 - da - ba},
    ]
    return {"total_cards": 60, "cards": cards,
            "deck_url": "https://www.pokemon-card.com/deck/confirm.html/deckID/code"}


class LibraryTests(unittest.TestCase):
    def test_exact_card_identity_and_60_card_sum(self):
        self.assertTrue(_valid_60(deck()))
        self.assertEqual(_identity_count(deck(), ALIASES, "ダダリン"), 3)
        self.assertEqual(_identity_count(deck(bad_name=True), ALIASES, "ダダリン"), 0)
        self.assertFalse(_valid_60({**deck(), "total_cards": 59}))

    def test_weekly_top16_and_full_dhelmise_cards(self):
        placements = [
            {"rank": rank, "deck_code": "code" if rank in (5, 9) else None}
            for rank in range(1, 17)
        ]
        city = {
            "season": "2027-S1",
            "events": [{
                "event_id": "123", "date": "2026-10-10",
                "category": "オープン", "venue_prefecture": "愛知県",
                "top16_checked_at": "2026-10-11T09:00:00+09:00",
                "top16_observed_rows": 8,
                "placements": placements,
            }],
        }
        output = build_library(city, {"decks": {"code": deck()}}, INDEX, ALIASES)
        rows = output["top16/W03.json"]["rows"]
        target = output["dhelmise/W03.json"]["rows"]
        self.assertEqual(len(rows), 16)
        self.assertEqual(len(target), 2)
        self.assertEqual(target[0]["rank"], 5)
        self.assertEqual(sum(c["count"] for c in target[0]["cards"]), 60)
        self.assertTrue(target[0]["banette_dhelmise_pattern"])
        self.assertEqual(target[0]["top16_event_coverage"], "confirmed_observation")
        stat = output["manifest.json"]["weeks"][0]
        self.assertEqual(stat["missing_deck_rows"], 14)
        self.assertEqual(stat["dhelmise_rows"], 2)
        self.assertEqual(stat["confirmed_top16_observation_event_count"], 1)

    def test_unchecked_event_is_not_claimed_complete(self):
        city = {
            "season": "2027-S1",
            "events": [{
                "event_id": "12", "date": "2026-10-04",
                "category": "ジュニア", "placements": [
                    {"rank": 1, "deck_code": "code"},
                ],
            }],
        }
        output = build_library(city, {"decks": {"code": deck()}}, INDEX, ALIASES)
        row = output["top16/W02.json"]["rows"][0]
        self.assertEqual(row["top16_event_coverage"], "unverified_or_partial")
        self.assertEqual(output["manifest.json"]["weeks"][0][
            "confirmed_top16_observation_event_count"], 0)

    def test_corrupt_or_missing_deck_ref_fails_closed(self):
        city = {"season": "2027-S1", "events": [
            {"event_id": "1", "date": "2026-10-05",
             "placements": [{"rank": 1, "deck_code": "missing"}]},
        ]}
        with self.assertRaisesRegex(ValueError, "Dangling"):
            build_library(city, {"decks": {}}, INDEX, ALIASES)
        with self.assertRaisesRegex(ValueError, "Invalid 60"):
            build_library(city, {"decks": {"missing": {**deck(), "total_cards": 59}}},
                          INDEX, ALIASES)


if __name__ == "__main__":
    unittest.main()
