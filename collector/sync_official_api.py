#!/usr/bin/env python3
"""Backfill published City League Top 16 results from official public endpoints.

Fallback/repair path for a stale or unavailable Termux/ngrok snapshot.
Existing records are preserved; only newly published events/decks are added.
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import re
import time
import threading
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from curl_cffi import requests as cffi_requests

BASE = "https://players.pokemon-card.com"
RESULT_LIST_URL = f"{BASE}/event/result/list"
EVENT_SEARCH_URL = f"{BASE}/event_search"
RESULT_DETAIL_URL = f"{BASE}/event_result_detail_search"
DECK_URL = "https://www.pokemon-card.com/deck/confirm.html/deckID/{code}"

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TMP = ROOT / ".tmp"
CITY_PATH = DATA / "city_db.json"
DECKS_PATH = DATA / "city_decks.json"
INDEX_PATH = DATA / "index.json"
PARTIAL_CITY_PATH = TMP / "official_backfill_city.json"
PARTIAL_DECKS_PATH = TMP / "official_backfill_decks.json"
CHANGED_FLAG = TMP / "official_backfill.changed"

JST = timezone(timedelta(hours=9))
_HTTP = threading.local()
_REQUEST_LOCK = threading.Lock()
_NEXT_REQUEST = 0.0
_RATE_LIMITED = False


def _wait_for_request():
    """Space all workers' requests one second apart, including retries."""
    global _NEXT_REQUEST
    with _REQUEST_LOCK:
        if _RATE_LIMITED:
            raise RuntimeError('Official source unavailable: rate limited; retry on next scheduled run')
        delay = _NEXT_REQUEST - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _NEXT_REQUEST = time.monotonic() + 1.0


def _get_session():
    """Keep independent browser-compatible HTTP sessions for each worker."""
    session = getattr(_HTTP, 'session', None)
    if session is None:
        session = cffi_requests.Session(impersonate='chrome')
        _HTTP.session = session
    return session


def now_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_bytes(url: str, *, attempts: int = 4, timeout: int = 25) -> bytes:
    global _RATE_LIMITED
    headers = {'Accept-Language': 'ja-JP,ja;q=0.9,en;q=0.8'}
    if url.startswith(BASE + '/'):
        headers.update({'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest',
                        'Referer': RESULT_LIST_URL})
    else:
        headers['Accept'] = 'text/html,application/xhtml+xml,*/*;q=0.8'
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            _wait_for_request()
            response = _get_session().get(url, headers=headers, timeout=timeout)
        except cffi_requests.RequestsError as exc:
            last_error = exc
        else:
            status = int(response.status_code)
            if status == 200:
                return response.content
            print(f'Official HTTP {status}, attempt {attempt}/{attempts}: {url}', flush=True)
            if status in (401, 403):
                raise RuntimeError(f'Official source unavailable: HTTP {status}: {url}')
            if status == 429:
                # Stop this source instead of continuing queued requests into a rate limit.
                with _REQUEST_LOCK:
                    _RATE_LIMITED = True
                raise RuntimeError(f'Official source unavailable: HTTP 429; retry on next scheduled run: {url}')
            last_error = RuntimeError(f'HTTP {status}: {url}')
            if status not in (500, 502, 503, 504):
                raise last_error
        if attempt < attempts:
            time.sleep(min(2 ** (attempt - 1), 5))
    raise RuntimeError(f'Failed after {attempts} attempts: {url}: {last_error}') from last_error


def fetch_json(url: str, params: dict) -> dict:
    payload = json.loads(fetch_bytes(f"{url}?{urlencode(params, doseq=True)}").decode("utf-8"))
    if not isinstance(payload, dict) or payload.get('code') != 200:
        raise ValueError(f"Invalid official API response: {url}")
    return payload


