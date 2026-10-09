# Top16 collection and MDM usage

The official results collector uses the **same** 08:00, 18:00, 20:00, 22:00 and 23:00 JST sync as Top8. It does not create a second workflow.

## Collection contract
- The official result API is paginated to rank <= 16 (including the source's tied ranking rows).
- Priority order: current/recent events, incomplete core Top8, then previously unchecked historical events.
- Maximum **24 event-detail requests** and **32 previously unseen Top9-16 deck codes** per run by default. The event limit is 24 *events*, not HTTP requests; pagination can require multiple HTTP requests.
- Existing deck codes are reused. All Top8 deck codes are mandatory: a failed Top8 deck parse aborts the official candidate transaction. Top9-16 deck codes are best effort: failures or budget overflow leave those **rows** pending rather than writing dangling deck references.
- Partial records are retried on subsequent runs using `top16_observed_rows` (number of source-observed Top9-16 rows) and `top16_captured_rows` (number safely stored). `top16_checked_at` means collector checked, **not** official publication time.
- Source access failure (including HTTP 403/429) does not justify treating the event as unpublished. The existing staging, 60-card validation, monotonic merge, and transaction rollback remain in place.

## Analysis contract
- The current Top8, Top4, Top2 and champion measures remain unchanged and **exclude** ranks 9-16.
- The Top16 context is a **separate CURRENT_WEEK field**. It includes an event only after eight or more extra rows were confirmed from the source and all source-observed extra rows were safely captured.
- If that criterion cannot be established, the event is excluded. The result is an **observed complete-event subset**, not all City League participants, not a nationwide usage rate, and not a claim that every tournament publishes Top16.
- Top16 and Top8 composition are compared only on the **same eligible events**. Top16->Top8 is an observed advancement fraction with Wilson 95% interval and small-N flag, **not** game win rate.
- The status `insufficient_coverage` is a valid result: never fill missing rankings or infer usage.
- The prior W01/W02 FINAL immutable snapshots are not modified. HISTORICAL/TREND Top8 remains independent.

## Operational checks
1. Confirm official API availability and count from actual responses before claiming Top16 coverage.
2. Verify the new tests, existing tests, and repository CI.
3. Confirm Top8 data is unchanged for the same events; each non-null deck code resolves to a validated 60-card deck.
4. Confirm actual request counts/elapsed time via `.tmp/sync/official/official_audit.json` and the sync diagnostics; the first historical backfill is bounded and may take several scheduled runs.
5. If official access is HTTP 403, record the absence of live verification; do not claim the Top16 pipeline completed a production collection.
