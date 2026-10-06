"""Offline, bounded player evidence JOIN. No player discovery or network access."""

import hashlib
import json
from datetime import date


def _id(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError("player_id must be a digit string; preserve leading zeros")
    return value


def join_players(city, classifications, start, end, history_start, *,
                 previous_start=None, previous_end=None, facts=None, csp_season=None):
    """Return selected Top8 rows with strictly earlier, same-category evidence.

    Counts describe observed public Top8 only. A zero is not absence of results.
    CSP is never inferred from placement points. Facts are scoped observations,
    not a player directory, and must predate the event they annotate.
    """
    start, end, floor = map(date.fromisoformat, (start, end, history_start))
    if not floor <= start <= end:
        raise ValueError("Require history_start <= start <= end")
    if (previous_start is None) != (previous_end is None):
        raise ValueError("Previous season requires both bounds")
    previous = None
    if previous_start is not None:
        a, b = map(date.fromisoformat, (previous_start, previous_end))
        if not floor <= a <= b < start:
            raise ValueError("Previous season must lie within history and before target")
        previous = a, b
    parents = {}
    for item in classifications.get("records", []):
        code = item["deck_code"]
        if code in parents:
            raise ValueError("Duplicate deck classification")
        parents[code] = (item.get("parent_archetype")
                         if item.get("classification_status") in ("classified", "partial") else None)
    rows, seen = [], set()
    for event in city["events"]:
        day = date.fromisoformat(event["date"])
        if not floor <= day <= end:
            continue
        event_id = event["event_id"]
        if not isinstance(event_id, str) or not event_id or event_id in seen:
            raise ValueError("Missing/duplicate event_id")
        seen.add(event_id)
        players = set()
        for placement in event.get("placements", []):
            rank = placement.get("rank")
            if type(rank) is not int or rank < 1:
                raise ValueError("Invalid rank")
            if rank > 8:
                continue
            pid = _id(placement.get("player_id"))
            if pid and pid in players:
                raise ValueError("Duplicate player within event")
            players.add(pid)
            rows.append(dict(event_id=event_id, event_date=event["date"],
                             category=event.get("category"), rank=rank, player_id=pid,
                             deck_code=placement.get("deck_code")))
    selected_ids = {r["player_id"] for r in rows if start <= date.fromisoformat(r["event_date"]) <= end
                    and r["player_id"]}
    # Keep only selected players in the temporary history index.
    history = {pid: [] for pid in selected_ids}
    for row in rows:
        if row["player_id"] in history:
            history[row["player_id"]].append(row)
    if facts is not None and (not isinstance(facts, list) or not csp_season):
        raise ValueError("CSP facts require a list and an explicit target season")
    csp = {}
    for fact in facts or []:
        allowed = {"player_id", "category", "season", "as_of", "verified_on", "csp", "source_url"}
        if set(fact) != allowed:
            raise ValueError("CSP facts require exactly the documented evidence fields")
        pid = _id(fact["player_id"])
        if pid not in selected_ids:
            continue
        as_of, verified = map(date.fromisoformat, (fact["as_of"], fact["verified_on"]))
        if as_of > verified or type(fact["csp"]) is not int or fact["csp"] < 0:
            raise ValueError("Invalid CSP observation")
        if not all(isinstance(fact[k], str) and fact[k].strip()
                   for k in ("season", "category", "source_url")):
            raise ValueError("CSP evidence scope/source required")
        if not fact["source_url"].startswith("https://"):
            raise ValueError("CSP requires HTTPS source")
        key = (pid, fact["category"], fact["season"], fact["as_of"], fact["verified_on"])
        if key in csp:
            raise ValueError("Duplicate/conflicting CSP observation")
        csp[key] = fact
    output = []
    for row in sorted(rows, key=lambda r: (r["event_date"], r["event_id"], r["rank"], r["player_id"] or "")):
        day = date.fromisoformat(row["event_date"])
        if not start <= day <= end:
            continue
        pid, category = row["player_id"], row["category"]
        prior = None if not pid or not category else [
            r for r in history[pid] if r["category"] == category and r["event_date"] < row["event_date"]]
        parent = parents.get(row["deck_code"])
        same = None if prior is None or parent is None else [
            r for r in prior if parents.get(r["deck_code"]) == parent]
        last = None if prior is None or previous is None else [
            r for r in prior if previous[0] <= date.fromisoformat(r["event_date"]) <= previous[1]]
        eligible = [f for f in csp.values() if f["player_id"] == pid and f["category"] == category and f["season"] == csp_season
                    and date.fromisoformat(f["verified_on"]) < day]
        # Never mix seasons or arbitrarily choose conflicting evidence.
        latest = max((f["as_of"] for f in eligible), default=None)
        latest_facts = [f for f in eligible if f["as_of"] == latest]
        output.append({**row, "observed_prior_top8": None if prior is None else len(prior),
                       "observed_previous_season_top8": None if last is None else len(last),
                       "observed_previous_season_best_rank": min((r["rank"] for r in last), default=None) if last is not None else None,
                       "parent_archetype": parent,
                       "observed_same_parent_prior_top8": None if same is None else len(same),
                       "csp_evidence": latest_facts[0] if len(latest_facts) == 1 else None})
    return {"version": "player-join-v1", "scope": "observed_public_top8_same_category",
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "history_start": floor.isoformat(),
            "previous_season": [previous_start, previous_end] if previous else None,
            "input_sha256": {name: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode()).hexdigest()
                             for name, value in (("city", city), ("classifications", classifications), ("facts", facts))},
            "csp_season": csp_season, "records": output}
