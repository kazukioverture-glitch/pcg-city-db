"""Locally commit a verified FINAL input snapshot; never push."""

import shutil
import subprocess
import uuid
from pathlib import Path

from .state import create_snapshot, safe_relative, verify_snapshot


def _git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def finalize_week(root, *, week_id, period_start, period_end, cutoff_datetime,
                  retrieved_at, event_ids):
    root = Path(root).resolve()
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("Root must be the Git repository root")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Git working tree must be clean")
    safe_relative(week_id)
    if "/" in week_id:
        raise ValueError("week_id must be one path component")
    before = _git(root, "rev-parse", "HEAD")
    parent = root / "data/analysis/weekly_snapshots" / week_id / "FINAL"
    # Exclusive reservation also prevents concurrent finalization and refuses
    # incomplete earlier FINAL attempts rather than overwriting them.
    parent.mkdir(parents=True, exist_ok=False)
    directory = parent / uuid.uuid4().hex
    relative = directory.relative_to(root).as_posix()
    try:
        create_snapshot(root, event_ids=event_ids, retrieved_at=retrieved_at,
                        snapshot_id=directory.name, week_id=week_id,
                        period_start=period_start, period_end=period_end,
                        analysis_stage="FINAL", cutoff_datetime=cutoff_datetime)
        verify_snapshot(directory)
        _git(root, "add", "--", relative)
        _git(root, "commit", "--only", "-m", f"Finalize {week_id} input snapshot", "--", relative)
    except BaseException:
        # If commit completed (e.g. interruption just after Git returned), keep
        # its files. Never roll back an existing commit or unrelated changes.
        if _git(root, "rev-parse", "HEAD") == before:
            _git(root, "reset", "--quiet", "HEAD", "--", relative)
            shutil.rmtree(parent)
        raise
    return directory, _git(root, "rev-parse", "HEAD")
