"""One transaction boundary for collection: acquire, stage, validate, publish."""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis.collection import sync_ledger, sync_schedule_snapshot
from analysis.state import read_state
from collector.download_termux import download_one, download_pair
from collector.merge_termux_snapshot import merge_snapshot
from collector.validate_snapshot import read, validate_snapshot, write_candidate

ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
DATA_FILES = ('city_db.json', 'city_decks.json', 'index.json', 'analysis/event_ledger.json')


def run_official(stage, work, timeout=720):
    try:
        result = subprocess.run([sys.executable, str(ROOT / 'collector/sync_official_api.py'),
                                 '--data-dir', str(stage), '--tmp-dir', str(work)],
                                timeout=timeout)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f'::warning::Official collection exceeded {timeout:.0f}-second budget', flush=True)
        return False



def refresh_local_schedule(root, work):
    """On the Android collector, fetch one exact day only when the target date changes."""
    root = Path(root)
    if root.resolve() != ROOT.resolve() or os.environ.get("TERMUX_BASE_URL"):
        return None
    now = datetime.now(JST)
    target = (now.date() + timedelta(days=1 if now.hour >= 20 else 0)).isoformat()
    output = root / ".tmp/public/city-schedule.json"
    if output.exists():
        try:
            if json.loads(output.read_text(encoding="utf-8")).get("target_date") == target:
                return output
        except (OSError, ValueError):
            pass
    try:
        result = subprocess.run([sys.executable, "-m", "collector.schedule",
                                 "--date", target, "--output", str(output)],
                                cwd=root, timeout=120)
    except subprocess.TimeoutExpired:
        print("::warning::Schedule collection exceeded 120-second budget", flush=True)
        return None
    if result.returncode != 0:
        print("::warning::Schedule collection failed; result sync continues", flush=True)
        return None
    return output

def sync(root=ROOT, base_url=None):
    deadline = time.monotonic() + 900
    root = Path(root)
    data = root / 'data'
    work = root / '.tmp/sync'
    if work.exists():
        shutil.rmtree(work)
    stage = work / 'data'
    stage.mkdir(parents=True)
    for name in DATA_FILES:
        destination = stage / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(data / name, destination)
    old_city, old_decks = read(data / 'city_db.json'), read(data / 'city_decks.json')
    validate_snapshot(old_city, old_decks)
    configured_url = base_url if base_url is not None else os.environ.get('TERMUX_BASE_URL')
    url = configured_url.strip().rstrip('/') if configured_url else ''
    schedule_from_termux = base_url is None and bool(os.environ.get('TERMUX_BASE_URL'))
    ledger = stage / 'analysis/event_ledger.json'
    schedule_accepted = False
    schedule_updates = 0
    local_schedule = refresh_local_schedule(root, work) if not url else None
    if local_schedule is not None:
        try:
            schedule_updates = sync_schedule_snapshot(ledger, local_schedule)
            schedule_accepted = True
        except (ValueError, TypeError, KeyError, AssertionError) as exc:
            print(f'::warning::Local schedule snapshot rejected: {exc}', flush=True)
    if url:
        downloads = download_pair(url, work / 'termux')
    else:
        termux_work = work / 'termux'
        termux_work.mkdir(parents=True)
        downloads = {name: dict(success=False, attempts=[], skipped='TERMUX_BASE_URL not configured')
                     for name in ('city-db.json', 'city-decks.json')}
        (termux_work / 'download_report.json').write_text(json.dumps(downloads, indent=2) + '\n')
        print('::notice::TERMUX_BASE_URL not configured; using official source only', flush=True)
    termux_ok = False
    if all(r['success'] for r in downloads.values()):
        try:
            city, decks = merge_snapshot(old_city, old_decks,
                                         read(work / 'termux/city_db.json'), read(work / 'termux/city_decks.json'))
            validate_snapshot(city, decks, old_city, old_decks)
            write_candidate(stage, city, decks)
            termux_ok = True
        except (ValueError, TypeError, KeyError, AssertionError) as exc:
            print(f'::warning::Termux snapshot rejected: {exc}', flush=True)
    if url and schedule_from_termux and termux_ok:
        schedule_download = download_one(url, 'city-schedule.json',
                                         work / 'termux/city_schedule.json',
                                         budget=30, attempts=1)
        if schedule_download['success']:
            try:
                schedule_updates = sync_schedule_snapshot(ledger, work / 'termux/city_schedule.json')
                schedule_accepted = True
            except (ValueError, TypeError, KeyError, AssertionError) as exc:
                print(f'::warning::Termux schedule snapshot rejected: {exc}', flush=True)
    # A failed official run may have written candidate files; restore the accepted
    # Termux candidate (or baseline) before continuing.
    backup = {name: (stage / name).read_bytes() for name in DATA_FILES[:3]}
    official_ok = run_official(stage, work / 'official', timeout=min(720, max(1, deadline - time.monotonic())))
    if not official_ok:
        for name, content in backup.items():
            (stage / name).write_bytes(content)
        print('::warning::Official coverage unavailable; no completeness claim', flush=True)
    official_errors = {}
    if official_ok:
        audit = read(work / 'official/official_audit.json')
        official_errors = audit.get('fetch_errors', {})
    report = dict(termux_configured=bool(url), termux_accepted=termux_ok,
                  schedule_accepted=schedule_accepted, schedule_updates=schedule_updates,
                  official_accepted=official_ok, official_complete=official_ok and not official_errors,
                  official_failed_event_ids=sorted(official_errors), published=False)
    report_path = work / 'sync_report.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    if not (termux_ok or official_ok):
        raise RuntimeError('Neither source succeeded; all stored DB/ledger files preserved')
    counts = validate_snapshot(read(stage / 'city_db.json'), read(stage / 'city_decks.json'), old_city, old_decks)
    # Validate index hashes/coverage with the existing final checker.
    check = [sys.executable, str(ROOT / 'collector/check_sync.py'), '--root', str(work)]
    if not official_ok:
        check += ['--official-unavailable']
    elif official_errors:
        check += ['--allow-partial-official']
    subprocess.run(check, check=True)
    if url:
        sync_ledger(ledger, city_path=work / 'termux/city_db.json', decks_path=work / 'termux/city_decks.json',
                    fetch_status='success' if downloads['city-db.json']['success'] else 'failed',
                    parse_status=None if termux_ok else 'failed', coverage_scope='collection_feed',
                    source_url=url + '/city-db.json')
    if official_ok and (work / 'official/official_backfill.changed').exists():
        sync_ledger(ledger, city_path=work / 'official/official_backfill_city.json',
                    decks_path=work / 'official/official_backfill_decks.json', fetch_status='success',
                    coverage_scope='top8', source_url='https://players.pokemon-card.com/event/result/list')
    read_state(ledger)  # schema validation before any production write
    before = {name: (data / name).read_bytes() for name in DATA_FILES}
    try:
        for name in DATA_FILES:
            temp = (data / name).with_suffix('.json.part')
            shutil.copyfile(stage / name, temp)
            temp.replace(data / name)
    except BaseException:
        for name, content in before.items():
            (data / name).write_bytes(content)
        raise
    report.update(published=True, **counts)
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print('SYNC RESULT: ' + json.dumps(report), flush=True)
    return report


if __name__ == '__main__':
    sync()
