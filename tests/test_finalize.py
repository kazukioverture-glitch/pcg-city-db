import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analysis.finalize import finalize_week
from analysis.state import STATE_FILES, verify_snapshot


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        (self.root / "data/analysis").mkdir(parents=True)
        for name in ("city_db.json", "city_decks.json", "index.json"):
            (self.root / "data" / name).write_text(json.dumps({"events": [{"event_id": "1"}]}))
        for kind, name in STATE_FILES.items():
            (self.root / "data/analysis" / name).write_text(json.dumps(
                {"schema_version": "1.2.1", "kind": kind, "records": []}))
        self.git("add", ".")
        self.git("commit", "-qm", "Initial")
        self.before = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True).strip()

    def finalize(self):
        return finalize_week(self.root, week_id="W02", period_start="2026-09-30T00:00:00+09:00",
                             period_end="2026-10-06T23:59:59+09:00",
                             cutoff_datetime="2026-10-07T00:00:00+09:00",
                             retrieved_at="2026-10-07T00:00:00+09:00", event_ids=["1"])

    def test_dirty_refused(self):
        (self.root / "untracked").write_text("keep")
        with self.assertRaisesRegex(ValueError, "clean"):
            self.finalize()
        self.assertEqual(self.git("rev-parse", "HEAD"), self.before)
        self.assertFalse((self.root / "data/analysis/weekly_snapshots").exists())

    def test_verified_commit_and_duplicate_refused(self):
        directory, sha = self.finalize()
        metadata = verify_snapshot(directory)
        self.assertFalse(metadata["git_dirty"])
        self.assertEqual(metadata["git_commit_sha"], self.before)
        self.assertNotEqual(sha, self.before)
        self.assertEqual(self.git("status", "--porcelain"), "")
        changed = self.git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines()
        self.assertTrue(all(p.startswith(str(directory.relative_to(self.root)) + "/") for p in changed))
        with self.assertRaises(FileExistsError):
            self.finalize()
        self.assertEqual(self.git("rev-parse", "HEAD"), sha)

    def test_verify_failure_rolls_back(self):
        with patch("analysis.finalize.verify_snapshot", side_effect=ValueError("SHA256 mismatch")):
            with self.assertRaises(ValueError):
                self.finalize()
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse((self.root / "data/analysis/weekly_snapshots/W02/FINAL").exists())
        self.assertEqual(self.git("rev-parse", "HEAD"), self.before)

    def test_commit_failure_rolls_back_staging(self):
        hook = self.root / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        with self.assertRaises(subprocess.CalledProcessError):
            self.finalize()
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse((self.root / "data/analysis/weekly_snapshots/W02/FINAL").exists())
        self.assertEqual(self.git("rev-parse", "HEAD"), self.before)

    def test_creation_failure_rolls_back(self):
        with patch("analysis.finalize.create_snapshot", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.finalize()
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse((self.root / "data/analysis/weekly_snapshots/W02/FINAL").exists())
