#!/usr/bin/env python3
"""Collect only the minimal City League schedule fields needed for result linkage."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from collector.sync_official_api import EVENT_SEARCH_URL, JST, fetch_json, now_jst

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / ".tmp/public/city-schedule.json"


def default_target_date() -> str:
    now = datetime.now(JST)
    target = now.date() + timedelta(days=1 if now.hour >= 20 else 0)
    return target.isoformat()


def discover_schedule(target_date: str, event_type: str = "3:2", max_pages: int = 20) -> list[dict]:
    found = {}
    offset = 0
    seen_pages = set()
    for _ in range(max_pages):
        payload = fetch_json(EVENT_SEARCH_URL, {
            "start_date": target_date,
            "end_date": target_date,
            "offset": offset,
            "order": 1,
            "event_type[]": [event_type],
        })
        rows = payload.get("event")
        if not isinstance(rows, list):
            raise ValueError("Invalid official schedule structure")
        if not rows:
            break
        page_key = tuple(str(row.get("event_holding_id") or "") for row in rows)
        if page_key in seen_pages:
            raise ValueError("Non-advancing schedule pagination")
        seen_pages.add(page_key)
        for row in rows:
            ymd = str(row.get("event_date_params") or "")
            if len(ymd) != 8 or not ymd.isdigit():
                continue
            iso_date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
            if iso_date != target_date:
                continue
            title = str(row.get("event_title") or "")
            if not title.startswith("シティリーグ2027"):
                continue
            event_id = str(row.get("event_holding_id") or "")
            if not event_id:
                continue
            venue_name = row.get("shop_name")
            venue_prefecture = row.get("prefecture_name")
            found[event_id] = {
                "event_id": event_id,
                "date": iso_date,
                "venue_name": venue_name.strip() if isinstance(venue_name, str) and venue_name.strip() else None,
                "venue_prefecture": (venue_prefecture.strip()
                                      if isinstance(venue_prefecture, str) and venue_prefecture.strip() else None),
            }
        offset += len(rows)
        total = int(payload.get("eventCount") or 0)
        if total and offset >= total:
            break
    else:
        raise RuntimeError("Official schedule page limit reached")
    return sorted(found.values(), key=lambda e: e["event_id"])


def write_snapshot(path: Path, target_date: str, events: list[dict]) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = None
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    if (isinstance(previous, dict) and previous.get("target_date") == target_date
            and previous.get("events") == events):
        return previous
    payload = {
        "source": EVENT_SEARCH_URL,
        "updated_at": now_jst(),
        "target_date": target_date,
        "event_count": len(events),
        "events": events,
    }
    part = path.with_suffix(path.suffix + ".part")
    part.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(part.read_text(encoding="utf-8"))
    part.replace(path)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=default_target_date())
    parser.add_argument("--event-type", default="3:2")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    events = discover_schedule(args.date, args.event_type, args.max_pages)
    payload = write_snapshot(args.output, args.date, events)
    print(json.dumps({
        "target_date": payload["target_date"],
        "event_count": payload["event_count"],
        "venue_known": sum(bool(e.get("venue_name") and e.get("venue_prefecture")) for e in payload["events"]),
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