def strip_html(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return html_lib.unescape(fragment).strip()


def parse_deck(code: str) -> dict:
    url = DECK_URL.format(code=code)
    page = fetch_bytes(url).decode("utf-8", errors="replace")
    cards = []
    table_pattern = re.compile(
        r'<table[^>]*class=["\'][^"\']*KSTable[^"\']*["\'][^>]*>(.*?)</table>',
        re.I | re.S,
    )
    for table_match in table_pattern.finditer(page):
        table = table_match.group(1)
        heading = re.search(r"<th[^>]*>(.*?)</th>", table, flags=re.I | re.S)
        if not heading:
            continue
        section = strip_html(heading.group(1))
        for row in re.finditer(r"<tr[^>]*>(.*?)</tr>", table, flags=re.I | re.S):
            body = row.group(1)
            card = re.search(r'id=["\']cardName_(\d+)["\'][^>]*>(.*?)</a>', body, flags=re.I | re.S)
            if not card:
                continue
            quantity = re.search(r"<span[^>]*>\s*(\d+)\s*枚\s*</span>", body, flags=re.I | re.S)
            if not quantity:
                continue
            cards.append({
                "name": strip_html(card.group(2)),
                "set_code": None,
                "card_number": None,
                "card_id": card.group(1),
                "count": int(quantity.group(1)),
                "section": section,
            })

    if not cards:
        # Official confirm pages render the table in JavaScript. Parse the
        # public embedded deck inputs and name assignments without executing JS.
        class Inputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = {}

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == 'input' and str(values.get('name', '')).startswith('deck_'):
                    self.values[values['name']] = values.get('value', '')

        inputs = Inputs()
        inputs.feed(page)
        def decode_js(value):
            value = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m[1], 16)), value)
            return html_lib.unescape(re.sub(r"\\([\\'\"])", r'\1', value))
        names = {m[1]: decode_js(m[2]) for m in re.finditer(
            r"PCGDECK\.searchItemNameAlt\[(\d+)\]\s*=\s*'((?:\\.|[^'\\])*)'", page)}
        sections = {'pke': 'ポケモン', 'gds': 'グッズ', 'tool': 'ポケモンのどうぐ',
                    'tech': 'ポケモンのどうぐ', 'sup': 'サポート', 'sta': 'スタジアム',
                    'ene': 'エネルギー', 'ajs': 'その他'}
        for field, value in inputs.values.items():
            if not value:
                continue
            kind = field.removeprefix('deck_')
            if kind not in sections:
                raise ValueError(f'{code}: unknown deck section {field}')
            entries = []
            for entry in value.split('-'):
                match = re.fullmatch(r'(\d+)_(\d+)_(\d+)', entry)
                if not match or match[1] not in names:
                    raise ValueError(f'{code}: invalid embedded card {entry}')
                card_id, count = match[1], int(match[2])
                if count <= 0:
                    raise ValueError(f'{code}: invalid card quantity')
                entries.append({'name': names[card_id], 'set_code': None, 'card_number': None,
                                'card_id': card_id, 'count': count})
            section = f"{sections[kind]} ({sum(c['count'] for c in entries)})"
            cards.extend({**entry, 'section': section} for entry in entries)

    total = sum(card["count"] for card in cards)
    if total != 60 or not cards:
        raise ValueError(f"{code}: parsed {total} cards, expected 60")
    return {
        "deck_code": code,
        "deck_url": url,
        "total_cards": 60,
        "unique_cards": len(cards),
        "cards": cards,
        "usages": [],
        "collected_at": now_jst(),
    }


