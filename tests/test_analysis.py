import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from analysis.cards import section_type, validate_deck
from analysis.state import (ROOT, append_observation, create_snapshot, events_in_prefecture,
                            import_legacy, read_json, read_state, validate, verify_snapshot,
                            write_state)


def ledger():
    return import_legacy({"events": [{"event_id": "1", "date": "2026-10-02",
                                     "category": "オープン", "placements": [
                                         {"player_id": "p1", "prefecture": "愛知県"}]}]})


def observation(**changes):
    value = dict(observed_at="2026-10-03T00:00:00+09:00", source_url=None,
                 publication_status="published", fetch_status="success", parse_status="partial",
                 coverage_scope="top8", published_rank_slots_total=4,
                 top8_slots_published=4, top8_slots_retrieved=3, top8_decks_known=2, notes=None)
    return {**value, **changes}


class StateTests(unittest.TestCase):
    def test_schema_and_checked_in_state(self):
        Draft202012Validator.check_schema(read_json(ROOT / "schemas/analysis-state.schema.json"))
        for path in (ROOT / "data/analysis").glob("*.json"):
            read_state(path)

    def test_import_preserves_unknown_venue_and_status(self):
        event = ledger()["records"][0]
        self.assertIsNone(event["venue_prefecture"])
        self.assertEqual(event["event_status"], "unknown")
        self.assertEqual(event["observations"], [])
        self.assertEqual(event["players"][0]["player_prefecture"], "愛知県")
        self.assertEqual(events_in_prefecture(ledger(), "愛知県"), [])
        data = ledger()
        data["records"][0]["venue_prefecture"] = "大阪府"
        self.assertEqual(len(events_in_prefecture(data, "大阪府")), 1)

    def test_publication_history_survives_failed_fetch(self):
        data = append_observation(ledger(), "1", observation())
        data = append_observation(data, "1", observation(
            observed_at="2026-10-04T00:00:00+09:00", publication_status="unknown",
            fetch_status="failed", parse_status="not_parsed", coverage_scope="unknown",
            published_rank_slots_total=None, top8_slots_published=None,
            top8_slots_retrieved=None, top8_decks_known=None))
        self.assertEqual(data["records"][0]["observations"][0]["publication_status"], "published")
        self.assertEqual(data["records"][0]["observations"][1]["fetch_status"], "failed")
        self.assertEqual(data["records"][0]["event_status"], "unknown")
        self.assertEqual(data["records"][0]["observations"][0]["top8_slots_published"], 4)

    def test_independent_event_states(self):
        for state in ("cancelled", "not_held", "held", "scheduled", "unknown"):
            data = ledger()
            data["records"][0]["event_status"] = state
            validate(append_observation(data, "1", observation(publication_status="not_published")))

    def test_invalid_observation_does_not_mutate(self):
        data = ledger()
        with self.assertRaises(ValidationError):
            append_observation(data, "1", observation(fetch_status="fetch_failed"))
        self.assertEqual(data["records"][0]["observations"], [])
        with self.assertRaises(ValueError):
            append_observation(data, "1", observation(top8_decks_known=4))

    def test_invalid_date_and_duplicate_event(self):
        data = ledger()
        data["records"][0]["event_date"] = "2026-02-30"
        with self.assertRaises(ValidationError):
            validate(data)
        data = ledger()
        data["records"].append(data["records"][0])
        with self.assertRaises(ValueError):
            validate(data)

    def test_classification_supports_multiple_tags_without_forcing_unknown(self):
        data = {"schema_version": "1.2.1", "kind": "deck_classifications", "records": [
            {"deck_code": "code", "parent_archetype": None, "variant_tags": [],
             "classification_status": "unknown", "classified_at": None,
             "classifier_version": None, "evidence": None}]}
        validate(data)
        record = data["records"][0]
        record.update(parent_archetype="既存分類", variant_tags=["タグA", "タグB"], classification_status="partial")
        validate(data)
        record["classification_status"] = "classified"
        validate(data)
        record["parent_archetype"] = None
        with self.assertRaises(ValidationError):
            validate(data)

    def test_atomic_state_roundtrip_and_invalid_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            write_state(path, ledger())
            before = path.read_bytes()
            data = ledger()
            data["records"][0]["result_status"] = "success"
            with self.assertRaises(ValidationError):
                write_state(path, data)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(read_state(path), ledger())

    def test_future_state_roundtrips(self):
        records = {
            "processed_sources": dict(source_id="v1", source_type="video", url="https://example.com/v1",
                                      content_sha256=None, processed_at=None, status="pending",
                                      snapshot_ids=["s1"], notes=None),
            "research_requests": dict(request_id="r1", requested_at="2026-10-03T00:00:00Z",
                                      question="公開確認", status="pending", snapshot_ids=["s1"], result=None),
            "forecast_log": dict(forecast_id="f1", recorded_at="2026-10-03T00:00:00Z", target_week="2026-W40",
                                 snapshot_ids=["s1"], forecast="記録のみ", outcome=None, status="recorded"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for kind, record in records.items():
                data = dict(schema_version="1.2.1", kind=kind, records=[record])
                path = Path(tmp) / f"{kind}.json"
                write_state(path, data)
                self.assertEqual(read_state(path), data)

    def test_ledger_history_cannot_be_deleted_or_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            data = append_observation(ledger(), "1", observation())
            write_state(path, data)
            for change in (ledger(), {**ledger(), "records": []}):
                with self.assertRaisesRegex(ValueError, "history"):
                    write_state(path, change)
            data["records"][0]["observations"][0]["publication_status"] = "unknown"
            with self.assertRaisesRegex(ValueError, "history"):
                write_state(path, data)
            self.assertEqual(read_state(path)["records"][0]["observations"][0]["publication_status"], "published")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "data").mkdir()
        self.city = self.root / "data/city_db.json"
        self.city.write_text('{"events":[{"event_id":"1"}]}', encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], cwd=self.root, check=True)

    def capture(self, **kwargs):
        args = dict(root=self.root, target_week="2026-W40", event_ids=["1"],
                    retrieved_at="2026-10-03T00:00:00+09:00", files=["data/city_db.json"])
        return create_snapshot(**{**args, **kwargs})

    def test_two_versions_preserve_bytes_and_commit(self):
        first = self.capture()
        original = (first / "inputs/data/city_db.json").read_bytes()
        self.city.write_text('{"events":[{"event_id":"1","new":true}]}', encoding="utf-8")
        second = self.capture()
        self.assertNotEqual(first, second)
        self.assertEqual((first / "inputs/data/city_db.json").read_bytes(), original)
        self.assertFalse(verify_snapshot(first)["git_dirty"])
        self.assertTrue(verify_snapshot(second)["git_dirty"])
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        self.assertEqual(verify_snapshot(first)["git_commit_sha"], head)

    def test_explicit_version_never_overwritten(self):
        directory = self.capture(snapshot_id="fixed")
        before = (directory / "metadata.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.capture(snapshot_id="fixed")
        self.assertEqual((directory / "metadata.json").read_bytes(), before)
        with self.assertRaises(ValueError):
            write_state(directory / "metadata.json", read_state(directory / "metadata.json"))

    def test_hash_detects_tampering(self):
        directory = self.capture()
        (directory / "inputs/data/city_db.json").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            verify_snapshot(directory)

    def test_invalid_inputs_and_path_traversal(self):
        cases = [dict(target_week="2026-W99"), dict(target_week="../bad"),
                 dict(retrieved_at="2026-10-03T00:00:00"), dict(event_ids=["missing"]),
                 dict(event_ids=["1", "1"]), dict(files=["../outside.json"]),
                 dict(files=["C:/outside.json"]), dict(snapshot_id="../bad"),
                 dict(files=["data/city_db.json", "data/city_db.json"])]
        for case in cases:
            with self.subTest(case=case), self.assertRaises((ValueError, ValidationError)):
                self.capture(**case)
        self.assertFalse((self.root / "data/analysis").exists())


class CardTests(unittest.TestCase):
    def deck(self, **changes):
        return {"total_cards": 60, "cards": [{"card_id": "1", "name": "カード名",
                "count": 60, "section": "ポケモンのどうぐ (60)", **changes}]}

    def codes(self, deck, master=None):
        return {i["code"] for i in validate_deck(deck, master)}

    def test_pokemon_tool_boundary(self):
        self.assertEqual(section_type("ポケモン"), "pokemon")
        self.assertEqual(section_type("ポケモン (20)"), "pokemon")
        self.assertEqual(section_type("ポケモンのどうぐ"), "tool")
        self.assertEqual(section_type("ポケモンのどうぐ (4)"), "tool")
        self.assertIsNone(section_type("ポケモンのどうぐっぽい"))
        self.assertIsNone(section_type("ポケモンカード"))

    def test_master_unavailable_and_unknown_id_are_warn(self):
        issues = validate_deck(self.deck())
        self.assertEqual(issues, [{"severity": "WARN", "code": "MASTER_UNAVAILABLE", "card_index": None}])
        self.assertEqual(self.codes(self.deck(), {}), {"CARD_ID_NOT_IN_MASTER"})

    def test_explicit_identity_alias_and_type(self):
        master = {"1": {"name": "カード名", "aliases": ["カード名M2100/100"], "card_type": "tool"}}
        self.assertEqual(validate_deck(self.deck(), master), [])
        self.assertEqual(validate_deck(self.deck(name="カード名M2100/100"), master), [])
        self.assertIn("CARD_NAME_MISMATCH", self.codes(self.deck(name="別名"), master))
        self.assertIn("CARD_TYPE_MISMATCH", self.codes(self.deck(section="ポケモン (60)"), master))
        self.assertIn("CARD_TYPE_UNKNOWN", self.codes(self.deck(section="不明"), master))

    def test_total_and_counts_checked_independently(self):
        deck = self.deck(count=59)
        self.assertIn("CARD_COUNT_SUM_NOT_60", self.codes(deck))
        deck["total_cards"] = 59
        self.assertIn("INVALID_TOTAL_CARDS", self.codes(deck))
        self.assertIn("INVALID_CARD_COUNT", self.codes(self.deck(count=True)))
        self.assertIn("CARD_ID_MISSING", self.codes(self.deck(card_id=None)))


if __name__ == "__main__":
    unittest.main()
