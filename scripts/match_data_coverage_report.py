from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc

DEBUG_PATHS = [Path(".logs/debug-last-run.json")]
CONTROLLED_REPORT_PATHS = [
    Path("artifacts/controlled-fallback-report.json"),
    Path(".data/exports/latest-controlled-fallback-report.json"),
]
RESCUE_PATHS = [
    Path(".data/exports/latest-rescue-candidates.json"),
    Path("artifacts/run-bot/latest-rescue-candidates.json"),
]

OUT_SUMMARY = Path(".data/exports/latest-match-data-coverage-summary.json")
OUT_MATCHES = Path(".data/exports/latest-match-data-coverage-matches.json")
OUT_NEAR_MISS = Path(".data/exports/latest-match-data-near-miss.json")
OUT_PROVIDER_GAPS = Path(".data/exports/latest-provider-gaps.json")
DELETED_PROVIDERS = {"api_football"}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def rows_from(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("candidates", "rows", "items", "rescue_candidates", "latest_rescue_candidates", "evaluated", "watchlist", "selected"):
        value = payload.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
    return []


def nested(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def value_from(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    metrics = nested(row, "metrics")
    for key in keys:
        if key in metrics and metrics.get(key) not in (None, ""):
            return metrics.get(key)
    diagnostics = nested(row, "diagnostics")
    quality = diagnostics.get("quality") if isinstance(diagnostics.get("quality"), dict) else {}
    for key in keys:
        if key in quality and quality.get(key) not in (None, ""):
            return quality.get(key)
    return default


def home(row: dict[str, Any]) -> str:
    return str(value_from(row, "home_team", "home", default="")).strip()


def away(row: dict[str, Any]) -> str:
    return str(value_from(row, "away_team", "away", default="")).strip()


def league(row: dict[str, Any]) -> str:
    return str(value_from(row, "league_name", "league", "competition", default="")).strip()


def start_time(row: dict[str, Any]) -> str:
    return str(value_from(row, "commence_time", "start_time", "kickoff", default="")).strip()


def family(row: dict[str, Any]) -> str:
    return str(value_from(row, "family", "market_family", default="unknown")).strip().lower() or "unknown"


def selection(row: dict[str, Any]) -> str:
    return str(value_from(row, "selection", "selection_text", "pick", default="")).strip()


def match_key(row: dict[str, Any]) -> str:
    raw = str(value_from(row, "match_key", default="")).strip()
    if raw:
        return raw
    return "|".join([home(row).lower(), away(row).lower(), start_time(row)])


def list_field(row: dict[str, Any], key: str) -> list[str]:
    value = row.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def reject_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in ("final_reject_reasons", "reject_reasons", "hard_reject_reasons", "quality_reasons"):
        reasons.extend(list_field(row, key))
    diagnostics = nested(row, "diagnostics")
    quality = diagnostics.get("quality") if isinstance(diagnostics.get("quality"), dict) else {}
    q_reasons = quality.get("reasons") if isinstance(quality, dict) else []
    if isinstance(q_reasons, list):
        reasons.extend(str(item) for item in q_reasons if str(item).strip())
    out: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            out.append(reason)
    return out


def candidate_score(row: dict[str, Any]) -> dict[str, Any]:
    odds = as_float(value_from(row, "odds", default=0.0))
    adjusted = as_float(value_from(row, "adjusted_probability", default=0.0))
    if adjusted > 1:
        adjusted /= 100.0
    implied = 1 / odds if odds > 1 else 0.0
    edge = as_float(value_from(row, "canonical_edge_pp", "edge_pp", default=(adjusted - implied) * 100 if odds > 1 else -999.0))
    ev = as_float(value_from(row, "canonical_ev_pct", "ev_pct", default=((adjusted * odds) - 1) * 100 if odds > 1 else -999.0))
    confidence = as_float(value_from(row, "confidence", default=0.0))
    books = as_int(value_from(row, "books_count", default=0))
    sources = as_int(value_from(row, "sources_count", default=0))
    return {
        "odds": round(odds, 4),
        "adjusted_probability": round(adjusted, 6),
        "canonical_edge_pp": round(edge, 3),
        "canonical_ev_pct": round(ev, 3),
        "confidence": round(confidence, 3),
        "books_count": books,
        "sources_count": sources,
    }


def first_existing(paths: list[Path]) -> dict[str, Any]:
    for path in paths:
        payload = load_json(path, {})
        if isinstance(payload, dict) and payload:
            return payload
    return {}


def load_rescue_candidates() -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for path in RESCUE_PATHS:
        for row in rows_from(load_json(path, [])):
            row.setdefault("_candidate_file", str(path))
            key = "|".join([match_key(row), family(row), selection(row).lower(), str(row.get("point") or ""), str(row.get("team_side") or "")])
            merged.setdefault(key, row)
    return list(merged.values())


def controlled_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in ("evaluated", "watchlist", "selected", "pick", "published_pick"):
        value = report.get(section)
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict):
                    item = dict(row)
                    item["_controlled_section"] = section
                    rows.append(item)
        elif isinstance(value, dict):
            item = dict(value)
            item["_controlled_section"] = section
            rows.append(item)
    return rows


def provider_stats_from_debug(debug: dict[str, Any]) -> dict[str, Any]:
    providers: dict[str, Any] = {}

    def visit(obj: Any, path: list[str]) -> None:
        if isinstance(obj, dict):
            keys = set(obj.keys())
            looks_provider = bool(
                {"enabled", "api_key_present"} & keys
                and (
                    "matches_built" in keys
                    or "events_fetched" in keys
                    or "fixtures_fetched" in keys
                    or "rows_fetched" in keys
                    or "offers_parsed" in keys
                    or "contexts_built" in keys
                    or "rate_limited" in keys
                    or "response_errors" in keys
                )
            )
            if looks_provider:
                name = path[-1] if path else "unknown"
                if str(name).strip().lower() not in DELETED_PROVIDERS:
                    providers[name] = obj
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    visit(value, path + [str(key)])
        elif isinstance(obj, list):
            for index, value in enumerate(obj[:100]):
                if isinstance(value, (dict, list)):
                    visit(value, path + [str(index)])

    visit(debug, [])
    return providers


def run_core_counts(debug: dict[str, Any], rescue_count: int, controlled_report: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(debug, ensure_ascii=False)

    def find_int(keys: list[str]) -> int | None:
        import re
        for key in keys:
            match = re.search(rf'"{key}"\s*:\s*(\d+)', text)
            if match:
                return int(match.group(1))
        return None

    return {
        "matches_seen": find_int(["matches_seen"]),
        "matches_with_offers": find_int(["matches_with_offers"]),
        "contexts_built": find_int(["contexts_built"]),
        "candidates_before_quality": find_int(["candidates_before_quality"]),
        "candidates_raw": find_int(["candidates_raw"]),
        "candidates_publishable": find_int(["candidates_publishable"]),
        "rescue_candidates_exported": rescue_count,
        "controlled_candidates_seen": controlled_report.get("candidates_seen") or controlled_report.get("checked_candidates"),
        "controlled_published": bool(controlled_report.get("published")),
        "controlled_status": controlled_report.get("status"),
    }


def build_match_rows(rescue: list[dict[str, Any]], controlled: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_match: dict[str, dict[str, Any]] = {}

    def add(row: dict[str, Any], origin: str) -> None:
        key = match_key(row)
        if not key:
            return
        item = by_match.setdefault(
            key,
            {
                "match_key": key,
                "home_team": home(row),
                "away_team": away(row),
                "league_name": league(row),
                "commence_time": start_time(row),
                "candidate_count": 0,
                "controlled_count": 0,
                "families": Counter(),
                "books_max": 0,
                "sources_max": 0,
                "best_ev_pct": -999.0,
                "best_edge_pp": -999.0,
                "best_confidence": 0.0,
                "best_candidate": None,
                "reject_reasons": Counter(),
                "origins": Counter(),
                "missing_flags": Counter(),
            },
        )
        item["candidate_count"] += 1
        if origin == "controlled":
            item["controlled_count"] += 1
        item["origins"].update([origin])
        item["families"].update([family(row)])
        score = candidate_score(row)
        item["books_max"] = max(item["books_max"], score["books_count"])
        item["sources_max"] = max(item["sources_max"], score["sources_count"])
        item["best_confidence"] = max(item["best_confidence"], score["confidence"])
        for reason in reject_reasons(row):
            item["reject_reasons"].update([reason])
        if score["sources_count"] <= 1:
            item["missing_flags"].update(["single_source"])
        if score["books_count"] <= 1:
            item["missing_flags"].update(["single_book"])
        if score["confidence"] < 74:
            item["missing_flags"].update(["confidence_below_micro_c"])
        if score["canonical_ev_pct"] < 8:
            item["missing_flags"].update(["ev_below_micro_c"])
        if score["canonical_edge_pp"] < 4:
            item["missing_flags"].update(["edge_below_micro_c"])
        if score["canonical_ev_pct"] > item["best_ev_pct"]:
            item["best_ev_pct"] = score["canonical_ev_pct"]
            item["best_edge_pp"] = score["canonical_edge_pp"]
            item["best_candidate"] = {
                "family": family(row),
                "selection": selection(row),
                "point": row.get("point"),
                **score,
                "reject_reasons": reject_reasons(row),
                "origin": origin,
                "candidate_source": str(row.get("_candidate_source") or row.get("candidate_source") or row.get("_candidate_file") or ""),
            }

    for row in rescue:
        add(row, "rescue")
    for row in controlled:
        add(row, "controlled")

    rows = []
    for item in by_match.values():
        out = dict(item)
        out["families"] = dict(item["families"].most_common())
        out["reject_reasons"] = dict(item["reject_reasons"].most_common(12))
        out["origins"] = dict(item["origins"].most_common())
        out["missing_flags"] = dict(item["missing_flags"].most_common())
        rows.append(out)
    rows.sort(key=lambda item: (-(item.get("best_ev_pct") or -999), -(item.get("best_confidence") or 0), item.get("commence_time") or ""))
    return rows


def provider_status_category(name: str, stats: dict[str, Any]) -> tuple[str, list[str]]:
    provider = str(name or "").strip().lower()
    flags: list[str] = []
    if provider in DELETED_PROVIDERS:
        return "deleted_provider", ["removed_from_project"]
    if stats.get("enabled") is False:
        return "disabled_by_config", ["disabled_by_config"]
    if stats.get("api_key_present") is False:
        flags.append("missing_key")
    if stats.get("rate_limited"):
        flags.append("rate_limited")
    if as_int(stats.get("response_errors"), 0) > 0:
        flags.append("response_errors")
    if stats.get("budget_exhausted"):
        flags.append("budget_exhausted")

    useful = any(as_int(stats.get(key), 0) > 0 for key in ("matches_built", "events_matched", "offers_parsed", "contexts_built", "team_form_contexts_built", "bzzoiro_contexts_built"))
    fetched = any(as_int(stats.get(key), 0) > 0 for key in ("events_fetched", "fixtures_fetched", "rows_fetched", "predictions_fetched"))
    if not useful:
        flags.append("no_useful_rows" if not fetched else "low_overlap_or_matching_failed")
    if provider == "sstats" and as_int(stats.get("contexts_built"), 0) > 0 and as_int(stats.get("matched_exact"), 0) == 0:
        flags.append("team_form_fallback_only")
    if provider == "sportlogic" and as_int(stats.get("offers_parsed"), 0) == 0:
        flags.append("parser_or_payload_shape_issue")
    if provider == "odds_api_io" and stats.get("account2_missing"):
        flags.append("odds_api_io_account2_missing")

    if "missing_key" in flags:
        category = "missing_key"
    elif "rate_limited" in flags:
        category = "rate_limited"
    elif "response_errors" in flags and not useful:
        category = "broken_or_unavailable"
    elif useful:
        category = "working"
    elif fetched:
        category = "low_overlap_or_matching_issue"
    else:
        category = "no_data"
    return category, flags


def provider_gaps(provider_stats: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, stats in provider_stats.items():
        if not isinstance(stats, dict):
            continue
        if str(name).strip().lower() in DELETED_PROVIDERS:
            continue
        category, flags = provider_status_category(str(name), stats)
        contexts_built = as_int(stats.get("contexts_built"))
        team_form_contexts = as_int(stats.get("team_form_contexts_built"))
        rows.append({
            "provider": name,
            "status_category": category,
            "enabled": stats.get("enabled"),
            "api_key_present": stats.get("api_key_present"),
            "events_fetched": stats.get("events_fetched"),
            "fixtures_fetched": stats.get("fixtures_fetched"),
            "rows_fetched": stats.get("rows_fetched"),
            "matches_built": stats.get("matches_built"),
            "events_matched": stats.get("events_matched"),
            "offers_parsed": stats.get("offers_parsed"),
            "contexts_built": stats.get("contexts_built"),
            "team_form_contexts_built": stats.get("team_form_contexts_built"),
            "direct_event_contexts_built": max(0, contexts_built - team_form_contexts),
            "response_errors": stats.get("response_errors"),
            "rate_limited": stats.get("rate_limited"),
            "budget_exhausted": stats.get("budget_exhausted"),
            "requested_bookmakers": stats.get("requested_bookmakers"),
            "requested_bookmakers_by_account": stats.get("requested_bookmakers_by_account"),
            "bookmakers_seen": stats.get("bookmakers_seen"),
            "bookmakers_seen_names": stats.get("bookmakers_seen_names"),
            "account2_missing": stats.get("account2_missing"),
            "gap_flags": flags,
        })
    order = {"working": 0, "low_overlap_or_matching_issue": 1, "missing_key": 2, "rate_limited": 3, "broken_or_unavailable": 4, "no_data": 5, "disabled_by_config": 6}
    rows.sort(key=lambda item: (order.get(str(item.get("status_category")), 99), str(item.get("provider"))))
    return rows


def main() -> int:
    now = datetime.now(UTC).isoformat()
    debug = first_existing(DEBUG_PATHS)
    controlled_report = first_existing(CONTROLLED_REPORT_PATHS)
    rescue = load_rescue_candidates()
    controlled = controlled_rows(controlled_report)
    provider_stats = provider_stats_from_debug(debug)
    match_rows = build_match_rows(rescue, controlled)
    gaps = provider_gaps(provider_stats)

    near_miss = [row for row in match_rows if row.get("best_candidate") and row["best_candidate"].get("canonical_ev_pct", -999) > 0][:30]
    missing_counter: Counter[str] = Counter()
    family_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    for row in match_rows:
        missing_counter.update(row.get("missing_flags") or {})
        family_counter.update(row.get("families") or {})
        reason_counter.update(row.get("reject_reasons") or {})

    provider_status_counts = Counter(str(row.get("status_category") or "unknown") for row in gaps)
    summary = {
        "created_at": now,
        "core_counts": run_core_counts(debug, len(rescue), controlled_report),
        "matches_with_candidates": len(match_rows),
        "positive_near_miss_count": len(near_miss),
        "top_near_miss": near_miss[:10],
        "missing_data_flags": dict(missing_counter.most_common(20)),
        "family_counts": dict(family_counter.most_common(20)),
        "reject_reason_counts_from_rows": dict(reason_counter.most_common(25)),
        "provider_status_counts": dict(provider_status_counts.most_common()),
        "provider_gap_summary": gaps,
        "outputs": {
            "summary": str(OUT_SUMMARY),
            "matches": str(OUT_MATCHES),
            "near_miss": str(OUT_NEAR_MISS),
            "provider_gaps": str(OUT_PROVIDER_GAPS),
        },
    }

    write_json(OUT_SUMMARY, summary)
    write_json(OUT_MATCHES, match_rows)
    write_json(OUT_NEAR_MISS, near_miss)
    write_json(OUT_PROVIDER_GAPS, gaps)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
