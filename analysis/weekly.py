"""Deterministic weekly analysis over an immutable City League snapshot.

This module never mutates collector data or snapshot inputs. It reports only
captured public result rows, keeps CURRENT_WEEK and TREND separate, and uses
venue_prefecture (never player prefecture) for regional selection.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from .cards import validate_deck
from .classification import RULES as CLASSIFIER_RULES, VERSION as CLASSIFIER_VERSION, classify
from .state import read_json, read_state, verify_snapshot

ENGINE_VERSION = "weekly-analysis-v1"
UNCLASSIFIED = "未分類"
STAGES = (("top8", 8), ("top4", 4), ("top2", 2), ("champion", 1))
CONVERSIONS = (
    ("top8_to_top4", "top8", "top4"),
    ("top4_to_top2", "top4", "top2"),
    ("top2_to_champion", "top2", "champion"),
    ("top8_to_champion", "top8", "champion"),
)
SMALL_SAMPLE_N = 20


def _ratio(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": None if denominator == 0 else numerator / denominator,
    }


def wilson95(successes, total):
    """Two-sided Wilson score interval with z=1.959963984540054."""
    if total == 0:
        return None
    if not (0 <= successes <= total):
        raise ValueError("Require 0 <= successes <= total")
    z = 1.959963984540054
    p = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def _event_day(event):
    value = event.get("date")
    return date.fromisoformat(value) if value else None


def _validate_comparison_period(start, end, current_start, current_end):
    previous_start, previous_end = date.fromisoformat(start), date.fromisoformat(end)
    if previous_start > previous_end:
        raise ValueError("Comparison period start must not exceed end")
    if previous_start <= date.fromisoformat(current_end[:10]) and previous_end >= date.fromisoformat(current_start[:10]):
        raise ValueError("CURRENT_WEEK and TREND comparison periods must not overlap")


def _filter_events(city, start, end, category, event_ids=None, region=None):
    start_d, end_d = date.fromisoformat(start), date.fromisoformat(end)
    allowed = set(event_ids) if event_ids is not None else None
    result = []
    seen = set()
    for event in city.get("events", []):
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Missing event_id")
        if event_id in seen:
            raise ValueError(f"Duplicate event_id: {event_id}")
        seen.add(event_id)
        day = _event_day(event)
        if day is None or not start_d <= day <= end_d:
            continue
        if allowed is not None and event_id not in allowed:
            continue
        if category is not None and event.get("category") != category:
            continue
        if region is not None and event.get("venue_prefecture") != region:
            continue
        result.append(event)
    return result


def _placements(events, max_rank=8):
    rows = []
    for event in events:
        for placement in event.get("placements", []):
            rank = placement.get("rank")
            if type(rank) is not int or rank < 1:
                raise ValueError(f"Invalid rank in event {event.get('event_id')}")
            if rank <= max_rank:
                rows.append({
                    "event_id": event["event_id"],
                    "event_date": event.get("date"),
                    "venue_prefecture": event.get("venue_prefecture"),
                    "rank": rank,
                    "deck_code": placement.get("deck_code"),
                })
    return rows


def _valid_deck(code, decks):
    if not code or code not in decks:
        return None
    deck = decks[code]
    if any(issue["severity"] == "ERROR" for issue in validate_deck(deck)):
        return None
    return deck


def _classification_resolver(classifications, decks, derived_timestamp):
    records = {}
    for record in classifications.get("records", []):
        code = record["deck_code"]
        if code in records:
            raise ValueError(f"Duplicate deck classification: {code}")
        records[code] = record
    cache = {}

    def resolve(code):
        if code in cache:
            return cache[code]
        existing = records.get(code)
        existing_version = existing.get("classifier_version") if existing is not None else None
        sidecar_compatible = (
            existing is not None
            and existing_version in (None, CLASSIFIER_VERSION, "manual")
        )
        if sidecar_compatible:
            parent = (existing.get("parent_archetype")
                      if existing.get("classification_status") in ("classified", "partial")
                      else None)
            result = {
                "parent": parent,
                "variant_tags": list(existing.get("variant_tags") or []) if parent else [],
                "status": existing.get("classification_status"),
                "source": "snapshot_sidecar",
            }
        else:
            deck = _valid_deck(code, decks)
            if deck is None:
                result = {"parent": None, "variant_tags": [], "status": "unknown",
                          "source": "missing_or_invalid_deck"}
            else:
                derived = classify(code, deck, derived_timestamp)
                parent = (derived.get("parent_archetype")
                          if derived.get("classification_status") in ("classified", "partial")
                          else None)
                result = {
                    "parent": parent,
                    "variant_tags": list(derived.get("variant_tags") or []) if parent else [],
                    "status": derived.get("classification_status"),
                    "source": ("derived_post_snapshot_rule_upgrade"
                               if existing is not None else "derived_post_snapshot"),
                }
        cache[code] = result
        return result

    return resolve


def _stage_rows(events):
    top8 = _placements(events, 8)
    return {
        "top8": top8,
        "top4": [r for r in top8 if r["rank"] <= 4],
        "top2": [r for r in top8 if r["rank"] <= 2],
        "champion": [r for r in top8 if r["rank"] == 1],
    }


def _composition(rows, resolve):
    counts = Counter((resolve(row["deck_code"])["parent"] or UNCLASSIFIED) for row in rows)
    total = len(rows)
    return [
        {"parent_archetype": parent, "count": count, "top_stage_share": _ratio(count, total)}
        for parent, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _variant_prevalence(rows, resolve):
    parent_totals = Counter()
    tag_counts = Counter()
    for row in rows:
        classification = resolve(row["deck_code"])
        parent = classification["parent"]
        if not parent:
            continue
        parent_totals[parent] += 1
        for tag in classification["variant_tags"]:
            tag_counts[(parent, tag)] += 1
    result = []
    for (parent, tag), count in sorted(tag_counts.items()):
        result.append({
            "parent_archetype": parent,
            "variant_tag": tag,
            "count": count,
            "within_parent_prevalence": _ratio(count, parent_totals[parent]),
            "note": "tags_are_not_mutually_exclusive",
        })
    return result


def _conversion(rows_by_stage, resolve):
    stage_counts = {}
    for stage, rows in rows_by_stage.items():
        stage_counts[stage] = Counter(
            (resolve(row["deck_code"])["parent"] or UNCLASSIFIED) for row in rows
        )
    parents = sorted(set().union(*(set(c) for c in stage_counts.values())))
    output = []
    for parent in parents:
        metrics = {}
        for name, earlier, later in CONVERSIONS:
            denominator = stage_counts[earlier][parent]
            numerator = stage_counts[later][parent]
            metrics[name] = {
                **_ratio(numerator, denominator),
                "ci95_wilson": wilson95(numerator, denominator),
                "small_sample_warning": 0 < denominator < SMALL_SAMPLE_N,
                "small_sample_threshold": SMALL_SAMPLE_N,
            }
        output.append({
            "parent_archetype": parent,
            "stage_counts": {stage: stage_counts[stage][parent] for stage, _ in STAGES},
            "conversion": metrics,
        })
    return output


def _classification_coverage(rows, resolve, decks):
    sources = Counter()
    known = 0
    valid_lists = 0
    classified_valid_lists = 0
    statuses = Counter()
    for row in rows:
        valid = _valid_deck(row["deck_code"], decks) is not None
        if valid:
            valid_lists += 1
        c = resolve(row["deck_code"])
        sources[c["source"]] += 1
        statuses[c["status"]] += 1
        if c["parent"]:
            known += 1
            if valid:
                classified_valid_lists += 1
    return {
        "parent_classified": _ratio(known, len(rows)),
        "parent_classified_among_valid_60_lists": _ratio(
            classified_valid_lists, valid_lists),
        "valid_60_deck_lists": valid_lists,
        "statuses": dict(sorted(statuses.items())),
        "sources": dict(sorted(sources.items())),
        "unclassified_count": len(rows) - known,
    }


def _card_identity_key(card):
    return card.get("card_id"), card.get("name")


def _exact_card_stats(rows, decks, limit=20):
    known_lists = []
    for row in rows:
        deck = _valid_deck(row["deck_code"], decks)
        if deck is not None:
            known_lists.append(deck)
    adoption = Counter()
    copies = Counter()
    sections = {}
    for deck in known_lists:
        per_list = Counter()
        for card in deck["cards"]:
            key = _card_identity_key(card)
            if not all(isinstance(v, str) and v for v in key):
                continue
            per_list[key] += card["count"]
            sections[key] = card.get("section")
        for key, count in per_list.items():
            adoption[key] += 1
            copies[key] += count
    rows_out = []
    for (card_id, name), count in sorted(
            adoption.items(), key=lambda item: (-item[1], item[0][1], item[0][0]))[:limit]:
        rows_out.append({
            "card_id": card_id,
            "display_name": name,
            "section": sections[(card_id, name)],
            "adopting_lists": count,
            "adoption_rate": _ratio(count, len(known_lists)),
            "average_copies_when_present": copies[(card_id, name)] / count,
        })
    return {
        "known_deck_lists": len(known_lists),
        "identity_rule": "exact_card_id_and_exact_display_name; no cross-printing inference",
        "top_exact_identities": rows_out,
    }


def _validate_watch_config(config):
    if config is None:
        return None
    if not isinstance(config, dict) or config.get("version") != "watch-cards-v1":
        raise ValueError("watch card config requires version=watch-cards-v1")
    cards = config.get("cards")
    if not isinstance(cards, list):
        raise ValueError("watch card config requires cards list")
    labels = set()
    for item in cards:
        if set(item) != {"label", "identities"} or not isinstance(item["label"], str) or not item["label"]:
            raise ValueError("watch card entry requires label and identities only")
        if item["label"] in labels:
            raise ValueError("duplicate watch card label")
        labels.add(item["label"])
        if not isinstance(item["identities"], list) or not item["identities"]:
            raise ValueError("watch card identities cannot be empty")
        seen = set()
        for identity in item["identities"]:
            if set(identity) != {"card_id", "name"}:
                raise ValueError("watch identity requires exact card_id and name")
            key = (identity["card_id"], identity["name"])
            if not all(isinstance(v, str) and v for v in key) or key in seen:
                raise ValueError("invalid/duplicate watch identity")
            seen.add(key)
    return config


def _watch_card_stats(rows, decks, config):
    if config is None:
        return {
            "status": "not_configured",
            "known_deck_lists": sum(_valid_deck(r["deck_code"], decks) is not None for r in rows),
            "cards": [],
            "note": "logical-card adoption is not inferred from display-name substrings",
        }
    _validate_watch_config(config)
    known = []
    for row in rows:
        deck = _valid_deck(row["deck_code"], decks)
        if deck is not None:
            known.append(deck)
    output = []
    for item in config["cards"]:
        identities = {(x["card_id"], x["name"]) for x in item["identities"]}
        adopters = total_copies = 0
        for deck in known:
            copies = sum(card["count"] for card in deck["cards"]
                         if _card_identity_key(card) in identities)
            if copies:
                adopters += 1
                total_copies += copies
        output.append({
            "label": item["label"],
            "adopting_lists": adopters,
            "adoption_rate": _ratio(adopters, len(known)),
            "average_copies_when_present": None if adopters == 0 else total_copies / adopters,
        })
    return {
        "status": "configured",
        "known_deck_lists": len(known),
        "cards": output,
        "identity_rule": "explicit_exact_identities_only",
    }


def discover_card_identities(decks_document, queries):
    """Return exact observed identities matching literal display-name substrings.

    Discovery is for manual curation only; it does not merge identities.
    """
    if not queries or any(not isinstance(q, str) or not q for q in queries):
        raise ValueError("At least one non-empty query is required")
    decks = decks_document.get("decks", {})
    result = {}
    for query in queries:
        matches = {}
        for deck in decks.values():
            if any(i["severity"] == "ERROR" for i in validate_deck(deck)):
                continue
            for card in deck.get("cards", []):
                name, card_id = card.get("name"), card.get("card_id")
                if isinstance(name, str) and query in name and isinstance(card_id, str) and card_id:
                    key = (card_id, name)
                    entry = matches.setdefault(key, {"card_id": card_id, "name": name,
                                                     "sections": set(), "list_occurrences": 0})
                    entry["sections"].add(card.get("section"))
                    entry["list_occurrences"] += 1
        result[query] = [
            {**entry, "sections": sorted(s for s in entry["sections"] if s is not None)}
            for _, entry in sorted(matches.items(), key=lambda item: (-item[1]["list_occurrences"], item[0]))
        ]
    return {"mode": "literal_candidate_discovery_no_identity_merge", "queries": result}


def _top16_context(events, decks, resolve, watch_config):
    """Describe Top16 only for events with all eight observed 9-16 rows captured.

    Comparisons use matched events, never the broader Top8 sample. The source's
    first-seen timestamp is not a claim about the official publication time.
    """
    eligible = []
    for event in events:
        observed = event.get("top16_observed_rows")
        extra = [p for p in event.get("placements", [])
                 if type(p.get("rank")) is int and 8 < p["rank"] <= 16]
        if (event.get("top16_checked_at") and type(observed) is int
                and observed >= 8 and len(extra) >= observed):
            eligible.append(event)
    coverage = {
        "result_events": len(events),
        "complete_top16_events": len(eligible),
        "excluded_events": len(events) - len(eligible),
        "captured_top16_rows": 0,
        "captured_matched_top8_rows": 0,
    }
    if not eligible:
        return {
            "status": "insufficient_coverage",
            "scope": "Top16 event subset only; full participant usage is not observable",
            "coverage": coverage,
            "top16_composition": [],
            "matched_top8_composition": [],
            "top16_to_top8": [],
        }
    top16 = _placements(eligible, 16)
    matched_top8 = _placements(eligible, 8)
    coverage["captured_top16_rows"] = len(top16)
    coverage["captured_matched_top8_rows"] = len(matched_top8)
    counts16 = Counter(resolve(r["deck_code"])["parent"] or UNCLASSIFIED for r in top16)
    counts8 = Counter(resolve(r["deck_code"])["parent"] or UNCLASSIFIED for r in matched_top8)
    conversion = []
    for parent, count in sorted(counts16.items(), key=lambda x: (-x[1], x[0])):
        advanced = counts8[parent]
        conversion.append({
            "parent_archetype": parent,
            "top16_count": count,
            "top8_count_in_same_events": advanced,
            "observed_top16_to_top8": {
                **_ratio(advanced, count),
                "ci95_wilson": wilson95(advanced, count),
                "small_sample_warning": count < SMALL_SAMPLE_N,
                "small_sample_threshold": SMALL_SAMPLE_N,
            },
        })
    return {
        "status": "observed_complete_event_subset",
        "scope": "Top16 event subset only; not participant usage or match win rate",
        "coverage": coverage,
        "top16_composition": _composition(top16, resolve),
        "matched_top8_composition": _composition(matched_top8, resolve),
        "top16_to_top8": conversion,
        "top16_classification": _classification_coverage(top16, resolve, decks),
        "top16_watch_cards": _watch_card_stats(top16, decks, watch_config),
    }


def _scope_analysis(events, decks, resolve, watch_config):
    stages = _stage_rows(events)
    return {
        "coverage": {
            "result_events": len(events),
            "captured_top8_rows": len(stages["top8"]),
            "known_top8_deck_lists": sum(_valid_deck(r["deck_code"], decks) is not None
                                         for r in stages["top8"]),
        },
        "stage_composition": {stage: _composition(rows, resolve) for stage, rows in stages.items()},
        "variant_prevalence_top8": _variant_prevalence(stages["top8"], resolve),
        "conversion": _conversion(stages, resolve),
        "classification_coverage_top8": _classification_coverage(stages["top8"], resolve, decks),
        "card_analysis_top8": {
            "exact_identities": _exact_card_stats(stages["top8"], decks),
            "watch_cards": _watch_card_stats(stages["top8"], decks, watch_config),
        },
        "top16_context": _top16_context(events, decks, resolve, watch_config),
    }


def _share_map(composition):
    return {row["parent_archetype"]: row["top_stage_share"] for row in composition}


def _variant_map(items):
    return {(x["parent_archetype"], x["variant_tag"]): x["within_parent_prevalence"] for x in items}


def _watch_map(block):
    return {x["label"]: x["adoption_rate"] for x in block["cards"]}


def _delta(current, previous):
    if current["value"] is None or previous["value"] is None:
        return None
    return current["value"] - previous["value"]


def _trend_scope(current, previous):
    curr_share = _share_map(current["stage_composition"]["top8"])
    prev_share = _share_map(previous["stage_composition"]["top8"])
    parent_rows = []
    for parent in sorted(set(curr_share) | set(prev_share)):
        c = curr_share.get(parent, _ratio(0, current["coverage"]["captured_top8_rows"]))
        p = prev_share.get(parent, _ratio(0, previous["coverage"]["captured_top8_rows"]))
        parent_rows.append({
            "parent_archetype": parent,
            "previous_top8_share": p,
            "current_top8_share": c,
            "delta": _delta(c, p),
        })
    curr_variants = _variant_map(current["variant_prevalence_top8"])
    prev_variants = _variant_map(previous["variant_prevalence_top8"])
    curr_parent_counts = {x["parent_archetype"]: x["count"]
                          for x in current["stage_composition"]["top8"]}
    prev_parent_counts = {x["parent_archetype"]: x["count"]
                          for x in previous["stage_composition"]["top8"]}
    variants = []
    for key in sorted(set(curr_variants) | set(prev_variants)):
        c = curr_variants.get(key, _ratio(0, curr_parent_counts.get(key[0], 0)))
        p = prev_variants.get(key, _ratio(0, prev_parent_counts.get(key[0], 0)))
        variants.append({
            "parent_archetype": key[0], "variant_tag": key[1],
            "previous_prevalence": p, "current_prevalence": c, "delta": _delta(c, p),
        })
    curr_watch = current["card_analysis_top8"]["watch_cards"]
    prev_watch = previous["card_analysis_top8"]["watch_cards"]
    watch_rows = []
    if curr_watch["status"] == prev_watch["status"] == "configured":
        cm, pm = _watch_map(curr_watch), _watch_map(prev_watch)
        for label in sorted(set(cm) | set(pm)):
            c = cm[label]
            p = pm[label]
            watch_rows.append({"label": label, "previous_adoption_rate": p,
                               "current_adoption_rate": c, "delta": _delta(c, p)})
    return {
        "parent_top8_share": parent_rows,
        "variant_prevalence": variants,
        "watch_card_adoption": {
            "status": "configured" if watch_rows else curr_watch["status"],
            "cards": watch_rows,
        },
    }


def _target_coverage(metadata, city, ledger, category, region=None):
    city_by_id = {e["event_id"]: e for e in city.get("events", [])}
    ledger_by_id = {r["event_id"]: r for r in (ledger or {}).get("records", [])}
    target_ids = metadata.get("event_ids", [])
    known_category = unknown_category = selected = missing_result = 0
    for event_id in target_ids:
        city_event = city_by_id.get(event_id)
        ledger_event = ledger_by_id.get(event_id)
        category_value = (city_event or {}).get("category")
        if category_value is None:
            category_value = (ledger_event or {}).get("category")
        venue = (city_event or {}).get("venue_prefecture")
        if venue is None:
            venue = (ledger_event or {}).get("venue_prefecture")
        if category_value is None:
            unknown_category += 1
            continue
        known_category += 1
        if category_value != category:
            continue
        if region is not None and venue != region:
            continue
        selected += 1
        if city_event is None:
            missing_result += 1
    return {
        "snapshot_target_events_all_categories": len(target_ids),
        "target_events_with_known_category": known_category,
        "target_events_with_unknown_category": unknown_category,
        "selected_target_events": selected,
        "selected_target_events_missing_from_captured_city_results": missing_result,
        "region_basis": None if region is None else "venue_prefecture_exact_match",
    }


def _external_sources(document, week_id):
    if document is None:
        return {"status": "not_provided", "source_count": 0, "records": []}
    if document.get("kind") != "processed_sources":
        raise ValueError("external sources must be a processed_sources document")
    records = [r for r in document.get("records", [])
               if r.get("status") == "processed" and r.get("target_week") == week_id]
    projected = []
    fields = ("source_id", "source_type", "url", "published_at", "target_week", "language",
              "translation_used", "video_visuals_verified", "confirmed_facts", "author_analysis",
              "analysis_basis", "predictions", "caveats", "tracking_signals", "notes")
    for record in records:
        projected.append({k: record.get(k) for k in fields})
    return {
        "status": "available" if records else "no_matching_processed_sources",
        "source_count": len(records),
        "records": projected,
        "separation_rule": "confirmed_facts_and_author_analysis_are_distinct; no author opinion is promoted to DB fact",
    }


def _find_forbidden_key(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            lower = key.lower()
            if "usage_rate" in lower or "使用率" in key:
                return f"{path}/{key}"
            found = _find_forbidden_key(child, f"{path}/{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_forbidden_key(child, f"{path}/{index}")
            if found:
                return found
    return None


def audit_report(report):
    findings = []

    def add(level, code, message):
        findings.append({"level": level, "code": code, "message": message})

    if "current_week" not in report or "trend" not in report:
        add("FAIL", "CURRENT_TREND_MISSING", "CURRENT_WEEK and TREND must be separate")
    current = report.get("current_week", {})
    trend = report.get("trend", {})
    if trend.get("status") == "available":
        comparison = trend.get("comparison_period") or {}
        try:
            _validate_comparison_period(comparison["start"], comparison["end"],
                                        current["period_start"], current["period_end"])
        except (KeyError, TypeError, ValueError):
            add("FAIL", "CURRENT_TREND_PERIOD", "TREND requires a valid period separate from CURRENT_WEEK")
    forbidden = _find_forbidden_key(report)
    if forbidden:
        add("FAIL", "USAGE_RATE_TERM", f"Forbidden usage-rate terminology at {forbidden}")
    regional = report.get("current_week", {}).get("regional")
    if regional and regional.get("selection_basis") != "venue_prefecture":
        add("FAIL", "REGION_BASIS", "Regional selection must use venue_prefecture")
    for scope_name in ("national", "regional"):
        scope = report.get("current_week", {}).get(scope_name, {}).get("analysis")
        if not scope:
            continue
        for row in scope.get("conversion", []):
            for metric in row["conversion"].values():
                if metric["denominator"] and metric["ci95_wilson"] is None:
                    add("FAIL", "CI_MISSING", f"95% CI missing in {scope_name}")
                if "small_sample_warning" not in metric:
                    add("FAIL", "SMALL_SAMPLE_FLAG_MISSING", f"small sample flag missing in {scope_name}")
        unknown = scope["classification_coverage_top8"]["unclassified_count"]
        if unknown:
            add("WARN", "CLASSIFICATION_INCOMPLETE",
                f"{scope_name}: {unknown} captured Top8 rows remain unclassified; no inference fill applied")
        if scope["card_analysis_top8"]["watch_cards"]["status"] != "configured":
            add("WARN", "WATCH_CARDS_UNCONFIGURED",
                f"{scope_name}: logical watch-card identities are not configured")
    if report.get("external_analysis", {}).get("status") in ("not_provided", "no_matching_processed_sources"):
        add("WARN", "EXTERNAL_ANALYSIS_ABSENT", "No processed external analysis was joined")
    if not findings:
        add("PASS", "AUDIT_OK", "No audit finding")
    status = ("FAIL" if any(f["level"] == "FAIL" for f in findings)
              else "WARN" if any(f["level"] == "WARN" for f in findings) else "PASS")
    return {"status": status, "findings": findings}


def build_weekly_report(metadata, city, decks_document, classifications, *,
                        category="オープン", region="愛知県",
                        compare_start=None, compare_end=None,
                        watch_config=None, external_sources=None,
                        generated_at=None):
    if metadata.get("kind") != "weekly_snapshot" or metadata.get("schema_version") != "1.2.2":
        raise ValueError("weekly report requires a v1.2.2 weekly snapshot")
    if (compare_start is None) != (compare_end is None):
        raise ValueError("comparison requires both start and end")
    if compare_start is not None:
        _validate_comparison_period(compare_start, compare_end,
                                    metadata["period_start"], metadata["period_end"])
    decks = decks_document.get("decks", {})
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    resolve = _classification_resolver(classifications, decks, generated_at)

    start = metadata["period_start"][:10]
    end = metadata["period_end"][:10]
    current_national_events = _filter_events(
        city, start, end, category, event_ids=metadata["event_ids"])
    current_regional_events = _filter_events(
        city, start, end, category, event_ids=metadata["event_ids"], region=region)

    current_national = _scope_analysis(current_national_events, decks, resolve, watch_config)
    current_regional = _scope_analysis(current_regional_events, decks, resolve, watch_config)

    report = {
        "engine_version": ENGINE_VERSION,
        "generated_at": generated_at,
        "week_id": metadata["week_id"],
        "analysis_stage": metadata["analysis_stage"],
        "snapshot_id": metadata["snapshot_id"],
        "category": category,
        "terminology": {
            "top8_share": "captured Top8 composition share; never participant usage rate",
            "card_adoption_rate": "share among captured Top8 rows with a valid 60-card list",
            "top16_context": "Top16 upper-placing rows among complete captured Top16 event subset",
            "top16_to_top8": "observed proportion advancing to Top8 within matched event subset; not match win rate",
        },
        "provenance": {
            "snapshot_git_commit_sha": metadata["git_commit_sha"],
            "snapshot_retrieved_at": metadata["retrieved_at"],
            "snapshot_cutoff_datetime": metadata["cutoff_datetime"],
            "classifier_version": CLASSIFIER_VERSION,
            "classifier_market": CLASSIFIER_RULES["market"],
            "classifier_card_pool_basis": CLASSIFIER_RULES["card_pool_basis"],
            "external_taxonomy_reference": CLASSIFIER_RULES["external_taxonomy_reference"],
            "classification_mode": "snapshot_sidecar_when_compatible_else_post_snapshot_derivation",
        },
        "current_week": {
            "period_start": metadata["period_start"],
            "period_end": metadata["period_end"],
            "national": {
                "target_coverage": None,
                "analysis": current_national,
            },
            "regional": {
                "region": region,
                "selection_basis": "venue_prefecture",
                "target_coverage": None,
                "analysis": current_regional,
            },
        },
        "trend": {
            "status": "not_requested" if compare_start is None else "available",
            "historical_basis": ("none" if compare_start is None
                                 else "recomputed_from_same_FINAL_snapshot_captured_city_data"),
            "comparison_period": None if compare_start is None else {
                "start": compare_start, "end": compare_end,
            },
            "national": None,
            "regional": None,
        },
        "external_analysis": _external_sources(external_sources, metadata["week_id"]),
        "limitations": [],
    }

    if compare_start is not None:
        previous_national_events = _filter_events(city, compare_start, compare_end, category)
        previous_regional_events = _filter_events(city, compare_start, compare_end, category, region=region)
        previous_national = _scope_analysis(previous_national_events, decks, resolve, watch_config)
        previous_regional = _scope_analysis(previous_regional_events, decks, resolve, watch_config)
        report["trend"]["national"] = {
            "previous_coverage": previous_national["coverage"],
            "comparison": _trend_scope(current_national, previous_national),
        }
        report["trend"]["regional"] = {
            "region": region,
            "previous_coverage": previous_regional["coverage"],
            "comparison": _trend_scope(current_regional, previous_regional),
        }

    # Coverage over target ids needs the captured event ledger when available. The
    # file wrapper fills this after reading the snapshot. Without it, city facts
    # are still sufficient for the analysis rows.
    if current_national["classification_coverage_top8"]["unclassified_count"]:
        report["limitations"].append(
            "Parent archetype coverage is incomplete; unsupported decks remain 未分類.")
    if watch_config is None:
        report["limitations"].append(
            "Watch-card logical identities are not configured; exact card IDs are shown separately.")
    if report["external_analysis"]["status"] != "available":
        report["limitations"].append(
            "No processed external source analysis for this week was joined.")
    report["audit"] = audit_report(report)
    return report


def analyze_snapshot(snapshot_dir, *, category="オープン", region="愛知県",
                     compare_start=None, compare_end=None, watch_config=None,
                     external_sources=None, generated_at=None):
    snapshot_dir = Path(snapshot_dir)
    metadata = verify_snapshot(snapshot_dir)

    def load(relative, required=True):
        path = snapshot_dir / "inputs" / relative
        if not path.exists():
            if required:
                raise FileNotFoundError(path)
            return None
        return read_json(path)

    city = load("data/city_db.json")
    decks = load("data/city_decks.json")
    classifications = load("data/analysis/deck_classifications.json", required=False)
    if classifications is None:
        classifications = {"kind": "deck_classifications", "records": []}
    ledger = load("data/analysis/event_ledger.json", required=False)
    report = build_weekly_report(
        metadata, city, decks, classifications, category=category, region=region,
        compare_start=compare_start, compare_end=compare_end, watch_config=watch_config,
        external_sources=external_sources, generated_at=generated_at)
    report["current_week"]["national"]["target_coverage"] = _target_coverage(
        metadata, city, ledger, category)
    report["current_week"]["regional"]["target_coverage"] = _target_coverage(
        metadata, city, ledger, category, region=region)

    identity_path = Path(__file__).with_name("classification_cards.json")
    rules_path = Path(__file__).with_name("archetype_rules.json")
    report["provenance"]["classifier_sha256"] = hashlib.sha256(
        identity_path.read_bytes() + b"\0" + rules_path.read_bytes()).hexdigest()
    report["provenance"]["classification_identity_sha256"] = hashlib.sha256(
        identity_path.read_bytes()).hexdigest()
    report["provenance"]["classification_rules_sha256"] = hashlib.sha256(
        rules_path.read_bytes()).hexdigest()
    report["provenance"]["snapshot_reference_hashes"] = {
        r["path"]: r["sha256"] for r in metadata["references"]
    }
    report["audit"] = audit_report(report)
    return report


def render_markdown(report):
    def pct(ratio):
        return "n/a" if ratio["value"] is None else f"{ratio['value'] * 100:.1f}%"

    national = report["current_week"]["national"]["analysis"]
    regional_block = report["current_week"]["regional"]
    regional = regional_block["analysis"]
    lines = [
        f"# シティリーグ {report['week_id']} {report['analysis_stage']} 環境分析",
        "",
        f"- 対象: {report['category']}",
        f"- CURRENT_WEEK: {report['current_week']['period_start']} ～ {report['current_week']['period_end']}",
        f"- snapshot: `{report['snapshot_id']}`",
        "- 比率は公開Top8捕捉行ベース。全参加者の使用率ではない。",
        f"- 分類provenance: `{report['provenance']['classification_mode']}`。snapshot内60枚リストからのpost-snapshot派生分析を含む。",
        f"- 分類器: `{report['provenance']['classifier_version']}`（{report['provenance'].get('classifier_market', 'unknown')}カードプール）、ルール+辞書SHA256: `{report['provenance'].get('classifier_sha256', 'not_available')}`",
        "",
        "## データカバレッジ",
        f"- 全国: 結果大会 {national['coverage']['result_events']}、Top8捕捉行 {national['coverage']['captured_top8_rows']}、60枚リスト判明 {national['coverage']['known_top8_deck_lists']}",
        f"- {regional_block['region']}: 結果大会 {regional['coverage']['result_events']}、Top8捕捉行 {regional['coverage']['captured_top8_rows']}、60枚リスト判明 {regional['coverage']['known_top8_deck_lists']}",
        "",
        "## CURRENT_WEEK｜全国 Top8構成",
        "",
        "| 親アーキタイプ | 件数 | Top8占有率 |",
        "|---|---:|---:|",
    ]
    for row in national["stage_composition"]["top8"]:
        lines.append(f"| {row['parent_archetype']} | {row['count']} | {pct(row['top_stage_share'])} |")
    lines += ["", "## CURRENT_WEEK｜勝ち上がり", ""]
    for row in national["conversion"]:
        if row["parent_archetype"] == UNCLASSIFIED:
            continue
        c = row["conversion"]["top8_to_champion"]
        ci = c["ci95_wilson"]
        ci_text = "n/a" if ci is None else f"{ci[0]*100:.1f}–{ci[1]*100:.1f}%"
        warning = "（少数標本）" if c["small_sample_warning"] else ""
        lines.append(
            f"- {row['parent_archetype']}: Top8→優勝 {c['numerator']}/{c['denominator']} "
            f"= {pct(c)}、95%CI {ci_text}{warning}")
    coverage = national["classification_coverage_top8"]
    lines += [
        "",
        "## 構築・採用カード",
        f"- 親分類判明: {coverage['parent_classified']['numerator']}/{coverage['parent_classified']['denominator']} = {pct(coverage['parent_classified'])}",
        f"- 有効60枚リスト内の親分類カバレッジ: {coverage['parent_classified_among_valid_60_lists']['numerator']}/{coverage['parent_classified_among_valid_60_lists']['denominator']} = {pct(coverage['parent_classified_among_valid_60_lists'])}",
    ]
    watch = national["card_analysis_top8"]["watch_cards"]
    if watch["status"] == "configured":
        for card in watch["cards"]:
            avg = "n/a" if card["average_copies_when_present"] is None else f"{card['average_copies_when_present']:.2f}"
            lines.append(
                f"- {card['label']}: {card['adopting_lists']}/{watch['known_deck_lists']} "
                f"= {pct(card['adoption_rate'])}、採用時平均 {avg}枚")
    else:
        lines.append("- 監視カード: exact ID辞書未設定。論理カード単位の採用率は判断不能。")
    lines += ["", f"## 全国 vs {regional_block['region']}"]
    lines.append(
        f"- {regional_block['region']}の地域判定は開催店舗の `venue_prefecture` のみ。選手所在地は使用しない。")
    for label, scope in (("全国", national), (regional_block["region"], regional)):
        lines += ["", f"## CURRENT_WEEK｜{label} 各段階の構成比", "",
                  "| 段階 | 親アーキタイプ | 件数 | 上位入賞デッキ内シェア |",
                  "|---|---|---:|---:|"]
        for stage, _ in STAGES:
            for row in scope["stage_composition"][stage]:
                lines.append(f"| {stage} | {row['parent_archetype']} | {row['count']} | {pct(row['top_stage_share'])} |")
        lines += ["", f"### {label} conversion（Wilson 95%CI）", "",
                  "| 親アーキタイプ | conversion | 成功/分母 | 比率 | 95%CI | 警告 |",
                  "|---|---|---:|---:|---|---|"]
        for row in scope["conversion"]:
            for name, c in row["conversion"].items():
                ci = c["ci95_wilson"]
                ci_text = "n/a" if ci is None else f"{ci[0]*100:.1f}–{ci[1]*100:.1f}%"
                warning = "少数標本（分母20未満）" if c["small_sample_warning"] else ""
                lines.append(f"| {row['parent_archetype']} | {name} | {c['numerator']}/{c['denominator']} | {pct(c)} | {ci_text} | {warning} |")
        lines += ["", f"### {label} 派生型（タグは重複可能）", "",
                  "| 親アーキタイプ | 派生タグ | 件数 | 親内構成比 |",
                  "|---|---|---:|---:|"]
        for row in scope["variant_prevalence_top8"]:
            lines.append(f"| {row['parent_archetype']} | {row['variant_tag']} | {row['count']} | {pct(row['within_parent_prevalence'])} |")
        exact = scope["card_analysis_top8"]["exact_identities"]
        lines += ["", f"### {label} exactカード採用（上位20 identity）",
                  f"- 分母: 捕捉Top8行の有効60枚リスト {exact['known_deck_lists']}件。別収録の統合は行わない。", "",
                  "| card_id | 完全一致表示名 | 採用リスト数 | 採用率 | 採用時平均枚数 |",
                  "|---|---|---:|---:|---:|"]
        for row in exact["top_exact_identities"]:
            lines.append(f"| {row['card_id']} | {row['display_name']} | {row['adopting_lists']} | {pct(row['adoption_rate'])} | {row['average_copies_when_present']:.2f} |")
    if report["trend"]["status"] == "available":
        lines += ["", "## TREND", ""]
        comparison = report["trend"]["comparison_period"]
        lines += [f"- 比較期間: {comparison['start']} ～ {comparison['end']}",
                  f"- historical_basis: `{report['trend']['historical_basis']}`。W01当時の固定snapshotではなく、同じFINAL入力の過去週データを再計算。"]
        for row in report["trend"]["national"]["comparison"]["parent_top8_share"]:
            if row["parent_archetype"] == UNCLASSIFIED or row["delta"] is None:
                continue
            lines.append(
                f"- {row['parent_archetype']}: Top8占有率 {pct(row['previous_top8_share'])} → "
                f"{pct(row['current_top8_share'])} ({row['delta']*100:+.1f}pt)")
        for label, scope_name in (("全国", "national"), (regional_block["region"], "regional")):
            scope = report["trend"][scope_name]
            lines += ["", f"### TREND｜{label}",
                      f"- 前週Top8捕捉行: {scope['previous_coverage']['captured_top8_rows']}"]
            for row in scope["comparison"]["parent_top8_share"]:
                delta = "n/a" if row["delta"] is None else f"{row['delta']*100:+.1f}pt"
                lines.append(f"- {row['parent_archetype']}: {pct(row['previous_top8_share'])} → {pct(row['current_top8_share'])} ({delta})")
            for row in scope["comparison"]["variant_prevalence"]:
                delta = "n/a" if row["delta"] is None else f"{row['delta']*100:+.1f}pt"
                lines.append(f"- {row['parent_archetype']} / {row['variant_tag']}: 親内構成比 {pct(row['previous_prevalence'])} → {pct(row['current_prevalence'])} ({delta})")
    lines += ["", "## 外部考察", ""]
    external = report["external_analysis"]
    if external["status"] != "available":
        lines.append(f"- {report['week_id']}対象の処理済み外部考察は未結合。")
    else:
        for source in external["records"]:
            lines.append(f"- {source['source_id']}: 確認事実 {len(source.get('confirmed_facts') or [])}件、作者考察 {len(source.get('author_analysis') or [])}件")
    lines += ["", "## 未解決事項", ""]
    lines.extend(f"- {item}" for item in report["limitations"])
    lines += ["", "## 監査", f"- status: **{report['audit']['status']}**"]
    for finding in report["audit"]["findings"]:
        lines.append(f"- {finding['level']} {finding['code']}: {finding['message']}")
    return "\n".join(lines) + "\n"
