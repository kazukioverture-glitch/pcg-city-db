#!/usr/bin/env python3
"""Validate final files and print official discovery versus stored results."""
import hashlib
import json
from pathlib import Path

try:
    from collector.merge_termux_snapshot import valid_deck
except ModuleNotFoundError:
    from merge_termux_snapshot import valid_deck

ROOT = Path(__file__).resolve().parents[1]


def main():
    def read(name):
        return json.loads((ROOT / name).read_text(encoding='utf-8'))
    city = read('data/city_db.json')
    decks = read('data/city_decks.json')['decks']
    index = read('data/index.json')
    audit = read('.tmp/official_audit.json')
    assert not audit['fetch_errors'], 'Official fetch errors remain'
    assert all(valid_deck(d) for d in decks.values()), 'Invalid actual card quantity'
    expected = {'event_count': len(city['events']),
                'placement_count': sum(len(e.get('placements', [])) for e in city['events']),
                'unique_deck_count': len(decks), 'valid_60_count': len(decks)}
    for key, value in expected.items():
        assert index[key] == value, f'Index mismatch: {key}'
    for name, key in [('city_db', 'city_sha256'), ('city_decks', 'decks_sha256')]:
        assert index[key] == hashlib.sha256((ROOT / f'data/{name}.json').read_bytes()).hexdigest()
    stored = {str(e['event_id']): e for e in city['events']}
    assert len(stored) == len(city['events']), 'Duplicate event identity'
    for date in sorted({e['date'] for e in audit['events']}):
        official = [e for e in audit['events'] if e['date'] == date]
        ids = {e['event_id'] for e in official}
        selected = [stored[i] for i in sorted(ids & stored.keys())]
        codes = {p['deck_code'] for e in selected for p in e['placements'] if p.get('deck_code')}
        assert codes <= decks.keys(), f'Missing deck records: {codes - decks.keys()}'
        missing = [{'event_id': e['event_id'], 'reason': e['status']} for e in official if e['event_id'] not in stored]
        assert not any(e['reason'] == 'published' for e in missing), 'Published event absent'
        print(json.dumps({'date': date, 'official_discovered': len(official), 'stored_discovered': len(selected),
              'all_stored_for_date': sum(e.get('date') == date for e in city['events']),
              'placement_rows': sum(len(e['placements']) for e in selected),
              'valid_60_decks': len(codes), 'missing_events': missing}, ensure_ascii=False))
    print('FINAL TOTALS: ' + json.dumps(expected))
    print('NEW DECK CODES: ' + json.dumps(audit.get('new_deck_codes', [])))


if __name__ == '__main__':
    main()
