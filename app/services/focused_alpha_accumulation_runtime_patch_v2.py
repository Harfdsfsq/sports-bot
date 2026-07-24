"""Repairs for the settlement-backed Focused Alpha accumulation lane.

The first accumulation implementation intentionally reused the existing shadow-bet
settlement path. This compatibility layer keeps that design while enforcing four
invariants across production runs:

* a zero remaining daily allowance can never select another observation;
* decision identities are canonical across selection-label and team-order variants;
* stored provider evidence comes only from the strict evidence-truth service;
* CLV is computed only from a genuinely later near-kickoff price snapshot.

Nothing in this module grants publication rights or relaxes live guards.
"""

from __future__ import annotations

import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services import focused_alpha_accumulation_runtime_patch as base
from app.services.focused_alpha_evidence_truth import evidence_truth

_ORIGINAL_ACCUMULATE = base.accumulate
_ORIGINAL_CHOOSE = base._choose
_PATCHED = False


def _strict_evidence(row: dict[str, Any]) -> dict[str, list[str]]:
    raw = row.get("raw_decision_snapshot")
    source = raw if isinstance(raw, dict) else row
    truth = evidence_truth(source)
    return {
        "odds_sources": list(truth.get("odds_sources") or []),
        "context_sources": list(truth.get("context_sources") or []),
        "bookmakers": list(truth.get("bookmakers") or []),
        "xg_sources": list(truth.get("xg_sources") or []),
    }


def _kickoff_date(row: dict[str, Any]) -> str:
    kickoff = base._dt(row.get("commence_time") or row.get("kickoff_utc"))
    return kickoff.date().isoformat() if kickoff is not None else ""


def _canonical_decision_key(row: dict[str, Any]) -> str:
    home = base._norm(row.get("home_team") or row.get("home"))
    away = base._norm(row.get("away_team") or row.get("away"))
    teams = sorted(value for value in (home, away) if value)
    team_key = "|".join(teams)
    if len(teams) < 2:
        team_key = base._norm(row.get("match_key") or row.get("canonical_match_id"))
    family = base._norm(row.get("family") or row.get("market_family"))
    selection = base._selection(row.get("selection_key") or row.get("selection"))
    point = base._point(row.get("point") or row.get("line") or row.get("handicap"))
    return "|".join((_kickoff_date(row), team_key, family, selection, point))


def _canonical_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    old_key = base._text(normalized.get("decision_key"))
    new_key = _canonical_decision_key(normalized)
    if new_key.strip("|"):
        normalized["decision_key"] = new_key
        normalized["selection_key"] = base._selection(
            normalized.get("selection_key") or normalized.get("selection")
        )
        if old_key and old_key != new_key:
            normalized["source_decision_key"] = old_key
    return normalized


def _canonical_board(board: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(board, dict):
        return board
    payload = deepcopy(board)
    for key in ("ranked", "selected_shadow"):
        rows = payload.get(key)
        if isinstance(rows, list):
            payload[key] = [
                _canonical_row(row)
                for row in rows
                if isinstance(row, dict)
            ]
    payload["accumulation_identity_version"] = "semantic_teams_date_market_v2"
    return payload


def _bounded_choose(
    ranked: list[dict[str, Any]],
    selections: dict[str, dict[str, Any]],
    strict_keys: set[str],
    snapshot_at: str,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, float | int]]:
    limits = base._limits()
    day = snapshot_at[:10]
    selected_today = {
        key
        for key, row in selections.items()
        if base._text(row.get("selected_at_utc"))[:10] == day
    }
    remaining = max(0, int(limits["daily_max"]) - len(selected_today | strict_keys))
    if remaining <= 0:
        return [], {"daily_cap_reached": 1}, limits
    return _ORIGINAL_CHOOSE(ranked, selections, strict_keys, snapshot_at)


