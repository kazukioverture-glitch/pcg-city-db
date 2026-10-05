import ast
import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

from jsonschema import ValidationError

from analysis.collection import sync_ledger
from analysis.state import (ROOT, create_snapshot, import_legacy, read_json,
                            read_state, validate, verify_snapshot, write_state)
import test_analysis


SOURCE = "https://example.com/city-db.json"
TIME = "2026-10-03T12:00:00+09:00"


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "event_ledger.json"
        self.city_path = self.root / "city_db.json"
        self.decks_path = self.root / "city_decks.json"
        self.city = {"updated_at": TIME, "events": [
            {"event_id": "1", "date": "2026-10-02", "category": "オープン",
             "placements": [
                 {"rank": 1, "deck_code": "deck1", "player_id": "p1", "prefecture": "愛知県"},
                 {"rank": 3, "deck_code": "deck2"}, {"rank": 3, "deck_code": None}]}]}
        self.city_path.write_text(json.dumps(self.city), encoding="utf-8")
        self.decks_path.write_text(json.dumps({"decks": {"deck1": {"total_cards": 60}}}), encoding="utf-8")

    def sync(self, **changes):
        return sync_ledger(self.path, **{**dict(city_path=self.city_path, decks_path=self.decks_path,
                                              fetch_status="success", coverage_scope="collection_feed",
                                              source_url=SOURCE, observed_at=TIME), **changes})

    def test_published_and_latest_failed_coexist_without_erasing(self):
        self.assertEqual(self.sync(), 1)
        first = read_state(self.path)["records"][0]["observations"][0]
        self.assertEqual(first["publication_status"], "published")
        self.assertEqual(first["fetch_status"], "success")
        self.assertEqual(first["top8_slots_retrieved"], 3)
        self.assertEqual(first["top8_decks_known"], 1)
        self.assertIsNone(first["published_rank_slots_total"])
        self.assertIsNone(first["top8_slots_published"])
        self.assertEqual(first["source_sha256"], hashlib.sha256(self.city_path.read_bytes()).hexdigest())
        self.sync(fetch_status="failed", observed_at="2026-10-04T12:00:00+09:00")
        record = read_state(self.path)["records"][0]
        self.assertEqual(record["observations"][0], first)
        self.assertEqual(record["observations"][-1]["publication_status"], "published")
        self.assertEqual(record["observations"][-1]["fetch_status"], "failed")
        self.assertEqual(record["observations"][-1]["parse_status"], "not_parsed")
        self.assertEqual(record["event_status"], "unknown")
        self.assertIsNone(record["venue_prefecture"])
        self.assertEqual(record["players"][0]["player_prefecture"], "愛知県")

    def test_parse_failure_and_validation_rejection_preserve_previous_records(self):
        self.sync()
        self.city_path.write_text("not json", encoding="utf-8")
        self.sync()
        latest = read_state(self.path)["records"][0]["observations"][-1]
        self.assertEqual((latest["fetch_status"], latest["parse_status"]), ("success", "failed"))
        self.assertEqual(latest["publication_status"], "published")
        # A rejected batch must not be imported even if it happens to be parseable.
        self.city_path.write_text('{"events":[{"event_id":"rejected"}]}', encoding="utf-8")
        self.sync(parse_status="failed")
        self.assertEqual([r["event_id"] for r in read_state(self.path)["records"]], ["1"])

    def test_scheduled_event_is_not_inferred_missing_or_failed(self):
        state = import_legacy({"events": [{"event_id": "scheduled"}]})
        state["records"][0].update(event_status="scheduled", legacy_source=None)
        write_state(self.path, state)
        self.sync()
        self.sync(fetch_status="failed")
        scheduled = read_state(self.path)["records"][0]
        self.assertEqual(scheduled["event_status"], "scheduled")
        self.assertEqual(scheduled["observations"], [])

    def test_invalid_rows_produce_partial_without_guessing_slots(self):
        self.city["events"][0]["placements"].append({"rank": "unknown"})
        self.city_path.write_text(json.dumps(self.city), encoding="utf-8")
        self.sync()
        latest = read_state(self.path)["records"][0]["observations"][-1]
        self.assertEqual(latest["parse_status"], "partial")
        self.assertEqual(latest["top8_slots_retrieved"], 3)

    def test_cli_updates_ledger_and_keeps_legacy_inputs_byte_identical(self):
        index_path = self.root / "index.json"
        index_path.write_bytes(b'{"status":"ok"}\n')
        paths = (self.city_path, self.decks_path, index_path)
        before = [p.read_bytes() for p in paths]
        result = subprocess.run([sys.executable, "-m", "analysis", "sync-ledger",
                                 "--city", str(self.city_path), "--decks", str(self.decks_path),
                                 "--ledger", str(self.path), "--fetch-status", "success",
                                 "--coverage-scope", "collection_feed", "--source-url", SOURCE],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p.read_bytes() for p in paths], before)
        self.assertEqual(len(read_state(self.path)["records"]), 1)

    def test_official_list_adapter_does_not_infer_rank_counts(self):
        self.city["events"][0].pop("placements")
        self.city["events"][0]["result_url"] = "https://players.pokemon-card.com/event/detail/1/result"
        self.city_path.write_text(json.dumps(self.city), encoding="utf-8")
        self.sync(coverage_scope="result_list")
        latest = read_state(self.path)["records"][0]["observations"][-1]
        self.assertEqual(latest["publication_status"], "published")
        self.assertIsNone(latest["top8_slots_retrieved"])


