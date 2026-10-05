#!/usr/bin/env python3
"""Stage a monotonic Termux merge for the existing count-decrease validator."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def stamp(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    return parsed


def valid_deck(deck):
    return (isinstance(deck, dict) and deck.get('total_cards') == 60
            and isinstance(deck.get('cards'), list) and bool(deck['cards'])
            and all(isinstance(c.get('count'), int) and c['count'] > 0 for c in deck['cards'])
            and sum(c['count'] for c in deck['cards']) == 60)


def merge_records(stored, incoming, key, old_time, new_time):
    result = {key(row): copy.deepcopy(row) for row in stored}
    for row in incoming:
        identity = key(row)
        old = result.get(identity)
        if old is None or stamp(row.get('collected_at') or new_time) >= stamp(old.get('collected_at') or old_time):
            result[identity] = copy.deepcopy(row)
    return list(result.values())


def merge_snapshot(old_city, old_decks, new_city, new_decks):
    for city in (old_city, new_city):
        if not isinstance(city.get('events'), list):
            raise ValueError('Invalid city DB')
        ids = [str(e['event_id']) for e in city['events']]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate event identity')
    for decks in (old_decks, new_decks):
        if not isinstance(decks.get('decks'), dict) or not all(valid_deck(d) for d in decks['decks'].values()):
            raise ValueError('Invalid 60-card deck')
    if not new_city['events'] or not new_decks['decks']:
        raise ValueError('Empty incoming snapshot')
    for key in ('season', 'season_start'):
        if old_city.get(key) and new_city.get(key) and old_city[key] != new_city[key]:
            raise ValueError('Snapshot season mismatch')
    ot, nt = old_city.get('updated_at'), new_city.get('updated_at')
    newer = stamp(nt) >= stamp(ot)
    city = copy.deepcopy(new_city if newer else old_city)
    events = merge_records(old_city['events'], new_city['events'], lambda e: str(e['event_id']), ot, nt)
    old_map = {str(e['event_id']): e for e in old_city['events']}
    new_map = {str(e['event_id']): e for e in new_city['events']}
    for event in events:
        identity = str(event['event_id'])
        if identity in old_map and identity in new_map:
            old, new = old_map[identity], new_map[identity]
            # Player identity is stable when a rank changes; equal ranks alone are not identities.
            def placement_key(p):
                return str(p.get('player_id') or (p.get('player_name'), p.get('deck_code')))
            event['placements'] = merge_records(old.get('placements', []), new.get('placements', []), placement_key,
                old.get('collected_at') or ot, new.get('collected_at') or nt)
        event['placement_count'] = len(event.get('placements', []))
    city.update(events=sorted(events, key=lambda e: (e.get('date', ''), str(e['event_id'])), reverse=True),
                event_count=len(events), placement_count=sum(e['placement_count'] for e in events),
                updated_at=max((ot, nt), key=stamp))
    odt, ndt = old_decks.get('updated_at'), new_decks.get('updated_at')
    decks = copy.deepcopy(new_decks if stamp(ndt) >= stamp(odt) else old_decks)
    merged = copy.deepcopy(old_decks['decks'])
    for code, new in new_decks['decks'].items():
        old = merged.get(code)
        if old is None:
            merged[code] = copy.deepcopy(new)
            continue
        chosen = new if stamp(new.get('collected_at') or ndt) >= stamp(old.get('collected_at') or odt) else old
        merged[code] = copy.deepcopy(chosen)
        merged[code]['usages'] = merge_records(old.get('usages', []), new.get('usages', []),
            lambda u: (str(u.get('event_id')), str(u.get('player_id') or u.get('player_name'))), odt, ndt)
    decks.update(decks=merged, unique_deck_count=len(merged), valid_60_count=len(merged), updated_at=max((odt, ndt), key=stamp))
    assert city['event_count'] >= len(old_city['events'])
    assert city['placement_count'] >= sum(len(e.get('placements', [])) for e in old_city['events'])
    assert len(merged) >= len(old_decks['decks'])
    return city, decks


def main():
    def read(path):
        return json.loads((ROOT / path).read_text(encoding='utf-8'))
    city, decks = merge_snapshot(read('data/city_db.json'), read('data/city_decks.json'),
                                 read('.tmp/city_db.json'), read('.tmp/city_decks.json'))
    for name, data in (('city_db', city), ('city_decks', decks)):
        (ROOT / f'.tmp/{name}.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Merged safely: {city['event_count']} events, {city['placement_count']} placements, {len(decks['decks'])} decks")


if __name__ == '__main__':
    main()
