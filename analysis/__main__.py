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
    weekly = commands.add_parser("weekly-report", help="Analyze one immutable weekly snapshot; never mutate inputs")
    weekly.add_argument("--snapshot", type=Path, required=True)
    weekly.add_argument("--category", default="オープン")
    weekly.add_argument("--region", default="愛知県")
    weekly.add_argument("--compare-start")
    weekly.add_argument("--compare-end")
    weekly.add_argument("--watch-cards", type=Path)
    weekly.add_argument("--external-sources", type=Path,
                        help="Optional processed_sources sidecar analysed independently before DB comparison")
    weekly.add_argument("--output-json", type=Path)
    weekly.add_argument("--output-md", type=Path)
    answer = commands.add_parser(
        "materialize-answer-index",
        help="Materialize compact reusable answer artifacts from one immutable FINAL snapshot")
    answer.add_argument("--snapshot", type=Path, required=True)
    answer.add_argument("--category", default="オープン")
    answer.add_argument("--region", default="愛知県")
    answer.add_argument("--compare-start")
    answer.add_argument("--compare-end")
    answer.add_argument("--watch-cards", type=Path)
    answer.add_argument("--external-sources", type=Path)
    answer.add_argument("--output-root", type=Path,
                        default=ROOT / "data/analysis/answer_index")
    discover = commands.add_parser("discover-cards", help="Literal candidate ID discovery; never merge identities")
    discover.add_argument("--snapshot", type=Path, required=True)
    discover.add_argument("--name", action="append", required=True, dest="names")
    source = commands.add_parser("record-source-analysis",
                                 help="Append one independently analysed external source; no fetching")
    source.add_argument("record", type=Path)
    source.add_argument("--output", type=Path, default=ROOT / "data/analysis/processed_sources.json")
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

    if args.command == "materialize-answer-index":
        from .answer_index import materialize_answer_index
        watch = read_json(args.watch_cards) if args.watch_cards else None
        external = read_state(args.external_sources) if args.external_sources else None
        result = materialize_answer_index(
            args.snapshot, output_root=args.output_root,
            category=args.category, region=args.region,
            compare_start=args.compare_start, compare_end=args.compare_end,
            watch_config=watch, external_sources=external)
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "weekly-report":
        from .weekly import analyze_snapshot, render_markdown
        watch = read_json(args.watch_cards) if args.watch_cards else None
        external = read_state(args.external_sources) if args.external_sources else None
        result = analyze_snapshot(
            args.snapshot, category=args.category, region=args.region,
            compare_start=args.compare_start, compare_end=args.compare_end,
            watch_config=watch, external_sources=external)
        json_text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        md_text = render_markdown(result)

        def save(path, content):
            if path is None:
                return
            resolved = path.resolve()
            snapshot_root = args.snapshot.resolve()
            if resolved == snapshot_root or resolved.is_relative_to(snapshot_root):
                raise ValueError("Report outputs cannot be written inside immutable snapshot inputs")
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")

        save(args.output_json, json_text)
        save(args.output_md, md_text)
        if args.output_json is None and args.output_md is None:
            print(json_text, end="")
        else:
            print(json.dumps({"snapshot_id": result["snapshot_id"],
                              "audit_status": result["audit"]["status"],
                              "output_json": str(args.output_json) if args.output_json else None,
                              "output_md": str(args.output_md) if args.output_md else None},
                             ensure_ascii=False))
    elif args.command == "discover-cards":
        from .weekly import discover_card_identities
        verify_snapshot(args.snapshot)
        decks = read_json(args.snapshot / "inputs/data/city_decks.json")
        print(json.dumps(discover_card_identities(decks, args.names), ensure_ascii=False, indent=2))
    elif args.command == "record-source-analysis":
        from .sources import append_source_analysis
        print(json.dumps(append_source_analysis(args.record, args.output), ensure_ascii=False))
    elif args.command == "join-players":
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
