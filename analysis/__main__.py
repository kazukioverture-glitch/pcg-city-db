import argparse
import json
from collections import Counter
from pathlib import Path

from .cards import validate_deck
from .state import (ROOT, STATE_FILES, create_snapshot, import_legacy, read_json,
                    read_state, verify_snapshot, write_state)


def main():
    parser = argparse.ArgumentParser(description="v1.2.1 weekly input/state tooling")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-state")
    commands.add_parser("init-ledger", help="Import explicit legacy facts; refuse overwrite")
    cards = commands.add_parser("validate-cards")
    cards.add_argument("--master", type=Path)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--target-week", required=True, help="ISO week, e.g. 2026-W40")
    snapshot.add_argument("--retrieved-at", required=True, help="Actual acquisition timestamp with offset")
    snapshot.add_argument("--event-id", action="append", required=True, dest="event_ids")
    snapshot.add_argument("--file", action="append", dest="files", help="Override default inputs; include state/master used")
    verify = commands.add_parser("verify-snapshot")
    verify.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "validate-state":
        for filename in STATE_FILES.values():
            read_state(ROOT / "data/analysis" / filename)
        print("OK: 5 state documents")
    elif args.command == "init-ledger":
        path = ROOT / "data/analysis/event_ledger.json"
        if path.exists():
            raise FileExistsError("Ledger exists; use append_observation/write_state without discarding history")
        write_state(path, import_legacy(read_json(ROOT / "data/city_db.json")))
    elif args.command == "snapshot":
        print(create_snapshot(ROOT, args.target_week, args.event_ids, args.retrieved_at, args.files))
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
