"""Bounded concurrent HTTP/1.1 downloads; only complete JSON becomes usable."""
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_URL = 'https://tuition-occultist-unwritten.ngrok-free.dev'
FILES = {'city-db.json': 'city_db.json', 'city-decks.json': 'city_decks.json'}


def download_one(base_url, filename, destination, *, budget=540, attempts=3):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    part = destination.with_suffix('.json.part')
    headers = destination.with_suffix('.headers')
    deadline = time.monotonic() + budget
    records = []
    for attempt in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        part.unlink(missing_ok=True)
        headers.unlink(missing_ok=True)
        command = ['curl', '--http1.1', '--silent', '--show-error', '--location',
                   '--fail-with-body', '--connect-timeout', '15', '--max-time', str(remaining),
                   '--header', 'ngrok-skip-browser-warning: true', '--dump-header', str(headers),
                   '--output', str(part), '--write-out', '%{http_code}', f'{base_url}/{filename}']
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=remaining + 2)
            status, code, error = run.stdout.strip(), run.returncode, run.stderr.strip()
        except subprocess.TimeoutExpired:
            status, code, error = '000', 124, 'Transfer budget exhausted'
        parsed = False
        if code == 0 and status == '200':
            try:
                value = json.loads(part.read_bytes())
                parsed = isinstance(value, dict)
                if not parsed:
                    error = 'JSON must be an object'
            except (OSError, ValueError) as exc:
                error = f'Invalid/incomplete JSON: {exc}'
        record = dict(file=filename, attempt=attempt, http_status=status, curl_exit=code,
                      parse_success=parsed, error=error)
        records.append(record)
        print(json.dumps(record), flush=True)
        if parsed:
            part.replace(destination)
            return dict(success=True, attempts=records)
        if headers.exists():
            safe_headers = [line for line in headers.read_text(errors='replace').splitlines()
                            if line.lower().startswith(('http/', 'content-type:', 'ngrok-error-code:', 'retry-after:'))]
            print('Response headers: ' + ' | '.join(safe_headers), flush=True)
        if part.exists():
            print('Response body prefix: ' + repr(part.read_bytes()[:1000].decode('utf-8', errors='replace')), flush=True)
        if attempt < attempts:
            time.sleep(min(2 ** attempt, max(0, deadline - time.monotonic())))
    part.unlink(missing_ok=True)
    return dict(success=False, attempts=records)


def download_pair(base_url, directory, **kwargs):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    base_url = (base_url or DEFAULT_URL).rstrip('/')
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {filename: pool.submit(download_one, base_url, filename, directory / local, **kwargs)
                   for filename, local in FILES.items()}
        results = {filename: future.result() for filename, future in futures.items()}
    for filename, result in results.items():
        (directory / (FILES[filename] + '.status')).write_text('success' if result['success'] else 'failed')
    (directory / 'download_report.json').write_text(json.dumps(results, indent=2) + '\n')
    return results
