"""Validate a candidate against stored identities, quantities and index hashes."""
import hashlib
import json
from pathlib import Path

from collector.merge_termux_snapshot import valid_deck, stamp


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_snapshot(city, decks, old_city=None, old_decks=None):
    if not isinstance(city, dict) or not isinstance(city.get('events'), list) or not city['events']:
        raise ValueError('Invalid/empty city DB')
    if not isinstance(decks, dict) or not isinstance(decks.get('decks'), dict) or not decks['decks']:
        raise ValueError('Invalid/empty decks DB')
    dm = decks['decks']
    if not all(valid_deck(d) for d in dm.values()):
        raise ValueError('Invalid 60-card deck')
    ids = [str(e['event_id']) for e in city['events']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate event identity')
    for event in city['events']:
        rows = event.get('placements')
        if not isinstance(rows, list):
            raise ValueError('Invalid placements')
        if any(p.get('deck_code') and p['deck_code'] not in dm for p in rows):
            raise ValueError('Missing referenced deck')
    if old_city is not None:
        for key in ('season', 'season_start'):
            if old_city.get(key) and city.get(key) != old_city[key]:
                raise ValueError('Snapshot season mismatch')
        if not {str(e['event_id']) for e in old_city['events']} <= set(ids):
            raise ValueError('city_db identity/count decreased')
        current = {str(e['event_id']): e for e in city['events']}
        for event in old_city['events']:
            def identity(p):
                return str(p.get('player_id') or (p.get('player_name'), p.get('deck_code')))
            if not {identity(p) for p in event.get('placements', [])} <= {identity(p) for p in current[str(event['event_id'])]['placements']}:
                raise ValueError('Placement identity/count decreased')
        if stamp(city.get('updated_at')) < stamp(old_city.get('updated_at')):
            raise ValueError('City timestamp regressed')
    if old_decks is not None:
        if not old_decks['decks'].keys() <= dm.keys():
            raise ValueError('city_decks identity/count decreased')
        if stamp(decks.get('updated_at')) < stamp(old_decks.get('updated_at')):
            raise ValueError('Deck timestamp regressed')
    return dict(event_count=len(ids), placement_count=sum(len(e['placements']) for e in city['events']),
                unique_deck_count=len(dm), valid_60_count=len(dm))


def write_candidate(directory, city, decks):
    directory = Path(directory)
    counts = validate_snapshot(city, decks)
    city.update(event_count=counts['event_count'], placement_count=counts['placement_count'])
    decks.update(unique_deck_count=counts['unique_deck_count'], valid_60_count=counts['valid_60_count'])
    for name, value in [('city_db', city), ('city_decks', decks)]:
        (directory / f'{name}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    index = dict(season=city.get('season', '2027-S1'), source=city.get('source'),
                 source_updated_at=city.get('updated_at'), **counts, status='ok')
    for name, key in [('city_db', 'city_sha256'), ('city_decks', 'decks_sha256')]:
        index[key] = hashlib.sha256((directory / f'{name}.json').read_bytes()).hexdigest()
    (directory / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return counts
