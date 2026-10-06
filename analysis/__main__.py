import argparse
import json
from collections import Counter
from pathlib import Path

from .cards import validate_deck
from .state import (ROOT, STATE_FILES, create_snapshot, import_legacy, read_json,
                    read_state, verify_snapshot, write_state)


def main():
    parser = argparse.ArgumentParser(description="v1.2.2 weekly input/state tooling")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-state")
    commands.add_parser("init-ledger", help="Import explicit legacy facts; refuse overwrite")
    classify = commands.add_parser("classify-decks", help="Offline date-range classification; preserve snapshots")
    classify.add_argument("--start", required=True)
    classify.add_argument("--end", required=True)
    classify.add_argument("--city", type=Path, default=ROOT / "data/city_db.json")
    classify.add_argument("--decks", type=Path, default=ROOT / "data/city_decks.json")
    classify.add_argument("--output", type=Path, default=ROOT / "data/analysis/deck_classifications.json")
    players = commands.add_parser("join-players", help="Offline selected Top8 evidence JOIN; JSON to stdout only")
    for name in ("start", "end", "history-start"):
        players.add_argument(f"--{name}", required=True)
    players.add_argument("--previous-start")
    players.add_argument("--previous-end")
    players.add_argument("--city", type=Path, default=ROOT / "data/city_db.json")
    players.add_argument("--classifications", type=Path, default=ROOT / "data/analysis/deck_classifications.json")
    players.add_argument("--csp-season", help="Required when CSP facts are supplied")
    players.add_argument("--facts", type=Path, help="Optional verified CSP observations (JSON list)")
    cards = commands.add_parser("validate-cards")
    cards.add_argument("--master", type=Path)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--target-week", help="Legacy ISO week, e.g. 2026-W40")
    snapshot.add_argument("--week-id", help="Custom week identifier, e.g. W02")
    snapshot.add_argument("--period-start")
    snapshot.add_argument("--period-end")
    snapshot.add_argument("--analysis-stage", choices=["INTERIM", "FINAL"])
    snapshot.add_argument("--cutoff-datetime")
    snapshot.add_argument("--retrieved-at", required=True, help="Actual acquisition timestamp with offset")
    snapshot.add_argument("--event-id", action="append", required=True, dest="event_ids")
    snapshot.add_argument("--file", action="append", dest="files", help="Override default inputs; include state/master used")
    final = commands.add_parser("finalize-week", help="Verify and locally commit FINAL inputs; never push")
    for name in ("week-id", "period-start", "period-end", "cutoff-datetime", "retrieved-at"):
        final.add_argument(f"--{name}", required=True)
    final.add_argument("--event-id", action="append", required=True, dest="event_ids")
    verify = commands.add_parser("verify-snapshot")
    verify.add_argument("directory", type=Path)
    sync = commands.add_parser("sync-ledger", help="Record a collection attempt without changing legacy DBs")
    sync.add_argument("--city", type=Path)
    sync.add_argument("--decks", type=Path)
    sync.add_argument("--ledger", type=Path, default=ROOT / "data/analysis/event_ledger.json")
    sync.add_argument("--fetch-status", required=True, choices=["not_attempted", "success", "failed"])
    sync.add_argument("--parse-status", choices=["not_parsed", "success", "partial", "failed"])
    sync.add_argument("--coverage-scope", required=True, choices=["collection_feed", "result_list", "top8"])
    sync.add_argument("--source-url", required=True)
    sync.add_argument("--observed-at")
    args = parser.parse_args()
    if args.command == "join-players":
        from .players import join_players
        result = join_players(read_json(args.city), read_state(args.classifications),
                              args.start, args.end, args.history_start,
                              previous_start=args.previous_start, previous_end=args.previous_end,
                              facts=read_json(args.facts) if args.facts else None, csp_season=args.csp_season)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "classify-decks":
        from .classification import classify_range
        print(json.dumps(classify_range(args.city, args.decks, args.output, args.start, args.end)))
    elif args.command == "validate-state":
        for filename in STATE_FILES.values():
            read_state(ROOT / "data/analysis" / filename)
        print("OK: 5 state documents")
    elif args.command == "init-ledger":
        path = ROOT / "data/analysis/event_ledger.json"
        if path.exists():
            raise FileExistsError("Ledger exists; use append_observation/write_state without discarding history")
        write_state(path, import_legacy(read_json(ROOT / "data/city_db.json")))
    elif args.command == "snapshot":
        print(create_snapshot(ROOT, args.target_week, args.event_ids, args.retrieved_at, args.files,
                              week_id=args.week_id, period_start=args.period_start, period_end=args.period_end,
                              analysis_stage=args.analysis_stage, cutoff_datetime=args.cutoff_datetime))
    elif args.command == "finalize-week":
        from .finalize import finalize_week
        directory, sha = finalize_week(ROOT, week_id=args.week_id,
                                      period_start=args.period_start, period_end=args.period_end,
                                      cutoff_datetime=args.cutoff_datetime,
                                      retrieved_at=args.retrieved_at, event_ids=args.event_ids)
        print(json.dumps({"directory": str(directory), "commit_sha": sha}))
    elif args.command == "sync-ledger":
        from .collection import sync_ledger
        print("OK:", sync_ledger(args.ledger, city_path=args.city, decks_path=args.decks,
                                 fetch_status=args.fetch_status, parse_status=args.parse_status,
                                 coverage_scope=args.coverage_scope, source_url=args.source_url,
                                 observed_at=args.observed_at), "observations appended")
    elif args.command == "verify-snapshot":
        print("OK:", verify_snapshot(args.directory)["snapshot_id"])
    elif args.command == "validate-cards":
        master_document = read_state(args.master) if args.master else None
        if master_document is not None and master_document["kind"] != "card_master":
            raise ValueError("--master requires a card_master document")
        master = master_document["cards"] if master_document else None
        issues = []
        for code, deck in read_json(ROOT / "data/city_decks.json")["decks"].items():
            issues.extend({"deck_code": code, **issue} for issue in validate_deck(deck, master))
        print(json.dumps({"counts": dict(Counter(i["severity"] for i in issues)),
                          "issues": issues}, ensure_ascii=False, indent=2))
        return 1 if any(i["severity"] == "ERROR" for i in issues) else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
