# Android/Termux source recovery (2026-10-12)

## Incident facts
- 2026-10-12 12:52 JST: GitHub Actions transport from Cloudflare Termux returned HTTP 200 for both JSON files, but official GitHub-hosted API returned HTTP 403.
- GitHub stored data still has source timestamp 2026-10-10 22:48:50 JST. No October 11 result event is yet in the stored DB.
- HTTP 200 of the Android endpoint does **not** imply its producer is running, and a workflow result of SUCCESS does **not** imply all published tournament results were collected.
- Cannot confirm the Android device's current local process or official API access from GitHub alone.

## Single-device repair (existing phone, existing repository)
From Android Termux, with its existing `~/pcg-city-db` checkout:

```bash
mkdir -p ~/pcg-city-db/scripts
curl -fsSL https://raw.githubusercontent.com/kazukioverture-glitch/pcg-city-db/main/scripts/termux_refresh.sh \
  -o ~/pcg-city-db/scripts/termux_refresh.sh
bash ~/pcg-city-db/scripts/termux_refresh.sh once
```

The script runs the existing official collector locally with `TERMUX_BASE_URL` unset to avoid reading its own web feed, validates the DB and all 60-card lists, and atomically replaces *published* `.tmp/public/city-db.json` and `city-decks.json` with symlinks to live checked DB files. Previously static published files are backed up before replacement. The original previous GitHub DB, historical FINAL snapshots and forecasts are untouched. On failure the script stops before link replacement and reports the relevant collector error.

Check `source_updated_at`, `latest_observed_event_day`, `Oct11_open_observed_events`, then GitHub sync. The existence of some October 11 events does not establish full tournament coverage; verify against the scheduled-event ledger before W03 FINAL.

### Recurring source collection (explicit start)

```bash
bash ~/pcg-city-db/scripts/termux_refresh.sh start
bash ~/pcg-city-db/scripts/termux_refresh.sh status
# To stop:
bash ~/pcg-city-db/scripts/termux_refresh.sh stop
```

The lightweight local loop collects immediately and at 07:30, 17:30, 19:30, 21:30, 22:30 JST, ahead of GitHub's five ingest runs. This is a foreground-device-managed Termux job, **not** a guaranteed server-side always-on scheduler; Android process termination or reboot stops it. The collector enforces a lock to avoid overlapping local refreshes. Its log is `.tmp/termux-refresh-loop.log`; last official run log is `.tmp/termux-refresh.last.log`. Only start recurring mode while the Android device is intended to be kept online.

If the Android-side official collector also returns HTTP 403, repeated requests will not solve source access. Record the error; do not infer that no results were published.

## GitHub diagnostics
`collector.sync.source_health` now records source timestamp, age, latest event date, and a 24-hour stale flag in `.tmp/sync/sync_report.json`. It prints a warning when an old-but-successful Termux transfer is used. Staleness is a **collection freshness** signal, not proof that a particular tournament result is available. Neither original collector transaction semantics nor historical files were modified.