class CustomWeekTests(unittest.TestCase):
    # Reuse only the Git fixture setup; do not duplicate the old test methods.
    setUp = test_analysis.SnapshotTests.setUp

    def capture(self, **changes):
        args = dict(root=self.root, event_ids=["1"], retrieved_at=TIME,
                    files=["data/city_db.json"], week_id="W02",
                    period_start="2026-09-30T00:00:00+09:00",
                    period_end="2026-10-06T23:59:59+09:00",
                    analysis_stage="INTERIM", cutoff_datetime=TIME, snapshot_id="same-id")
        return create_snapshot(**{**args, **changes})

    def test_wednesday_to_tuesday_interim_and_final_stored_separately(self):
        interim = self.capture()
        before = (interim / "metadata.json").read_bytes()
        final = self.capture(analysis_stage="FINAL", cutoff_datetime="2026-10-07T01:00:00+09:00")
        self.assertNotEqual(interim, final)
        self.assertEqual((interim / "metadata.json").read_bytes(), before)
        self.assertEqual(verify_snapshot(interim)["period_start"], "2026-09-30T00:00:00+09:00")
        self.assertEqual(verify_snapshot(final)["analysis_stage"], "FINAL")
        self.assertEqual(verify_snapshot(final)["week_id"], "W02")

    def test_ledger_only_events_and_unknown_ids(self):
        state = import_legacy({"events": [{"event_id": str(i)} for i in range(2, 8)]})
        for event, status in zip(state["records"], ("scheduled", "cancelled", "unknown", "unknown", "unknown", "unknown")):
            event["event_status"] = status
        from test_analysis import observation
        state["records"][2]["observations"].append(observation(publication_status="not_published"))
        state["records"][3]["observations"].append(observation(fetch_status="failed", parse_status="not_parsed"))
        state["records"][4]["observations"].append(observation(parse_status="failed"))
        write_state(self.root / "data/analysis/event_ledger.json", state)
        files = ["data/city_db.json", "data/analysis/event_ledger.json"]
        directory = self.capture(event_ids=[str(i) for i in range(1, 8)], files=files)
        self.assertEqual(verify_snapshot(directory)["event_ids"], [str(i) for i in range(1, 8)])
        with self.assertRaisesRegex(ValueError, "missing"):
            self.capture(event_ids=["missing"], files=files, snapshot_id="unknown")
        # An uncaptured live ledger must not be used for membership checks.
        with self.assertRaisesRegex(ValueError, "missing"):
            self.capture(event_ids=["2"], snapshot_id="uncaptured")

    def test_invalid_custom_periods_and_paths_are_rejected(self):
        cases = [dict(period_end="2026-09-29T00:00:00+09:00"), dict(week_id="../W02"),
                 dict(analysis_stage="interim"), dict(cutoff_datetime="2026-09-29T00:00:00+09:00"),
                 dict(period_start=None), dict(period_start="2026-09-30T00:00:00")]
        for case in cases:
            with self.subTest(case=case), self.assertRaises((ValueError, ValidationError)):
                self.capture(**case)

    def test_collected_ledger_to_interim_snapshot_end_to_end(self):
        city = {"events": [{"event_id": "1", "date": "2026-10-02", "placements": [{"rank": 1}]}]}
        self.city.write_text(json.dumps(city), encoding="utf-8")
        ledger_path = self.root / "data/analysis/event_ledger.json"
        sync_ledger(ledger_path, city_path=self.city, fetch_status="success", source_url=SOURCE,
                    coverage_scope="collection_feed", observed_at=TIME)
        directory = self.capture(files=["data/city_db.json", "data/analysis/event_ledger.json"])
        captured = read_state(directory / "inputs/data/analysis/event_ledger.json")
        self.assertEqual(captured["records"][0]["observations"][0]["publication_status"], "published")
        verify_snapshot(directory)


