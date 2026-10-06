"""Exercise the production validator against stored DB and corrupt candidates."""

import hashlib
import os
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from analysis.state import ROOT, read_json


class LegacyValidateTests(unittest.TestCase):
    def run_validate(self, mutate=None):
        script = """
from collector.validate_snapshot import read, validate_snapshot, write_candidate
from pathlib import Path
validate_snapshot(read('.tmp/city_db.json'), read('.tmp/city_decks.json'),
                  read('data/city_db.json'), read('data/city_decks.json'))
counts = write_candidate(Path('data'), read('.tmp/city_db.json'), read('.tmp/city_decks.json'))
print(f"OK: {counts['event_count']} events, {counts['unique_deck_count']} decks")
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".tmp").mkdir()
            (root / "data").mkdir()
            for name in ("city_db", "city_decks"):
                content = (ROOT / f"data/{name}.json").read_bytes()
                (root / f".tmp/{name}.json").write_bytes(content)
                (root / f"data/{name}.json").write_bytes(content)
            if mutate:
                mutate(root)
            result = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
            if result.returncode == 0:
                index = read_json(root / "data/index.json")
                for name, key in (("city_db", "city_sha256"), ("city_decks", "decks_sha256")):
                    self.assertEqual(index[key], hashlib.sha256((root / f"data/{name}.json").read_bytes()).hexdigest())
            return result

    def test_current_data_pass_unchanged_validate(self):
        result = self.run_validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        events = len(read_json(ROOT / "data/city_db.json")["events"])
        decks = len(read_json(ROOT / "data/city_decks.json")["decks"])
        self.assertIn(f"OK: {events} events, {decks} decks", result.stdout)

    def test_invalid_60_card_deck_rejected(self):
        def mutate(root):
            path = root / ".tmp/city_decks.json"
            data = read_json(path)
            next(iter(data["decks"].values()))["total_cards"] = 59
            path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_validate(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid 60-card deck", result.stderr)

    def test_event_count_decrease_rejected(self):
        def mutate(root):
            path = root / ".tmp/city_db.json"
            data = read_json(path)
            data["events"].pop()
            path.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_validate(mutate)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("city_db identity/count decreased", result.stderr)


if __name__ == "__main__":
    unittest.main()
