from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

from app.services.daily_coverage_common import (
    MIN_CONTEXT_SOURCES,
    MIN_ODDS_SOURCES,
    TARGET_MATCHES,
    app_timezone,
    as_float,
    as_int,
    independent_sources,
    row_key,
    row_kickoff,
)
from app.services.daily_coverage_identity import (
    build_ledger_identity_index,
    lookup_ledger_row,
)


def coverage_horizon_days() -> int:
    for name in (
        "DAY_INVENTORY_HORIZON_DAYS",
        "DAY_INVENTORY_TARGET_HORIZON_DAYS",
        "RUN_DAYS_AHEAD",
    ):
        raw = os.getenv(name)
        if raw is None or not str(raw).strip():
            continue
        try:
            return max(1, min(4, int(float(str(raw)))))
        except (TypeError, ValueError):
            continue
    return 2


def horizon_day_offset(kickoff: datetime, date_key: str) -> int | None:
    try:
        start = date.fromisoformat(str(date_key)[:10])
    except ValueError:
        return 0
    current = kickoff.astimezone(app_timezone()).date()
    offset = (current - start).days
    return offset if 0 <= offset < coverage_horizon_days() else None


def _bucket(hours: float) -> tuple[int, str]:
    for index, edge in enumerate((4, 8, 12, 16, 20, 24)):
        if hours < edge:
            return index, f"{0 if index == 0 else edge - 4}_{edge}h"
    return 6, "24h_plus"


def _league_penalty(row: dict[str, Any]) -> int:
    text = " ".join(
        str(row.get(key) or "").lower()
        for key in ("league_name", "home_team", "away_team")
    )
    penalty = (
        3
        if any(
            token in text
            for token in (
                "u17",
                "u18",
                "u19",
                "u20",
                "u21",
                "u23",
                "youth",
                "reserve",
            )
        )
        else 0
    )
    penalty += 2 if any(token in text for token in ("women", "femin", "friendly")) else 0
    return penalty


def _observed(
    row: dict[str, Any], ledger_row: dict[str, Any]
) -> tuple[list[str], list[str]]:
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    odds = independent_sources(
        list(row.get("odds_sources") or [])
        + list(coverage.get("odds_sources") or [])
        + list(ledger_row.get("odds_sources") or []),
        role="odds",
    )
    contexts = independent_sources(
        list(row.get("context_sources") or [])
        + list(coverage.get("context_sources") or [])
        + list(ledger_row.get("context_sources") or []),
        role="context",
    )
    return odds, contexts


def rank_inventory(
    rows: list[dict[str, Any]],
    ledger: dict[str, Any],
    now: datetime,
    date_key: str,
) -> list[dict[str, Any]]:
    ledger_matches = (
        ledger.get("matches") if isinstance(ledger.get("matches"), dict) else {}
    )
    identity_index = build_ledger_identity_index(ledger_matches)
    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for row in rows:
        key, kickoff = row_key(row), row_kickoff(row)
        if not key or kickoff is None:
            continue
        day_offset = horizon_day_offset(kickoff, date_key)
        if day_offset is None:
            continue
        hours = (kickoff - now).total_seconds() / 3600.0
        if hours < -0.25:
            continue
        ledger_row = lookup_ledger_row(
            row,
            key,
            kickoff,
            ledger_matches,
            identity_index,
        )
        odds, contexts = _observed(row, ledger_row)
        source_ids = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
        provider_bonus = sum(
            bool(source_ids.get(name))
            for name in (
                "odds_api_io",
                "sportlogic",
                "bzzoiro",
                "sstats",
                "football_data",
            )
        )
        bucket, bucket_name = _bucket(hours)
        item = dict(row)
        item.update(
            {
                "match_key": key,
                "kickoff_utc": kickoff.isoformat(),
                "hours_to_kickoff": round(hours, 3),
                "time_bucket": bucket_name,
                "horizon_day_offset": day_offset,
                "coverage_horizon_days": coverage_horizon_days(),
                "odds_sources": odds,
                "context_sources": contexts,
                "odds_sources_count": len(odds),
                "context_sources_count": len(contexts),
                "line_deficit": max(0, MIN_ODDS_SOURCES - len(odds)),
                "context_deficit": max(0, MIN_CONTEXT_SOURCES - len(contexts)),
                "ledger_identity_match": bool(ledger_row),
            }
        )
        rank = (
            bucket,
            _league_penalty(row),
            -(bool(odds) + bool(contexts)),
            -provider_bonus,
            -as_float(row.get("priority")),
            kickoff,
            key,
        )
        ranked.append((rank, item))
    ranked.sort(key=lambda item: item[0])
    return [item for _, item in ranked[:TARGET_MATCHES]]


def coverage_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "matches": len(rows),
        "with_1plus_odds_sources": sum(
            as_int(row.get("odds_sources_count")) >= 1 for row in rows
        ),
        "with_2plus_odds_sources": sum(
            as_int(row.get("odds_sources_count")) >= 2 for row in rows
        ),
        "with_1plus_context_sources": sum(
            as_int(row.get("context_sources_count")) >= 1 for row in rows
        ),
        "with_2plus_context_sources": sum(
            as_int(row.get("context_sources_count")) >= 2 for row in rows
        ),
        "with_2plus_both": sum(
            as_int(row.get("odds_sources_count")) >= 2
            and as_int(row.get("context_sources_count")) >= 2
            for row in rows
        ),
        "with_semantic_ledger_match": sum(bool(row.get("ledger_identity_match")) for row in rows),
        "horizon_day_0": sum(as_int(row.get("horizon_day_offset"), -1) == 0 for row in rows),
        "horizon_day_1plus": sum(as_int(row.get("horizon_day_offset"), -1) >= 1 for row in rows),
    }


__all__ = [
    "coverage_horizon_days",
    "coverage_summary",
    "horizon_day_offset",
    "rank_inventory",
]