class ForecastTests(unittest.TestCase):
    def forecast(self, timing="PRE"):
        return dict(forecast_id="f1", created_at=TIME, target_week="W02", forecast_timing=timing,
                    forecast_text="検収用の予測原文", evidence=["snapshot内の出典"],
                    falsification_condition="結果が予測条件を満たさない", source_snapshot_id="snapshot1",
                    evaluation_eligible=timing == "PRE")

    def document(self, timing="PRE"):
        return dict(schema_version="1.2.2", kind="forecast_log", records=[self.forecast(timing)])

    def test_pre_late_post_saved_and_only_pre_evaluation_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            for timing in ("PRE", "LATE", "POST"):
                path = Path(tmp) / f"{timing}.json"
                data = self.document(timing)
                write_state(path, data)
                self.assertEqual(read_state(path), data)
                data["records"][0]["evaluation_eligible"] = timing != "PRE"
                with self.assertRaises(ValidationError):
                    validate(data)

    def test_forecast_original_and_timing_cannot_be_overwritten_or_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "forecasts.json"
            write_state(path, self.document("LATE"))
            before = path.read_bytes()
            for key, value in (("forecast_text", "書き換え"), ("created_at", "2026-10-01T00:00:00Z"),
                               ("evidence", ["別根拠"]), ("source_snapshot_id", "another")):
                changed = self.document("LATE")
                changed["records"][0][key] = value
                with self.assertRaisesRegex(ValueError, "immutable"):
                    write_state(path, changed)
            with self.assertRaisesRegex(ValueError, "immutable"):
                write_state(path, self.document("PRE"))
            with self.assertRaisesRegex(ValueError, "immutable"):
                write_state(path, {**self.document(), "records": []})
            self.assertEqual(path.read_bytes(), before)
            appended = self.document("LATE")
            appended["records"].append({**self.forecast("POST"), "forecast_id": "f2"})
            write_state(path, appended)
            self.assertEqual(len(read_state(path)["records"]), 2)

    def test_legacy_forecast_is_readable_and_also_immutable(self):
        legacy = dict(schema_version="1.2.1", kind="forecast_log", records=[
            dict(forecast_id="old", recorded_at=TIME, target_week="2026-W40", snapshot_ids=["s1"],
                 forecast="旧原文", outcome=None, status="recorded")])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "forecast.json"
            write_state(path, legacy)
            legacy["records"][0]["forecast"] = "変更"
            with self.assertRaisesRegex(ValueError, "immutable"):
                write_state(path, legacy)


class WorkflowCompatibilityTests(unittest.TestCase):
    def test_collector_output_matches_baseline_and_updates_ledger_offline(self):
        # Fake the official list page; no Playwright/browser/network is needed.
        text = "シティリーグ 2026/10/02"
        parent = SimpleNamespace(count=AsyncMock(return_value=1), inner_text=AsyncMock(return_value=text))
        parent.first = parent
        link = SimpleNamespace(get_attribute=AsyncMock(return_value="/event/detail/1/result"),
                               inner_text=AsyncMock(return_value=text), locator=lambda _: parent)
        links = SimpleNamespace(count=AsyncMock(return_value=1), nth=lambda _: link)
        body = SimpleNamespace(inner_text=AsyncMock(return_value=text))
        page = SimpleNamespace(goto=AsyncMock(), wait_for_load_state=AsyncMock(),
                               wait_for_timeout=AsyncMock(), evaluate=AsyncMock(),
                               title=AsyncMock(return_value="Official result list"),
                               locator=lambda selector: body if selector == "body" else links)
        browser = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=SimpleNamespace(
            chromium=SimpleNamespace(launch=AsyncMock(return_value=browser))))
        context.__aexit__ = AsyncMock(return_value=False)

        def load(source):
            tree = ast.parse(source)
            tree.body = [node for node in tree.body
                         if not (isinstance(node, ast.ImportFrom) and node.module == "playwright.async_api")
                         and not isinstance(node, ast.If)]
            namespace = {"__file__": str(ROOT / "collector/collect.py")}
            exec(compile(tree, "collector/collect.py", "exec"), namespace)
            namespace["async_playwright"] = lambda: context
            return namespace

        old_source = subprocess.check_output(["git", "show", "bae6a6b:collector/collect.py"],
                                             cwd=ROOT, encoding="utf-8")
        new_source = (ROOT / "collector/collect.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = load(old_source), load(new_source)
            old["OUTPUT"], new["OUTPUT"] = root / "old.json", root / "index.json"
            ledger_path = root / "event_ledger.json"
            new["record_collection"] = Mock(side_effect=lambda status: sync_ledger(
                ledger_path, city_path=new["OUTPUT"], fetch_status=status,
                coverage_scope="result_list", source_url=new["LIST_URL"], observed_at=TIME))
            asyncio.run(old["main"]())
            attempt = {}
            asyncio.run(new["main"](attempt))
            before, after = read_json(old["OUTPUT"]), read_json(new["OUTPUT"])
            before.pop("updated_at")
            after.pop("updated_at")
            self.assertEqual(after, before)
            self.assertEqual(attempt["fetch_status"], "success")
            new["record_collection"].assert_called_once_with("success")
            record = read_state(ledger_path)["records"][0]
            self.assertEqual(record["event_id"], "1")
            self.assertEqual(record["observations"][0]["publication_status"], "published")

    def test_validate_python_block_identical_to_baseline(self):
        current = (ROOT / ".github/workflows/collect.yml").read_text(encoding="utf-8")
        previous = subprocess.check_output(["git", "show", "bae6a6b:.github/workflows/collect.yml"],
                                           cwd=ROOT, encoding="utf-8")
        def block(text):
            return textwrap.dedent(text.split("python - <<'PY'\n", 1)[1].split("          PY", 1)[0])
        self.assertEqual(block(current), block(previous))
        self.assertIn("steps.ledger.outcome == 'success'", current)
        self.assertIn("steps.official.outcome == 'success'", current)

if __name__ == "__main__":
    unittest.main()
