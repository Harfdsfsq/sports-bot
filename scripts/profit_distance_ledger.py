from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc

REPORT_PATHS = [
    Path("artifacts/controlled-fallback-report.json"),
    Path(".data/exports/latest-controlled-fallback-report.json"),
]
CANDIDATE_PATHS = [
    Path(".data/exports/latest-rescue-candidates.json"),
    Path("artifacts/run-bot/latest-rescue-candidates.json"),
]

OUT_LEDGER = Path(".data/profit-distance-ledger.jsonl")
OUT_SUMMARY = Path(".data/exports/latest-profit-distance-summary.json")
OUT_WATCHLIST = Path(".data/exports/latest-profit-watchlist.json")
OUT_PUBLISHABLE_WATCHLIST = Path(".data/exports/latest-profit-publishable-watchlist.json")
OUT_BLOCKED_POSITIVE = Path(".data/exports/latest-profit-blocked-positive.json")

DEFAULT_ALLOWED_FAMILIES = {"totals", "dnb"}


def env_set(name: str, default: set[str]) -> set[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return set(default)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
    for key in ("candidates", "rows", "items", "rescue_candidates", "latest_rescue_candidates"):
        value = payload.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
    return []


def nested_dict(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def metric(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    if key in row:
        return as_float(row.get(key), default)

    metrics = nested_dict(row, "metrics")
    if key in metrics:
        return as_float(metrics.get(key), default)

    diagnostics = nested_dict(row, "diagnostics")
    quality = diagnostics.get("quality") if isinstance(diagnostics.get("quality"), dict) else {}
    if key in quality:
        return as_float(quality.get(key), default)

    controlled = diagnostics.get("controlled_fallback") if isinstance(diagnostics.get("controlled_fallback"), dict) else {}
    if key in controlled:
        return as_float(controlled.get(key), default)

    return default


def family(row: dict[str, Any]) -> str:
    return str(row.get("family") or row.get("market_family") or "").strip().lower()


def selection(row: dict[str, Any]) -> str:
    return str(row.get("selection") or row.get("selection_text") or row.get("pick") or "").strip()


def home(row: dict[str, Any]) -> str:
    return str(row.get("home_team") or row.get("home") or "").strip()


def away(row: dict[str, Any]) -> str:
    return str(row.get("away_team") or row.get("away") or "").strip()


def start_time(row: dict[str, Any]) -> str:
    return str(row.get("commence_time") or row.get("start_time") or row.get("kickoff") or "").strip()


def candidate_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            str(row.get("match_key") or ""),
            home(row).lower(),
            away(row).lower(),
            start_time(row),
            family(row),
            selection(row).lower(),
            str(row.get("point") or ""),
            str(row.get("team_side") or "").lower(),
        ]
    )


def source_summary(row: dict[str, Any]) -> dict[str, Any]:
    return nested_dict(row, "source_summary")


def list_field(row: dict[str, Any], key: str) -> list[str]:
    value = row.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def quality_payload(row: dict[str, Any]) -> dict[str, Any]:
    diagnostics = nested_dict(row, "diagnostics")
    value = diagnostics.get("quality")
    return value if isinstance(value, dict) else {}


def controlled_payload(row: dict[str, Any]) -> dict[str, Any]:
    diagnostics = nested_dict(row, "diagnostics")
    value = diagnostics.get("controlled_fallback")
    return value if isinstance(value, dict) else {}


def final_reject_reasons(row: dict[str, Any]) -> list[str]:
    # Only true reject fields are used here. Generic candidate "reasons" often contains
    # source/model notes, so it is intentionally kept out of rejection counters.
    keys = (
        "final_reject_reasons",
        "reject_reasons",
        "hard_reject_reasons",
        "controlled_fallback_reject_reasons",
    )
    reasons: list[str] = []
    for key in keys:
        reasons.extend(list_field(row, key))

    quality = quality_payload(row)
    value = quality.get("reject_reasons") or quality.get("hard_reject_reasons")
    if isinstance(value, list):
        reasons.extend(str(item) for item in value if str(item).strip())

    controlled = controlled_payload(row)
    value = controlled.get("reject_reasons") or controlled.get("hard_reject_reasons")
    if isinstance(value, list):
        reasons.extend(str(item) for item in value if str(item).strip())

    # Deduplicate without reordering.
    result: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        text = str(reason).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def candidate_notes(row: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    # Generic "reasons" is useful, but it is not a final rejection reason.
    for key in ("reasons", "quality_reasons", "model_reasons"):
        notes.extend(list_field(row, key))

    ss = source_summary(row)
    for key in ("selected_source", "selected_bookmaker", "source", "market_move", "context"):
        value = ss.get(key)
        if value not in (None, ""):
            notes.append(f"{key}={value}")

    result: list[str] = []
    seen: set[str] = set()
    for note in notes:
        text = str(note).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def report_rejection_counts(report: dict[str, Any]) -> dict[str, int]:
    for key in ("rejection_counts", "reject_counts", "reason_counts", "reasons"):
        value = report.get(key)
        if isinstance(value, dict):
            result: dict[str, int] = {}
            for raw_key, raw_value in value.items():
                try:
                    result[str(raw_key)] = int(raw_value)
                except Exception:
                    continue
            if result:
                return dict(sorted(result.items(), key=lambda item: item[1], reverse=True))
    return {}


def reliability(row: dict[str, Any], confidence: float) -> dict[str, Any]:
    books = as_int(row.get("books_count"), as_int(metric(row, "books_count"), 0))
    sources = as_int(row.get("sources_count"), as_int(metric(row, "sources_count"), 0))
    quality_raw = metric(row, "quality_score_raw", metric(row, "quality_score", 0.0))
    q_source = str(row.get("quality_score_source") or "").strip().lower()
    fam = family(row)

    data_rel = 1.0 if quality_raw > 0 and sources >= 2 else 0.74 if books >= 2 and sources >= 1 else 0.45
    if quality_raw <= 0 or q_source == "proxy":
        data_rel *= 0.85 if books >= 2 else 0.62

    market_rel = 1.0 if books >= 2 else 0.60
    family_rel = 1.0 if fam == "dnb" else 0.90 if fam == "totals" else 0.60
    confidence_rel = min(1.0, max(0.55, confidence / 72.0)) if confidence > 0 else 0.55

    return {
        "books_count": books,
        "sources_count": sources,
        "quality_score_raw": round(quality_raw, 3),
        "quality_score_source": q_source or ("raw" if quality_raw > 0 else "proxy_or_missing"),
        "data_reliability": round(data_rel, 3),
        "market_reliability": round(market_rel, 3),
        "family_reliability": round(family_rel, 3),
        "confidence_reliability": round(confidence_rel, 3),
    }


def profit_score(row: dict[str, Any]) -> dict[str, Any]:
    odds = metric(row, "odds", as_float(row.get("odds"), 0.0))
    adjusted = metric(row, "adjusted_probability", as_float(row.get("adjusted_probability"), 0.0))
    if adjusted > 1.0:
        adjusted /= 100.0

    implied = 1.0 / odds if odds > 1.0 else 0.0
    edge = metric(row, "canonical_edge_pp", (adjusted - implied) * 100.0 if odds > 1.0 else -999.0)
    ev = metric(row, "canonical_ev_pct", ((adjusted * odds) - 1.0) * 100.0 if odds > 1.0 else -999.0)
    confidence = metric(row, "confidence", as_float(row.get("confidence"), 0.0))
    rel = reliability(row, confidence)

    uncertainty_penalty = 0.0
    if rel["sources_count"] <= 1:
        uncertainty_penalty += 1.8
    if rel["books_count"] <= 1:
        uncertainty_penalty += 2.2
    if rel["quality_score_source"] in {"proxy", "proxy_or_missing", "unknown"}:
        uncertainty_penalty += 1.4
    if confidence < 66:
        uncertainty_penalty += 1.2

    ev_lower_bound = ev - uncertainty_penalty
    score = max(0.0, ev_lower_bound)
    score *= rel["data_reliability"] * rel["market_reliability"] * rel["family_reliability"] * rel["confidence_reliability"]

    return {
        "odds": round(odds, 4),
        "adjusted_probability": round(adjusted, 6),
        "implied_probability": round(implied, 6),
        "canonical_edge_pp": round(edge, 3),
        "canonical_ev_pct": round(ev, 3),
        "confidence": round(confidence, 3),
        "uncertainty_penalty_pct": round(uncertainty_penalty, 3),
        "ev_lower_bound_pct": round(ev_lower_bound, 3),
        "profit_score": round(score, 3),
        **rel,
    }


def load_report() -> dict[str, Any]:
    for path in REPORT_PATHS:
        payload = load_json(path, {})
        if isinstance(payload, dict) and payload:
            payload["_path"] = str(path)
            return payload
    return {}


def selected_key(report: dict[str, Any]) -> str:
    for key in ("selected", "pick", "published_pick", "best_candidate"):
        value = report.get(key)
        if isinstance(value, dict):
            return candidate_key(value)

    for key in ("published", "picks"):
        value = report.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return candidate_key(value[0])

    return ""


def load_candidates() -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for path in CANDIDATE_PATHS:
        for row in rows_from(load_json(path, [])):
            row.setdefault("_candidate_file", str(path))
            merged.setdefault(candidate_key(row), row)
    return list(merged.values())


def blocked_reasons(item: dict[str, Any], allowed_families: set[str]) -> list[str]:
    result: list[str] = []
    fam = str(item.get("family") or "").lower()
    if fam not in allowed_families:
        result.append(f"family_not_publishable:{fam or 'unknown'}")

    for reason in item.get("final_reject_reasons") or []:
        text = str(reason)
        if (
            text.startswith("family_not_allowed:")
            or text in {
                "canonical_negative_value",
                "xg_probability_gap_hard_reject",
                "xg_direction_conflict",
                "btts_probability_gap_hard_reject",
                "btts_direction_conflict",
                "dnb_direction_conflict",
                "match_already_started",
                "match_time_outside_window",
                "match_time_too_late",
                "duplicate_fallback_sent_index",
            }
            or text.startswith("duplicate_state:")
        ):
            result.append(text)

    return result


def main() -> int:
    now = datetime.now(UTC).isoformat()
    report = load_report()
    selected = selected_key(report)
    allowed_families = env_set("CONTROLLED_FALLBACK_ALLOWED_FAMILIES", DEFAULT_ALLOWED_FAMILIES)

    rows: list[dict[str, Any]] = []
    candidate_rejection_counts: Counter[str] = Counter()
    candidate_note_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()

    for raw in load_candidates():
        key = candidate_key(raw)
        metrics = profit_score(raw)
        final_reasons = final_reject_reasons(raw)
        notes = candidate_notes(raw)

        item = {
            "recorded_at": now,
            "run_report_path": report.get("_path", ""),
            "candidate_key": key,
            "decision": "published" if selected and key == selected else "candidate",
            "home_team": home(raw),
            "away_team": away(raw),
            "commence_time": start_time(raw),
            "family": family(raw),
            "selection": selection(raw),
            "point": raw.get("point"),
            "bookmaker": raw.get("bookmaker") or source_summary(raw).get("selected_bookmaker") or "",
            "source": source_summary(raw).get("selected_source") or source_summary(raw).get("source") or "",
            "final_reject_reasons": final_reasons,
            "candidate_notes": notes,
            **metrics,
        }

        blocks = blocked_reasons(item, allowed_families)
        if item["decision"] != "published":
            if float(item.get("profit_score") or 0.0) <= 0:
                item["decision"] = "rejected"
            elif blocks:
                item["decision"] = "blocked_positive"
                item["blocked_reasons"] = blocks
            else:
                item["decision"] = "publishable_watchlist"

        rows.append(item)
        candidate_rejection_counts.update(final_reasons)
        candidate_note_counts.update(notes)
        family_counts.update([item["family"] or "unknown"])

    rows.sort(
        key=lambda item: (
            item["decision"] != "published",
            item["decision"] != "publishable_watchlist",
            -float(item.get("profit_score") or 0.0),
        )
    )

    publishable_watchlist = [
        item
        for item in rows
        if item["decision"] == "publishable_watchlist" and float(item.get("profit_score") or 0.0) > 0
    ][:25]
    blocked_positive = [
        item
        for item in rows
        if item["decision"] == "blocked_positive" and float(item.get("profit_score") or 0.0) > 0
    ][:25]
    all_positive = [
        item
        for item in rows
        if item["decision"] != "published" and float(item.get("profit_score") or 0.0) > 0
    ][:25]

    OUT_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with OUT_LEDGER.open("a", encoding="utf-8") as fh:
        for item in rows:
            fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    report_counts = report_rejection_counts(report)
    summary = {
        "created_at": now,
        "report_path": report.get("_path", ""),
        "allowed_families": sorted(allowed_families),
        "candidates": len(rows),
        "published": sum(1 for item in rows if item["decision"] == "published"),
        "publishable_watchlist_count": len(publishable_watchlist),
        "blocked_positive_count": len(blocked_positive),
        "positive_total_count": len(all_positive),
        "top_publishable_watchlist": publishable_watchlist[:10],
        "top_blocked_positive": blocked_positive[:10],
        "top_positive_all": all_positive[:10],
        "report_rejection_counts": report_counts,
        "candidate_final_rejection_counts": dict(candidate_rejection_counts.most_common(25)),
        "candidate_note_counts": dict(candidate_note_counts.most_common(25)),
        "family_counts": dict(family_counts),
        "ledger_path": str(OUT_LEDGER),
        "outputs": {
            "summary": str(OUT_SUMMARY),
            "all_positive_watchlist": str(OUT_WATCHLIST),
            "publishable_watchlist": str(OUT_PUBLISHABLE_WATCHLIST),
            "blocked_positive": str(OUT_BLOCKED_POSITIVE),
        },
    }

    write_json(OUT_SUMMARY, summary)
    write_json(OUT_WATCHLIST, all_positive)
    write_json(OUT_PUBLISHABLE_WATCHLIST, publishable_watchlist)
    write_json(OUT_BLOCKED_POSITIVE, blocked_positive)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
