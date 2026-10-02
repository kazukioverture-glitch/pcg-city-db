"""Execute the unchanged workflow's actual Validate script against offline fixtures."""

import hashlib
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
        workflow = (ROOT / ".github/workflows/collect.yml").read_text(encoding="utf-8")
        script = textwrap.dedent(workflow.split("python - <<'PY'\n", 1)[1].split("          PY", 1)[0])
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
            result = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True)
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
        self.assertIn("city_db count decreased", result.stderr)


if __name__ == "__main__":
    unittest.main()
