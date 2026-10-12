#!/data/data/com.termux/files/usr/bin/bash
# Repair the Android collection SOURCE, not the GitHub transport.
# Run: bash scripts/termux_refresh.sh once
# Opt-in recurring runs: bash scripts/termux_refresh.sh start
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p .tmp/public .tmp
RUN_LOG="$ROOT/.tmp/termux-refresh.last.log"
DAEMON_LOG="$ROOT/.tmp/termux-refresh-loop.log"
PID_FILE="$ROOT/.tmp/termux-refresh-loop.pid"
LOCK_DIR="$ROOT/.tmp/termux-refresh.lock"
SCRIPT="$ROOT/scripts/termux_refresh.sh"

show_state() {
  python - <<'PY'
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
data=json.loads(Path("data/city_db.json").read_text(encoding="utf-8"))
events=data.get("events", [])
day="2026-10-11"
w03=[e for e in events if day >= (e.get("date") or "") >= "2026-10-05" and e.get("category") == "オープン"]
d11=[e for e in w03 if e.get("date")==day]
print(json.dumps({
  "source_updated_at":data.get("updated_at"),
  "latest_observed_event_day":max((e.get("date") or "" for e in events), default=None),
  "W03_open_observed_events":len(w03),
  "Oct11_open_observed_events":len(d11),
  "Oct11_open_captured_top8_rows":sum(1 for e in d11 for p in e.get("placements",[]) if type(p.get("rank")) is int and p["rank"]<=8),
  "full_coverage_verified":False,
},ensure_ascii=False))
PY
}

refresh_once() {
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "ERROR: another Termux refresh is running, or stale lock exists: $LOCK_DIR" >&2
    return 2
  fi
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
  echo "SOURCE BEFORE:"
  show_state

  # The Android SOURCE must fetch official data, never pull its own published feed.
  unset TERMUX_BASE_URL
  python -c "import curl_cffi, jsonschema" || {
    echo "ERROR: dependencies unavailable. Existing DB has not been changed." >&2
    return 2
  }
  echo "Running Android official collection. No GitHub or old FINAL files are touched."
  if ! python -m collector.sync > "$RUN_LOG" 2>&1; then
    tail -n 35 "$RUN_LOG" >&2
    echo "ERROR: Android official collection failed; original DB retained." >&2
    return 1
  fi
  tail -n 8 "$RUN_LOG"

  # A collector can succeed with no new published official results.
  # Verify both local DB files before rewiring the already-running HTTP server.
  python - <<'PY'
import json
from pathlib import Path
from collector.validate_snapshot import validate_snapshot
city=json.loads(Path("data/city_db.json").read_text(encoding="utf-8"))
decks=json.loads(Path("data/city_decks.json").read_text(encoding="utf-8"))
validate_snapshot(city,decks)
assert all(d.get("total_cards")==60 for d in decks["decks"].values())
print("POST-COLLECTION: DB schema and all 60-card lists verified.")
PY

  # Serve LIVE DB paths rather than accidentally serving a stale regular-file copy.
  for pair in "city-db.json:data/city_db.json" "city-decks.json:data/city_decks.json"; do
    name="${pair%%:*}"
    relative="${pair#*:}"
    target="$ROOT/.tmp/public/$name"
    source="$ROOT/$relative"
    if [[ ! -f "$source" ]]; then
      echo "ERROR: validated file missing: $source" >&2
      return 2
    fi
    if [[ -f "$target" && ! -L "$target" ]]; then
      cp -p "$target" "$target.pre-live-link.bak"
      echo "Backed up previously static published file: $name"
    fi
    link="$target.new.$$"
    rm -f "$link"
    ln -s "$source" "$link"
    mv -f "$link" "$target"
    echo "Published live symlink: $name"
  done

  echo "SOURCE AFTER:"
  show_state
  if command -v curl >/dev/null; then
    curl -fsS --max-time 8 -o /dev/null "http://127.0.0.1:8080/city-db.json" \
      && echo "HTTP 8080 local publication: OK" \
      || echo "WARNING: local HTTP 8080 not reachable. Check existing server/Cloudflare tunnel."
  fi
  echo "Android collection completed. GitHub can ingest at the next scheduled sync."
}

loop() {
  # Immediately refresh, then at 07:30, 17:30, 19:30, 21:30, 22:30 JST.
  while :; do
    bash "$SCRIPT" once || echo "WARNING: refresh failed; next scheduled run will retry"
    seconds="$(python - <<'PY'
from datetime import datetime,timedelta,timezone
tz=timezone(timedelta(hours=9))
now=datetime.now(tz)
slots=[(7,30),(17,30),(19,30),(21,30),(22,30)]
times=[now.replace(hour=h,minute=m,second=0,microsecond=0) for h,m in slots]
future=[t for t in times if t>now]
target=min(future) if future else times[0]+timedelta(days=1)
print(max(30,int((target-now).total_seconds())))
PY
)"
    sleep "$seconds"
  done
}

case "${1:-once}" in
  once) refresh_once ;;
  status)
    show_state
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Android refresh loop: RUNNING (PID $(cat "$PID_FILE"))"
    else
      echo "Android refresh loop: STOPPED"
    fi
    ;;
  start)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Android refresh loop already running"; exit 0
    fi
    nohup bash "$SCRIPT" loop > "$DAEMON_LOG" 2>&1 < /dev/null &
    echo "$!" > "$PID_FILE"
    echo "Android refresh loop started. PID $(cat "$PID_FILE")"
    echo "Log: $DAEMON_LOG (Android standby may stop background processes)"
    ;;
  stop)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      kill "$(cat "$PID_FILE")"
      echo "Android refresh loop stopped"
    fi
    rm -f "$PID_FILE"
    ;;
  loop) loop ;;
  *) echo "Usage: bash scripts/termux_refresh.sh [once|start|stop|status]" >&2; exit 2 ;;
esac
