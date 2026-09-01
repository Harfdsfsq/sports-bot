"""Adaptive information-value selection for HARIZON.

The discovery inventory remains broad, but expensive provider work is concentrated
on matches that can plausibly become high-quality decisions.  The selector does not
require a fixed number of matches and never relaxes publication guards.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.services.daily_coverage_common import (
    EXPORT_DIR,
    as_float,
    as_int,
    atomic_write,
    independent_sources,
)
from app.services.focused_alpha_history import build_history_audit, league_prior
from app.utils import canonicalize_team_name, parse_datetime

REPORT_PATH = EXPORT_DIR / "latest-focused-alpha-cohort.json"


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def enabled() -> bool:
    return _truthy("FOCUSED_ALPHA_ENABLED", True)


def _limit(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(float(str(os.getenv(name) or default)))))
    except Exception:
        return default


def _float_limit(name: str, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(str(os.getenv(name) or default))))
    except Exception:
        return default


def max_matches() -> int:
    return _limit("FOCUSED_ALPHA_MAX_MATCHES", 100, 10, 180)


def phase_targets() -> tuple[int, int, int]:
    maximum = max_matches()
    raw = str(os.getenv("FOCUSED_ALPHA_PHASE_TARGETS") or "40,70,100")
    values: list[int] = []
    for item in raw.split(","):
        try:
            values.append(max(1, min(maximum, int(float(item.strip())))))
        except Exception:
            continue
    while len(values) < 3:
        values.append(maximum)
    values = sorted(values[:3])
    return values[0], values[1], values[2]


def _containers(row: dict[str, Any]) -> list[dict[str, Any]]:
    result = [row]
    seen = {id(row)}
    stack = [row]
    while stack and len(result) < 32:
        current = stack.pop()
        for key in (
            "metadata",
            "coverage",
            "source_summary",
            "model_inputs",
            "context",
            "quality",
            "diagnostics",
            "enrichment",
            "market_summary",
            "price_summary",
        ):
            value = current.get(key)
            if isinstance(value, dict) and id(value) not in seen:
                seen.add(id(value))
                result.append(value)
                stack.append(value)
    return result


def _values(row: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    result: list[Any] = []
    for box in _containers(row):
        for key in keys:
            value = box.get(key)
            if isinstance(value, dict):
                result.extend(value.keys())
            elif isinstance(value, (list, tuple, set)):
                result.extend(value)
            elif isinstance(value, str) and value.strip():
                result.extend(re.split(r"[,;+|/]", value))
    return result


def _number(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for box in _containers(row):
        for key in keys:
            value = box.get(key)
            if value in (None, "") or isinstance(value, bool):
                continue
            try:
                parsed = float(str(value).replace(",", "."))
                if math.isfinite(parsed):
                    values.append(parsed)
            except Exception:
                continue
    return max(values) if values else None


def _flag(row: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for box in _containers(row):
        for key in keys:
            value = box.get(key)
            if isinstance(value, bool) and value:
                return True
            if isinstance(value, (int, float)) and value > 0:
                return True
            if isinstance(value, str) and value.strip().lower() not in {"", "0", "false", "none", "null", "unknown"}:
                return True
    return False


def _strict_sources(row: dict[str, Any], role: str) -> list[str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    verified_key = "verified_odds_sources" if role == "odds" else "verified_context_sources"
    if verified_key in metadata:
        return independent_sources(metadata.get(verified_key), role=role)
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    values: list[Any] = []
    values.extend(row.get(f"{role}_sources") or [])
    values.extend(coverage.get(f"{role}_sources") or [])
    return independent_sources(values, role=role)


def _bookmaker_depth(row: dict[str, Any]) -> int:
    explicit = {
        str(value).strip().lower()
        for value in _values(
            row,
            (
                "verified_bookmakers",
                "bookmakers",
                "bookmaker_names",
                "price_confirmation_bookmakers",
                "exact_bookmakers",
            ),
        )
        if str(value).strip()
    }
    if explicit:
        return min(12, len(explicit))
    numeric = _number(
        row,
        (
            "bookmaker_count",
            "bookmakers_count",
            "latest_books_max",
            "price_confirmation_count",
            "price_confirmations_count",
        ),
    )
    # Legacy books_count often contains raw offer rows (100+).  Such values are not
    # bookmaker depth and receive no artificial bonus.
    if numeric is None or numeric > 25:
        return 0
    return max(0, min(12, int(numeric)))


def _provider_identity_count(row: dict[str, Any]) -> int:
    source_ids = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
    providers = {
        str(key).strip().lower()
        for key, value in source_ids.items()
        if str(key).strip() and value not in (None, "", [], {})
    }
    providers.update(_strict_sources(row, "odds"))
    providers.update(_strict_sources(row, "context"))
    return len(providers)


def _competition_flags(row: dict[str, Any]) -> dict[str, bool]:
    text = " ".join(
        str(row.get(key) or "").lower()
        for key in ("league_name", "home_team", "away_team")
    )
    return {
        "youth": any(token in text for token in ("u17", "u18", "u19", "u20", "u21", "u23", "youth")),
        "reserve": any(token in text for token in ("reserve", "reserves", "резерв")),
        "friendly": any(token in text for token in ("friendly", "товарищ")),
        "women": any(token in text for token in ("women", "woman", "femin", "женщ")),
    }


def _timing_score(hours: float) -> float:
    if hours < 0.33:
        return -100.0
    if hours <= 2.0:
        return 4.0
    if hours <= 12.0:
        return 12.0
    if hours <= 24.0:
        return 9.0
    if hours <= 36.0:
        return 5.0
    return 1.0


def _market_depth_score(row: dict[str, Any], bookmaker_depth: int) -> float:
    offers = _number(row, ("offers_count", "offer_count", "market_rows", "odds_rows")) or 0.0
    dispersion = _number(row, ("consensus_dispersion_pct", "price_dispersion_pct"))
    score = min(10.0, bookmaker_depth * 1.8) + min(4.0, math.log1p(max(0.0, offers)))
    if dispersion is not None:
        if dispersion <= 5.0:
            score += 3.0
        elif dispersion > 15.0:
            score -= 5.0
    return score


def _evidence_score(row: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    odds_sources = _strict_sources(row, "odds")
    context_sources = _strict_sources(row, "context")
    books = _bookmaker_depth(row)
    has_xg = _flag(
        row,
        (
            "has_xg",
            "xg",
            "expected_goals",
            "expected_home",
            "expected_away",
            "home_xg",
            "away_xg",
        ),
    )
    has_form = _flag(
        row,
        (
            "has_form",
            "form",
            "home_form",
            "away_form",
            "recent_form",
            "last_games_stats",
            "glicko",
            "elo",
        ),
    )
    provider_count = _provider_identity_count(row)
    score = 0.0
    score += (0.0, 13.0, 25.0)[min(2, len(odds_sources))]
    score += (0.0, 17.0, 31.0)[min(2, len(context_sources))]
    score += _market_depth_score(row, books)
    score += 10.0 if has_xg else 0.0
    score += 6.0 if has_form else 0.0
    score += min(8.0, provider_count * 1.5)

    # One known source plus a missing second source is an efficient enrichment
    # target.  Zero-evidence fixtures are expensive and uncertain.
    expected_gain = 0.0
    if len(odds_sources) == 1:
        expected_gain += 7.0
    elif len(odds_sources) == 0:
        expected_gain -= 8.0
    if len(context_sources) == 1:
        expected_gain += 9.0
    elif len(context_sources) == 0:
        expected_gain -= 12.0
    score += expected_gain
    return score, {
        "odds_sources": odds_sources,
        "context_sources": context_sources,
        "bookmaker_depth": books,
        "has_hard_xg_signal": has_xg,
        "has_form_signal": has_form,
        "provider_identity_count": provider_count,
        "expected_enrichment_gain_score": expected_gain,
    }


def score_match(row: dict[str, Any], history_report: dict[str, Any] | None = None) -> dict[str, Any]:
    hours = as_float(row.get("hours_to_kickoff"), 999.0)
    evidence_score, evidence = _evidence_score(row)
    timing = _timing_score(hours)
    flags = _competition_flags(row)
    prior = league_prior(row.get("league_name"), history_report)
    learned = prior["reliability"] * 3.0 + prior["profit_signal"] * 8.0
    identity = 0.0
    if row.get("match_key") and row.get("home_team") and row.get("away_team") and row.get("kickoff_utc"):
        identity += 8.0
    if bool(row.get("ledger_identity_match")):
        identity += 4.0
    penalty = 0.0
    penalty += 14.0 if flags["youth"] else 0.0
    penalty += 14.0 if flags["reserve"] else 0.0
    penalty += 9.0 if flags["friendly"] else 0.0
    penalty += 5.0 if flags["women"] else 0.0
    if not row.get("match_key") or not row.get("kickoff_utc"):
        penalty += 60.0
    if row.get("provider_assignment_eligible") is False:
        penalty += 100.0
    score = evidence_score + timing + identity + learned - penalty
    reasons: list[str] = []
    if evidence["odds_sources"]:
        reasons.append(f"odds_sources={len(evidence['odds_sources'])}")
    if evidence["context_sources"]:
        reasons.append(f"context_sources={len(evidence['context_sources'])}")
    if evidence["has_hard_xg_signal"]:
        reasons.append("xg_present")
    if evidence["has_form_signal"]:
        reasons.append("form_present")
    if evidence["expected_enrichment_gain_score"] > 0:
        reasons.append("high_expected_enrichment_gain")
    reasons.extend(name for name, value in flags.items() if value)
    return {
        "match_key": row.get("match_key"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "league_name": row.get("league_name"),
        "kickoff_utc": row.get("kickoff_utc"),
        "hours_to_kickoff": hours,
        "focused_alpha_score": round(score, 3),
        "components": {
            "evidence": round(evidence_score, 3),
            "timing": round(timing, 3),
            "identity": round(identity, 3),
            "learned_prior": round(learned, 3),
            "penalty": round(penalty, 3),
        },
        "evidence": evidence,
        "history_prior": prior,
        "flags": flags,
        "reasons": reasons,
    }


def _league_key(row: dict[str, Any]) -> str:
    return str(row.get("league_name") or "unknown").strip().lower()


def _kickoff_priority(hours: float) -> tuple[int, float]:
    """Return the rules-defined nearest-first collection bucket."""

    for index, upper in enumerate((4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 36.0)):
        if hours <= upper:
            return index, hours
    return 7, hours


def _routing_match_key(row: dict[str, Any]) -> str:
    """Build the provider/runtime identity without losing Unicode letters."""

    home = canonicalize_team_name(str(row.get("home_team") or ""))
    away = canonicalize_team_name(str(row.get("away_team") or ""))
    kickoff = row.get("kickoff_utc") or row.get("kickoff")
    if home and away and kickoff:
        try:
            date_key = parse_datetime(kickoff).astimezone(UTC).date().isoformat()
        except Exception:
            date_key = ""
        if date_key:
            return f"{date_key}|{home}|{away}"
    for name in (
        "canonical_match_key",
        "canonical_match_id",
        "semantic_match_key",
        "semantic_key",
        "match_key",
    ):
        value = str(row.get(name) or "").strip()
        if value:
            return value
    return ""


def select_focus_cohort(
    ranked_rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    history_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    audit = history_report or build_history_audit()
    minimum_score = as_float(os.getenv("FOCUSED_ALPHA_MIN_MATCH_SCORE"), 44.0)
    maximum = max_matches()
    max_per_league = _limit("FOCUSED_ALPHA_MAX_PER_LEAGUE", 10, 2, 30)
    exploration_slots = _limit("FOCUSED_ALPHA_EXPLORATION_SLOTS", 6, 0, 20)

    scored: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in ranked_rows:
        item = dict(row)
        source_match_key = str(item.get("match_key") or "").strip()
        routing_match_key = _routing_match_key(item)
        if routing_match_key:
            item["match_key"] = routing_match_key
        if source_match_key and source_match_key != routing_match_key:
            item["focused_alpha_source_match_key"] = source_match_key
        detail = score_match(item, audit)
        item["focused_alpha_score"] = detail["focused_alpha_score"]
        item["focused_alpha"] = detail
        scored.append((item, detail))
    scored.sort(
        key=lambda pair: (
            -as_float(pair[1].get("focused_alpha_score")),
            as_float(pair[0].get("hours_to_kickoff"), 999.0),
            str(pair[0].get("match_key") or ""),
        )
    )

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    league_counts: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    for row, detail in scored:
        key = str(row.get("match_key") or "")
        if not key or key in selected_keys:
            rejected["missing_or_duplicate_identity"] += 1
            continue
        if as_float(detail.get("focused_alpha_score")) < minimum_score:
            rejected["score_below_min"] += 1
            continue
        league = _league_key(row)
        if league_counts[league] >= max_per_league:
            rejected["league_diversity_cap"] += 1
            continue
        row["focused_alpha_selection_lane"] = "quality"
        selected.append(row)
        selected_keys.add(key)
        league_counts[league] += 1
        if len(selected) >= max(0, maximum - exploration_slots):
            break

    # Small exploration lane avoids permanently excluding competitions with little
    # history.  It remains subject to identity, timing and evidence constraints and
    # is never a publication entitlement.
    if exploration_slots > 0 and len(selected) < maximum:
        for row, detail in scored:
            key = str(row.get("match_key") or "")
            if not key or key in selected_keys:
                continue
            if as_float(detail.get("focused_alpha_score")) < minimum_score - 12.0:
                continue
            evidence = detail.get("evidence") if isinstance(detail.get("evidence"), dict) else {}
            if not evidence.get("odds_sources") or not evidence.get("context_sources"):
                continue
            league = _league_key(row)
            if league_counts[league] >= max_per_league:
                continue
            row = dict(row)
            row["focused_alpha_exploration"] = True
            row["focused_alpha_selection_lane"] = "exploration"
            selected.append(row)
            selected_keys.add(key)
            league_counts[league] += 1
            if len(selected) >= maximum:
                break

    quality_selected_rows = len(selected)
    bootstrap_slots = _limit(
        "FOCUSED_ALPHA_BOOTSTRAP_MATCHES",
        24,
        0,
        maximum,
    )
    bootstrap_max_hours = float(
        _limit("FOCUSED_ALPHA_BOOTSTRAP_MAX_HOURS", 36, 4, 72)
    )
    active_provider_rows = sum(
        1
        for row, detail in scored
        if row.get("provider_assignment_eligible") is not False
        and 0.33
        <= as_float(detail.get("hours_to_kickoff"), 999.0)
        <= bootstrap_max_hours
    )
    bootstrap_selected: list[dict[str, Any]] = []

    # A quality target outside the current publication window must not make the
    # whole run blind to matches that will start before the next scheduled run.
    # Add a bounded enrichment-only bridge when the quality cohort and the active
    # runner window do not overlap.  This changes provider targeting only; every
    # publication guard remains authoritative downstream.
    publish_window_hours = _float_limit(
        "PUBLISH_WINDOW_HOURS",
        2.0,
        0.5,
        48.0,
    )
    min_lead_hours = _float_limit(
        "MIN_KICKOFF_LEAD_MINUTES",
        20.0,
        0.0,
        240.0,
    ) / 60.0
    run_window_bridge_slots = _limit(
        "FOCUSED_ALPHA_RUN_WINDOW_BRIDGE_MATCHES",
        24,
        0,
        maximum,
    )
    selected_in_run_window = sum(
        1
        for row in selected
        if min_lead_hours
        <= as_float(row.get("hours_to_kickoff"), 999.0)
        <= publish_window_hours
    )
    run_window_bridge_selected: list[dict[str, Any]] = []
    if selected and not selected_in_run_window and run_window_bridge_slots > 0:
        bridge_candidates = sorted(
            scored,
            key=lambda pair: (
                as_float(pair[1].get("hours_to_kickoff"), 999.0),
                sum(
                    bool((pair[1].get("flags") or {}).get(name))
                    for name in ("youth", "reserve", "friendly")
                ),
                -(
                    len((pair[1].get("evidence") or {}).get("odds_sources") or [])
                    + len(
                        (pair[1].get("evidence") or {}).get("context_sources")
                        or []
                    )
                ),
                -as_float(pair[1].get("focused_alpha_score")),
                str(pair[0].get("match_key") or ""),
            ),
        )
        for row, detail in bridge_candidates:
            key = str(row.get("match_key") or "")
            hours = as_float(detail.get("hours_to_kickoff"), 999.0)
            if (
                not key
                or key in selected_keys
                or row.get("provider_assignment_eligible") is False
                or hours < min_lead_hours
                or hours > publish_window_hours
                or not row.get("home_team")
                or not row.get("away_team")
                or not row.get("kickoff_utc")
            ):
                continue
            league = _league_key(row)
            if league_counts[league] >= max_per_league:
                continue
            item = dict(row)
            item["focused_alpha_run_window_bridge"] = True
            item["focused_alpha_selection_lane"] = "run_window_bridge_enrichment"
            item["focused_alpha_run_window_bridge_reason"] = (
                "quality_cohort_outside_publish_window"
            )
            selected.append(item)
            run_window_bridge_selected.append(item)
            selected_keys.add(key)
            league_counts[league] += 1
            if (
                len(run_window_bridge_selected) >= run_window_bridge_slots
                or len(selected) >= maximum
            ):
                break

    # Cold-start recovery: a match cannot be required to already have providers
    # before it is allowed to call those providers. This lane only chooses a
    # bounded enrichment cohort; all value, price-integrity, coverage and
    # line-movement publication guards remain authoritative.
    if not selected and bootstrap_slots > 0:
        bootstrap_candidates = sorted(
            scored,
            key=lambda pair: (
                *_kickoff_priority(
                    as_float(pair[1].get("hours_to_kickoff"), 999.0)
                ),
                sum(
                    bool((pair[1].get("flags") or {}).get(name))
                    for name in ("youth", "reserve", "friendly")
                ),
                -(
                    len((pair[1].get("evidence") or {}).get("odds_sources") or [])
                    + len(
                        (pair[1].get("evidence") or {}).get("context_sources")
                        or []
                    )
                ),
                -as_float(pair[1].get("focused_alpha_score")),
                str(pair[0].get("match_key") or ""),
            ),
        )
        for row, detail in bootstrap_candidates:
            key = str(row.get("match_key") or "")
            hours = as_float(detail.get("hours_to_kickoff"), 999.0)
            if (
                not key
                or key in selected_keys
                or row.get("provider_assignment_eligible") is False
                or hours < 0.33
                or hours > bootstrap_max_hours
                or not row.get("home_team")
                or not row.get("away_team")
                or not row.get("kickoff_utc")
            ):
                continue
            league = _league_key(row)
            if league_counts[league] >= max_per_league:
                continue
            item = dict(row)
            item["focused_alpha_bootstrap"] = True
            item["focused_alpha_selection_lane"] = "bootstrap_enrichment"
            item["focused_alpha_bootstrap_reason"] = (
                "no_quality_cohort_before_provider_enrichment"
            )
            selected.append(item)
            bootstrap_selected.append(item)
            selected_keys.add(key)
            league_counts[league] += 1
            if len(bootstrap_selected) >= bootstrap_slots or len(selected) >= maximum:
                break

    phase = phase_targets()
    health_status = "ok"
    if active_provider_rows > 0 and not selected:
        health_status = "blocked_active_inventory_without_provider_targets"
    elif bootstrap_selected:
        health_status = "cold_start_bootstrap_active"
    elif run_window_bridge_selected:
        health_status = "run_window_bridge_active"
    report = {
        "status": health_status,
        "created_at_utc": current.isoformat(),
        "mode": "focused_alpha_information_value",
        "discovery_rows_seen": len(ranked_rows),
        "selected_rows": len(selected),
        "quality_selected_rows": quality_selected_rows,
        "bootstrap_selected_rows": len(bootstrap_selected),
        "bootstrap_triggered": bool(bootstrap_selected),
        "bootstrap_reason": (
            "no_quality_cohort_before_provider_enrichment"
            if bootstrap_selected
            else None
        ),
        "bootstrap_match_keys": [
            row["match_key"] for row in bootstrap_selected
        ],
        "bootstrap_max_hours": bootstrap_max_hours,
        "run_window_bridge_triggered": bool(run_window_bridge_selected),
        "run_window_bridge_selected_rows": len(run_window_bridge_selected),
        "run_window_bridge_match_keys": [
            row["match_key"] for row in run_window_bridge_selected
        ],
        "run_window_hours": publish_window_hours,
        "run_window_min_lead_hours": round(min_lead_hours, 3),
        "quality_targets_in_run_window_before_bridge": selected_in_run_window,
        "active_provider_eligible_rows": active_provider_rows,
        "selected_unique_keys": len(selected_keys),
        "max_matches": maximum,
        "minimum_score": minimum_score,
        "phase_targets": list(phase),
        "max_per_league": max_per_league,
        "exploration_slots": exploration_slots,
        "history_live_learning_ready": bool(audit.get("live_learning_ready")),
        "history_settled_rows": as_int(audit.get("settled_rows")),
        "history_thresholds_auto_tuned": False,
        "selection_objective": "maximize_expected_information_and_risk_adjusted_decision_quality",
        "fixed_coverage_quota": False,
        "publication_minimum_count": 0,
        "publication_contract_relaxed": False,
        "rejection_counts": dict(rejected),
        "league_counts": dict(league_counts),
        "selected_match_keys": [row["match_key"] for row in selected],
        "selected": [
            {
                "match_key": row.get("match_key"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "league_name": row.get("league_name"),
                "kickoff_utc": row.get("kickoff_utc"),
                "hours_to_kickoff": row.get("hours_to_kickoff"),
                "focused_alpha_score": row.get("focused_alpha_score"),
                "focused_alpha_exploration": bool(row.get("focused_alpha_exploration")),
                "focused_alpha_bootstrap": bool(row.get("focused_alpha_bootstrap")),
                "focused_alpha_run_window_bridge": bool(
                    row.get("focused_alpha_run_window_bridge")
                ),
                "selection_lane": row.get("focused_alpha_selection_lane"),
                "components": (row.get("focused_alpha") or {}).get("components"),
                "evidence": (row.get("focused_alpha") or {}).get("evidence"),
                "reasons": (row.get("focused_alpha") or {}).get("reasons"),
            }
            for row in selected
        ],
        "top_rejected": [
            {
                "match_key": row.get("match_key"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "league_name": row.get("league_name"),
                "score": detail.get("focused_alpha_score"),
                "components": detail.get("components"),
                "evidence": detail.get("evidence"),
            }
            for row, detail in scored
            if str(row.get("match_key") or "") not in selected_keys
        ][:30],
    }
    atomic_write(REPORT_PATH, report)
    return {"rows": selected, "report": report, "history": audit}


__all__ = [
    "REPORT_PATH",
    "enabled",
    "max_matches",
    "phase_targets",
    "score_match",
    "select_focus_cohort",
]
