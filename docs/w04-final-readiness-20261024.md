# W04 FINAL readiness audit — 2026-10-24

**Status: PRE-FINAL / scoped-release review needed. This document does not certify nationwide schedule coverage and does not release FINAL.**

## Immutable audit reference

- Audit target: W04 (2026-10-12 through 2026-10-18, JST).
- Source main commit at audit: `a2f033c50ef86e8c50aeeb78ddbbfe2aa4dd9ff4`.
- Superseding merged change: PR #33, merge commit `39af623dd4bf1504fd87f2b4e402a785282b2541`; PR #31 closed unmerged.
- `data/index.json` source time: `2026-10-24T10:39:52+09:00`.
- `data/index.json` City DB SHA-256: `40e30daadbba7a9ae6ed89296c92ed8968a58f00f4dc5d7aee78f0687775116c`.
- `data/index.json` deck DB SHA-256: `c737dc4af1fc917f48942b2674a31833c1303b69dd79c68453f8c02085a36b76`.
- This audit is scoped to the pinned source commit, not an automatically advancing main.

## Result vs repository event ledger (ID-level set comparison)

The W04 date-range filter applied to both `data/city_db.json` and `data/analysis/event_ledger.json` yields **130 distinct result event IDs and 130 distinct ledger event IDs**, with **zero scheduled-only and zero result-only IDs**. This **does not establish that the repository ledger contains every nationwide scheduled event**; schedule-source completeness has not been independently verified.

| Category | Result events | Captured Top8 rank rows | Top8 rows with a deck code | Top16 rank rows |
|---|---:|---:|---:|---:|
| オープン | 101 | 808 | 805 | 1,617 |
| シニア | 17 | 128 | 128 | 128 |
| ジュニア | 12 | 92 | 92 | 92 |
| **All** | **130** | **1,028** | **1,025** | **1,837** |

- All 101 Open result events have exactly eight Top8 rows and are recorded as Top16-checked with at least eight observed places 9–16. In total, Open has **809 rows ranked 9–16** (event `1115062` contains nine published rank-9 rows). This counts published ranking observations, not verified attendance.
- Three Junior/Senior events show only four published top ranks: `1115568` (Junior, オレタン福岡天神店), `1115586` (Senior, ポケモンセンターヒロシマ), `1115507` (Senior, ポケモンセンターカガワ). Source observations repeatedly reported four rows, with no evidence that a collector dropped an additional four. **Do not invent Top8 rows or equate capacity with attendees.**
- The W04 Top16 library reports 1,833 valid 60-card lists of 1,837 published ranking rows; the four invalid/unavailable are **unregistered deck codes**, not demonstrated invalid 60-card deck lists.

## Unavailable deck lists — preserve unknown (no reconstruction)

All four below are Open. Data sources store `deck_code: null`.

| Event | Date | Placement | Venue |
|---|---|---|---|
| `1115198` | 2026-10-12 | 1 | BOOKOFFSUPERBAZAAR町田中央通り店 |
| `1115071` | 2026-10-17 | 3 | 福福トレカ 秋葉原店 |
| `1115087` | 2026-10-18 | 5 | バトロコミニ十和田店 |
| `1115061` | 2026-10-12 | 9 | Open event; Top16 only |

Independent published-result compilations also report unregistered recipes at the first three events. References:
- https://sakiyomi360.com/pokemoncard-tournaments/city-2026-10-12-1115198/
- https://sakiyomi360.com/pokemoncard-tournaments/city-2026-10-17-1115071/
- https://sakiyomi360.com/pokemoncard-tournaments/city-2026-10-18-1115087/

Classification test baseline on pinned main: twelve Top8 deck codes remain legitimately ambiguous (**ten Open, two Junior**); these are **separate from** the three missing Open Top8 deck codes.
- Across all categories, evidence-backed classified deck-coded Top8 rows: **1,013 / 1,025 (98.83%)**.
- Open: **795 / 805** deck-coded Top8 rows classified; **795 / 808** captured Top8 rows if unregistered lists stay in the denominator.
- Twelve unknown deck codes are explicitly locked by `tests/test_w04_classification.py`. Do not apply a forced parent label.

## CI evidence

- PR #33 head `7f5bf70864a969cdc5c5b5af6c01c78e0a30596b`: [Analysis CI success](https://github.com/kazukioverture-glitch/pcg-city-db/actions/runs/40056862816).
- Merge commit `39af623...`: [Analysis CI success](https://github.com/kazukioverture-glitch/pcg-city-db/actions/runs/40057087101).
- Subsequent classifier commit `f8f7a0a...`: [Analysis CI success](https://github.com/kazukioverture-glitch/pcg-city-db/actions/runs/40057869529).
- This does **not** mean W04 FINAL release QA has been executed.

## Remaining release gates (maximum three)

1. **Coverage scope certification.** Independently reconcile the full official W04 schedule against ledger IDs, **or** release with an explicitly approved scope `captured_published_results_only` and `full_week_coverage_verified=false`. The 130/130 repository-local match is not independent national completeness.
2. **Missing-data acceptance and 60-card integrity.** Preserve the three Open Top8 unregistered deck lists, the one Open Top16 unregistered list, and the twelve intentionally unclassified deck codes as explicit unknowns. Confirm that all *available* 60-card lists pass validation, without claiming missing lists have 60 verified cards.
3. **Pinned W04-only FINAL artifact + QA.** Freeze a new W04 snapshot from audited inputs, recompute W04 Open regional/national summaries and stages with denominator labels, verify hashes and provenance, and publish W04-specific artifacts only after required gates are approved. The older W04 weekly delivery is **INTERIM, stale** (`2026-10-20T07:31:28+09:00`, 100 Open events and 800 Open Top8 rows); it must not be relabeled FINAL.

## Protected paths

No changes should be made to W01/W02 FINAL, W03 FINAL, existing weekly snapshots, `data/analysis/forecast_log.json`, or other forecast logs. This PR adds an audit document only; it is not a FINAL release.