def _refresh_closing_v2(
    selections: dict[str, dict[str, Any]],
    observations: list[dict[str, Any]],
    as_of: datetime,
) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in observations:
        if isinstance(row, dict):
            grouped.setdefault(base._text(row.get("decision_key")), []).append(row)

    max_minutes_before_kickoff = max(
        15,
        int(os.getenv("FOCUSED_ALPHA_CLOSING_MAX_MINUTES_BEFORE_KICKOFF", "240") or 240),
    )
    minimum_snapshot_gap_minutes = max(
        1,
        int(os.getenv("FOCUSED_ALPHA_CLOSING_MIN_SNAPSHOT_GAP_MINUTES", "5") or 5),
    )

    for key, selection in selections.items():
        kickoff = base._dt(selection.get("kickoff_utc"))
        selected_at = base._dt(
            selection.get("selected_at_utc") or selection.get("snapshot_at_utc")
        )
        rows = sorted(
            grouped.get(key, []),
            key=lambda row: base._text(row.get("snapshot_at_utc")),
        )
        pre_kickoff = [
            row
            for row in rows
            if base._dt(row.get("snapshot_at_utc")) is not None
            and (kickoff is None or base._dt(row.get("snapshot_at_utc")) <= kickoff)
            and base._float(row.get("odds")) > 1.0
        ]
        distinct_by_time = {
            base._dt(row.get("snapshot_at_utc")): row
            for row in pre_kickoff
            if base._dt(row.get("snapshot_at_utc")) is not None
        }
        distinct_rows = [
            distinct_by_time[stamp]
            for stamp in sorted(distinct_by_time)
        ]
        closing = distinct_rows[-1] if distinct_rows else None
        closing_at = base._dt(closing.get("snapshot_at_utc")) if closing else None
        finalized_by_clock = bool(kickoff is not None and kickoff <= as_of)
        later_than_selection = bool(
            selected_at is not None
            and closing_at is not None
            and closing_at >= selected_at + timedelta(minutes=minimum_snapshot_gap_minutes)
        )
        near_kickoff = bool(
            kickoff is not None
            and closing_at is not None
            and timedelta(0) <= kickoff - closing_at <= timedelta(minutes=max_minutes_before_kickoff)
        )
        sufficient = len(distinct_rows) >= 2 and later_than_selection and near_kickoff

        selection["snapshots"] = len(rows)
        selection["distinct_pre_kickoff_snapshots"] = len(distinct_rows)
        selection["latest_snapshot_at_utc"] = (
            rows[-1].get("snapshot_at_utc") if rows else None
        )
        selection["closing_odds_candidate"] = closing.get("odds") if closing else None
        selection["closing_snapshot_at_utc"] = (
            closing.get("snapshot_at_utc") if closing else None
        )
        selection["closing_price_finalized"] = finalized_by_clock and sufficient
        if not finalized_by_clock:
            selection["closing_price_status"] = "kickoff_not_reached"
        elif len(distinct_rows) < 2:
            selection["closing_price_status"] = "insufficient_distinct_snapshots"
        elif not later_than_selection:
            selection["closing_price_status"] = "no_later_snapshot_after_selection"
        elif not near_kickoff:
            selection["closing_price_status"] = "latest_snapshot_not_near_kickoff"
        else:
            selection["closing_price_status"] = "finalized_from_later_near_kickoff_snapshot"

        taken = base._float(selection.get("odds"))
        close = base._float(selection.get("closing_odds_candidate"))
        selection["clv_pct"] = (
            round((taken / close - 1.0) * 100.0, 4)
            if selection["closing_price_finalized"] and taken > 1.0 and close > 1.0
            else None
        )


def _accumulate_v2(board: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _ORIGINAL_ACCUMULATE(_canonical_board(board))
    if isinstance(result, dict):
        result["version"] = "focused_alpha_accumulation_v3_valid_clv"
        result["identity_policy"] = (
            "sorted_team_pair_kickoff_date_family_selection_point"
        )
        result["evidence_truth_basis"] = (
            "explicit_provider_and_exact_offer_identities"
        )
        result["clv_policy"] = (
            "two_distinct_snapshots_later_than_selection_and_near_kickoff"
        )
        result["publication_contract_relaxed"] = False
        result["telegram_publication_enabled"] = False
        try:
            base._write(base.ACCUMULATION_PATH, result)
        except Exception:
            pass
    return result


def install() -> dict[str, Any]:
    global _PATCHED
    if not _PATCHED:
        base._evidence = _strict_evidence
        base._choose = _bounded_choose
        base._refresh_closing = _refresh_closing_v2
        base.accumulate = _accumulate_v2
        _PATCHED = True
    installed = base.install()
    accepted_statuses = {"installed", "already_installed"}
    status = (
        "installed"
        if installed.get("status") in accepted_statuses
        else installed.get("status")
    )
    return {
        "status": status,
        "base_install": installed,
        "version": "focused_alpha_accumulation_v3_valid_clv",
        "publication_contract_relaxed": False,
        "telegram_publication_enabled": False,
        "installed_at_utc": datetime.now(UTC).isoformat(),
    }


__all__ = [
    "_bounded_choose",
    "_canonical_board",
    "_canonical_decision_key",
    "_canonical_row",
    "_refresh_closing_v2",
    "_strict_evidence",
    "install",
]
