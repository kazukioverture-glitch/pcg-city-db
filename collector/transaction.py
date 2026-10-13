"""Single-writer publication with restartable rollback on Linux/Android.

Readers must still validate index hashes: separate paths are not a read-atomic
snapshot. Git publishes the validated four-file result in one commit.
"""
import fcntl
import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

DATA_FILES = ('city_db.json', 'city_decks.json', 'index.json', 'analysis/event_ledger.json')


def flush_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    with target.open('rb') as handle:
        os.fsync(handle.fileno())
    flush_directory(target.parent)


def replace_file(source, target):
    target = Path(target)
    temporary = target.with_suffix('.json.part')
    durable_copy(source, temporary)
    temporary.replace(target)
    flush_directory(target.parent)


@contextmanager
def collection_lock(root):
    directory = Path(root) / '.tmp'
    directory.mkdir(parents=True, exist_ok=True)
    # Never unlink a flock path; waiters must refer to the same inode.
    with (directory / 'collection.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another collector is running; no files changed') from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def recover(root):
    """Restore all pending originals before reading DB or deleting work files."""
    root = Path(root)
    pending = root / '.tmp/publication.pending'
    if not pending.exists():
        return False
    manifest = json.loads((pending / 'manifest.json').read_text())
    if not manifest or not set(manifest) <= set(DATA_FILES):
        raise ValueError('Invalid rollback manifest; manual inspection required')
    # Check every backup before touching any production file.
    for name, digest in manifest.items():
        if hashlib.sha256((pending / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Invalid rollback backup; manual inspection required')
    for name in manifest:
        target = root / 'data' / name
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != manifest[name]:
            replace_file(pending / name, target)
    completed = root / '.tmp/publication.completed'
    if completed.exists():
        shutil.rmtree(completed)
    pending.rename(completed)
    flush_directory(pending.parent)
    shutil.rmtree(completed)
    return True


def publish(root, stage):
    """Caller holds collection_lock; unchanged files are never replaced."""
    root, stage = Path(root), Path(stage)
    changed = [name for name in DATA_FILES
               if (root / 'data' / name).read_bytes() != (stage / name).read_bytes()]
    if not changed:
        return []
    pending = root / '.tmp/publication.pending'
    if pending.exists():
        raise RuntimeError('Recover pending publication before starting another')
    preparing = root / '.tmp/publication.preparing'
    completed = root / '.tmp/publication.completed'
    for path in (preparing, completed):
        if path.exists():
            shutil.rmtree(path)
    preparing.mkdir(parents=True)
    manifest = {}
    for name in changed:
        durable_copy(root / 'data' / name, preparing / name)
        manifest[name] = hashlib.sha256((preparing / name).read_bytes()).hexdigest()
    with (preparing / 'manifest.json').open('w') as handle:
        json.dump(manifest, handle)
        handle.flush()
        os.fsync(handle.fileno())
    flush_directory(preparing)
    preparing.rename(pending)
    flush_directory(pending.parent)
    try:
        for name in changed:
            replace_file(stage / name, root / 'data' / name)
        pending.rename(completed)
        flush_directory(pending.parent)
    except BaseException:
        # If rollback itself fails, keep the pending journal for next startup.
        recover(root)
        raise
    shutil.rmtree(completed)
    return changed