def result_event_from_api(event_id: str) -> dict | None:
    per_page = 8
    offset = 0
    event = {}
    top_rows = []
    seen_pages = set()

    # Preserve ties; stop after reaching a rank outside the Top16 range.
    while True:
        payload = fetch_json(RESULT_DETAIL_URL, {
            "event_holding_id": event_id,
            "offset": offset,
            "per_page": per_page,
        })
        event = event or (payload.get("event") or {})
        results = payload.get("results")
        if not isinstance(results, list):
            raise ValueError(f"Invalid results structure: {event_id}")
        if not results:
            break
        page_key = json.dumps(results, sort_keys=True, ensure_ascii=False)
        if page_key in seen_pages or offset > 10000:
            raise ValueError(f"Non-advancing result pagination: {event_id}")
        seen_pages.add(page_key)

        reached_beyond_top16 = False
        for item in results:
            try:
                rank = int(item.get("rank"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid rank: {event_id}") from exc
            if 1 <= rank <= 16:
                top_rows.append(item)
            else:
                reached_beyond_top16 = True
                break

        total_count = int(payload.get("count") or 0)
        offset += len(results)
        if reached_beyond_top16 or len(results) < per_page or (total_count and offset >= total_count):
            break

    if not top_rows:
        return None

    date_value = ((event.get("eventDate") or {}).get("date") or "")[:10]
    category = event.get("league")
    placements = []
    for item in top_rows:
        deck_code = item.get("deck_id") or None
        player_id = str(item.get("player_id") or "")
        area = item.get("area")
        rank = int(item.get("rank"))
        points = int(item.get("point"))
        name = item.get("name")
        placements.append({
            "rank": rank,
            "points": points,
            "player_name": name,
            "player_id": player_id,
            "prefecture": area,
            "player_url": f"{BASE}/users/{player_id}" if item.get("show_profile") and player_id else None,
            "deck_code": deck_code,
            "deck_url": DECK_URL.format(code=deck_code) if deck_code else None,
            "raw_text": f"{rank} 位 {points}pt\n{name}\nプレイヤーID：{player_id}\n{area} デッキ\nをみる",
        })
    return {
        "event_id": str(event_id),
        "date": date_value,
        "category": category,
        "result_url": f"{BASE}/event/detail/{event_id}/result",
        "placement_count": len(placements),
        "placements": placements,
        "top16_observed_rows": sum(8 < p["rank"] <= 16 for p in placements),
        "top16_checked_at": now_jst(),
        "collected_at": now_jst(),
    }


def discover_recent_city_events(floor_date: str, max_pages: int) -> list[dict]:
    found = {}
    per_page = 200
    offset = 0
    seen_pages = set()
    for _ in range(max_pages):
        payload = fetch_json(EVENT_SEARCH_URL, {
            "offset": offset,
            "limit": per_page,
            "order": 4,
            "result_resist": 1,
            "event_type[]": ["3:2"],
        })
        rows = payload.get("event")
        if not isinstance(rows, list):
            raise ValueError('Invalid discovery structure')
        if not rows:
            break
        page_key = tuple(str(row.get("event_holding_id") or "") for row in rows)
        if page_key in seen_pages:
            raise ValueError("Non-advancing event discovery pagination")
        seen_pages.add(page_key)
        valid_dates = []
        for row in rows:
            ymd = str(row.get("event_date_params") or "")
            if len(ymd) != 8 or not ymd.isdigit():
                continue
            iso_date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
            valid_dates.append(iso_date)
            title = str(row.get("event_title") or "")
            if iso_date < floor_date:
                continue
            if row.get("event_type") != 2 or not title.startswith("シティリーグ2027"):
                continue
            venue_name = row.get("shop_name")
            venue_prefecture = row.get("prefecture_name")
            found[str(row["event_holding_id"])] = {
                "event_id": str(row["event_holding_id"]),
                "date": iso_date,
                "category": row.get("leagueName"),
                "venue_name": venue_name.strip() if isinstance(venue_name, str) and venue_name.strip() else None,
                "venue_prefecture": (venue_prefecture.strip()
                                      if isinstance(venue_prefecture, str) and venue_prefecture.strip() else None),
            }
        offset += len(rows)
        total = int(payload.get("eventCount") or 0)
        if total and offset >= total:
            break
        if valid_dates and min(valid_dates) < floor_date:
            break
    else:
        raise RuntimeError("Official discovery page limit reached before season boundary")
    return sorted(found.values(), key=lambda x: (x["date"], x["event_id"]))


def usage_from(event: dict, placement: dict) -> dict:
    return {
        "event_id": event["event_id"],
        "date": event.get("date"),
        "category": event.get("category"),
        "rank": placement.get("rank"),
        "player_name": placement.get("player_name"),
        "player_id": placement.get("player_id"),
    }


def usage_key(usage: dict) -> tuple:
    return (usage.get("event_id"), usage.get("rank"), usage.get("player_id"))


def update_index(city: dict, decks: dict) -> None:
    index = {
        "season": city.get("season", "2027-S1"),
        "source": city.get("source") or RESULT_LIST_URL,
        "source_updated_at": city.get("updated_at"),
        "event_count": len(city.get("events") or []),
        "placement_count": city.get("placement_count"),
        "unique_deck_count": len(decks.get("decks") or {}),
        "valid_60_count": sum(
            1 for deck in (decks.get("decks") or {}).values()
            if isinstance(deck, dict) and deck.get("total_cards") == 60
        ),
        "city_sha256": hashlib.sha256(CITY_PATH.read_bytes()).hexdigest(),
        "decks_sha256": hashlib.sha256(DECKS_PATH.read_bytes()).hexdigest(),
        "status": "ok",
    }
    write_json(INDEX_PATH, index)


def select_event_candidates(discovered, stored_events, *, today=None, limit=24):
    """Bounded queue: fresh events first, then incomplete or never-checked events."""
    if limit < 1:
        raise ValueError("max-detail-events must be positive")
    day = today or datetime.now(JST).date()
    cutoff = (day - timedelta(days=2)).isoformat()
    chosen = []
    for candidate in discovered:
        event_date = candidate.get("date") or ""
        if not event_date or event_date > day.isoformat():
            continue
        old = stored_events.get(candidate["event_id"])
        old_rows = (old or {}).get("placements") or []
        captured = sum(8 < p.get("rank", 0) <= 16 for p in old_rows)
        outstanding = bool(old and (old.get("top16_observed_rows") or 0) > captured)
        recent = event_date >= cutoff
        if not (recent or old is None or not old.get("top16_checked_at") or outstanding):
            continue
        missing_core = old is None or not any(p.get("rank", 99) <= 8 for p in old_rows)
        # Always finish unfinished recent events before polling already-complete ones.
        # During backfill, unverified older events must outrank already-checked
        # recent events; otherwise a busy weekend starves historical Top16.
        priority = (0 if recent and missing_core else
                    1 if recent and (outstanding or not old.get("top16_checked_at")) else
                    2 if missing_core else
                    3 if outstanding or not old.get("top16_checked_at") else 4)
        chosen.append((priority, -int(event_date.replace("-", "")), candidate["event_id"], candidate))
    chosen.sort()
    return [x[3] for x in chosen[:limit]]


def main() -> int:
    global DATA, TMP, CITY_PATH, DECKS_PATH, INDEX_PATH, PARTIAL_CITY_PATH, PARTIAL_DECKS_PATH, CHANGED_FLAG
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--deck-workers", type=int, default=8)
    parser.add_argument("--max-detail-events", type=int, default=24)
    parser.add_argument("--max-top16-decks", type=int, default=32)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--tmp-dir", type=Path)
    args = parser.parse_args()
    if args.data_dir:
        DATA = args.data_dir
        CITY_PATH, DECKS_PATH, INDEX_PATH = [DATA / f"{name}.json" for name in ("city_db", "city_decks", "index")]
    if args.tmp_dir:
        TMP = args.tmp_dir
        PARTIAL_CITY_PATH = TMP / "official_backfill_city.json"
        PARTIAL_DECKS_PATH = TMP / "official_backfill_decks.json"
        CHANGED_FLAG = TMP / "official_backfill.changed"

    TMP.mkdir(parents=True, exist_ok=True)
    for path in (PARTIAL_CITY_PATH, PARTIAL_DECKS_PATH, CHANGED_FLAG):
        path.unlink(missing_ok=True)

    city = read_json(CITY_PATH)
    decks = read_json(DECKS_PATH)
    event_rows = city.get("events")
    deck_map = decks.get("decks")
    if not isinstance(event_rows, list) or not isinstance(deck_map, dict):
        raise ValueError("Stored DB has invalid structure")

    try:
        from collector.merge_termux_snapshot import valid_deck
    except ModuleNotFoundError:
        from merge_termux_snapshot import valid_deck
    if not all(valid_deck(deck) for deck in deck_map.values()):
        raise ValueError("Stored DB contains invalid 60-card deck")

    existing_ids = {str(event.get("event_id")) for event in event_rows}
    existing_dates = [
        event.get("date") for event in event_rows
        if isinstance(event, dict) and isinstance(event.get("date"), str)
    ]
    floor_date = city.get("season_start", "2026-09-26")
    discovered = discover_recent_city_events(floor_date, args.max_pages)
    stored_events = {str(e["event_id"]): e for e in event_rows}
    selected = select_event_candidates(discovered, stored_events, limit=args.max_detail_events)
    print(f"Discovered {len(discovered)} official City League events; checking {len(selected)} priority events", flush=True)
    new_events = []
    checked_events = []
    errors = {}
    with ThreadPoolExecutor(max_workers=max(1, args.deck_workers)) as pool:
        futures = {pool.submit(result_event_from_api, row["event_id"]): row for row in selected}
        for future in as_completed(futures):
            candidate = futures[future]
            try:
                event = future.result()
            except Exception as exc:
                errors[candidate["event_id"]] = str(exc)
                continue
            checked_events.append({**candidate, "official_placement_count": len(event["placements"]) if event else 0,
                                   "status": "published" if event else "result unpublished/empty"})
            if len(checked_events) % 20 == 0:
                print(f"Checked official results: {len(checked_events)}/{len(selected)}", flush=True)
            if event is None:
                print(f"Skip unpublished/empty result: {candidate['event_id']}")
                continue
            event["date"] = event.get("date") or candidate["date"]
            event["category"] = event.get("category") or candidate["category"]
            for key in ("venue_name", "venue_prefecture"):
                if candidate.get(key) is not None:
                    event[key] = candidate[key]
            old = stored_events.get(event["event_id"])
            if old:
                # Preserve historic rows when the endpoint temporarily publishes fewer rows.
                old_rows = {str(p.get("player_id") or (p.get("player_name"), p.get("deck_code"))): p
                            for p in old.get("placements", [])}
                old_rows.update({str(p.get("player_id") or (p.get("player_name"), p.get("deck_code"))): p
                                 for p in event["placements"]})
                event["placements"] = list(old_rows.values())
                event["placement_count"] = len(event["placements"])
                event["top16_observed_rows"] = max(
                    old.get("top16_observed_rows") or 0, event["top16_observed_rows"])
                event["top16_checked_at"] = old.get("top16_checked_at") or event["top16_checked_at"]
                event = {**old, **event}
                same_rows = (
                    sorted(old.get("placements", []), key=lambda p: str(p.get("player_id")))
                    == sorted(event["placements"], key=lambda p: str(p.get("player_id")))
                )
                same_metadata = all(
                    old.get(key) == event.get(key)
                    for key in ("date", "category", "venue_name", "venue_prefecture")
                )
                if same_rows and same_metadata and old.get("top16_checked_at"):
                    continue
            new_events.append(event)
    write_json(TMP / "official_audit.json", {"checked_at": now_jst(), "floor_date": floor_date,
               "events": sorted(checked_events, key=lambda e: (e["date"], e["event_id"])),
               "selected_event_count": len(selected), "discovered_event_count": len(discovered),
               "fetch_errors": errors})
    print(f"Official discovery: {len(discovered)} city events on/after {floor_date}; {len(new_events)} new/updated")
    if errors and not new_events:
        raise RuntimeError(f"Official detail fetch failure without usable updates; no main files written: {errors}")
    if errors:
        print(f"::warning::Partial official collection; retaining failed event records: {errors}", flush=True)
    if not new_events:
        print("No official backfill needed")
        return 0

    # Top8 lists are mandatory; 9-16 are bounded and best-effort.
    mandatory_codes, optional_codes = set(), set()
    for event in new_events:
        for placement in event["placements"]:
            code = placement.get("deck_code")
            if code:
                (mandatory_codes if placement["rank"] <= 8 else optional_codes).add(code)
    required = sorted(mandatory_codes - deck_map.keys())
    optional = sorted(optional_codes - deck_map.keys() - mandatory_codes)
    if args.max_top16_decks < 0:
        raise ValueError("max-top16-decks must be nonnegative")
    optional_now = optional[:args.max_top16_decks]
    deferred = set(optional[args.max_top16_decks:])

    def fetch_decks(codes):
        parsed, failures = {}, {}
        with ThreadPoolExecutor(max_workers=max(1, args.deck_workers)) as pool:
            futures = {pool.submit(parse_deck, code): code for code in codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    parsed[code] = future.result()
                except Exception as exc:
                    failures[code] = str(exc)
        return parsed, failures

    core_parsed, core_errors = fetch_decks(required)
    if core_errors:
        detail = "; ".join(f"{code}: {reason}" for code, reason in sorted(core_errors.items()))
        raise RuntimeError(f"Top8 deck collection failed; stored DB preserved: {detail}")
    extra_parsed, extra_errors = fetch_decks(optional_now)
    parsed_decks = {**core_parsed, **extra_parsed}
    deferred.update(extra_errors)
    available = set(deck_map) | set(parsed_decks)
    for event in new_events:
        event["placements"] = [
            p for p in event["placements"]
            if p["rank"] <= 8 or not p.get("deck_code") or p["deck_code"] in available
        ]
        event["placement_count"] = len(event["placements"])
        event["top16_captured_rows"] = sum(8 < p["rank"] <= 16 for p in event["placements"])
    needed_codes = {}
    for event in new_events:
        for placement in event["placements"]:
            code = placement.get("deck_code")
            if code:
                needed_codes.setdefault(code, []).append(usage_from(event, placement))
    print(f"Top8 new decks: {len(required)}, Top16 fetched: {len(extra_parsed)}, "
          f"Top16 deferred/failed: {len(deferred)}", flush=True)

    audit = read_json(TMP / 'official_audit.json')
    audit['new_deck_codes'] = sorted(parsed_decks)
    audit['top16_deferred_codes'] = sorted(deferred)
    audit['top16_fetch_errors'] = extra_errors
    audit['top16_observed_rows'] = sum(e.get("top16_observed_rows", 0) for e in new_events)
    audit['top16_captured_rows'] = sum(e.get("top16_captured_rows", 0) for e in new_events)
    write_json(TMP / 'official_audit.json', audit)

    for code, usages in needed_codes.items():
        if code not in deck_map:
            deck_map[code] = parsed_decks[code]
        deck = deck_map[code]
        keys = {usage_key(u) for u in deck.get("usages", [])}
        for usage in usages:
            if usage_key(usage) not in keys:
                deck.setdefault("usages", []).append(usage)
                keys.add(usage_key(usage))

    replacements = {event["event_id"]: event for event in new_events}
    event_rows[:] = [replacements.pop(str(event["event_id"]), event) for event in event_rows]
    event_rows.extend(replacements.values())
    event_rows.sort(key=lambda e: (e.get("date") or "", str(e.get("event_id") or "")), reverse=True)

    stamp = now_jst()
    city["source"] = city.get("source") or RESULT_LIST_URL
    city["updated_at"] = stamp
    city["event_count"] = len(event_rows)
    city["placement_count"] = sum(
        len(event.get("placements") or []) for event in event_rows if isinstance(event, dict)
    )
    decks["updated_at"] = stamp
    decks["unique_deck_count"] = len(deck_map)
    decks["valid_60_count"] = sum(
        1 for deck in deck_map.values() if isinstance(deck, dict) and deck.get("total_cards") == 60
    )

    # No main DB writes occur until all required new decks have parsed and validated to 60 cards.
    write_json(CITY_PATH, city)
    write_json(DECKS_PATH, decks)
    update_index(city, decks)

    partial_codes = sorted(needed_codes)
    partial_decks = {code: deck_map[code] for code in partial_codes if code in deck_map}
    write_json(PARTIAL_CITY_PATH, {
        "season": city.get("season", "2027-S1"),
        "season_start": city.get("season_start", "2026-09-26"),
        "updated_at": stamp,
        "event_count": len(new_events),
        "placement_count": sum(e["placement_count"] for e in new_events),
        "source": RESULT_LIST_URL,
        "events": new_events,
    })
    write_json(PARTIAL_DECKS_PATH, {
        "season": decks.get("season", "2027-S1"),
        "updated_at": stamp,
        "unique_deck_count": len(partial_decks),
        "valid_60_count": sum(1 for d in partial_decks.values() if d.get("total_cards") == 60),
        "decks": partial_decks,
    })
    CHANGED_FLAG.write_text("changed\n", encoding="utf-8")

    print(
        f"Backfill complete: +{len(new_events)} events, "
        f"+{sum(e['placement_count'] for e in new_events)} placements, "
        f"+{len(parsed_decks)} new deck codes"
    )
    print(
        f"Totals: {city['event_count']} events, {city['placement_count']} placements, "
        f"{decks['unique_deck_count']} unique decks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
