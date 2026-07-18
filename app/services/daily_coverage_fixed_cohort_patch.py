from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.daily_coverage_common import (
    DAY_DIR,
    MIN_CONTEXT_SOURCES,
    MIN_ODDS_SOURCES,
    TARGET_MATCHES,
    atomic_write,
    independent_sources,
    load,
    row_key,
    row_kickoff,
)

_INSTALLED = False
_ORIGINAL_RANK = None


def _path(date_key: str) -> Path:
    return DAY_DIR / f"daily-coverage-cohort-{date_key}.json"


def _coverage(row: dict[str, Any], ledger_row: dict[str, Any]) -> tuple[list[str], list[str]]:
    del row
    odds = independent_sources(ledger_row.get("odds_sources") or [], role="odds")
    contexts = independent_sources(ledger_row.get("context_sources") or [], role="context")
    return odds, contexts


def _refresh(
    cohort: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    ledger: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    current_by_key = {row_key(row): row for row in rows if row_key(row)}
    ledger_rows = ledger.get("matches") if isinstance(ledger.get("matches"), dict) else {}
    result: list[dict[str, Any]] = []
    for stored in cohort:
        key = row_key(stored)
        if not key:
            continue
        base = dict(stored)
        base.update(current_by_key.get(key) or {})
        kickoff = row_kickoff(base)
        ledger_row = ledger_rows.get(key) if isinstance(ledger_rows.get(key), dict) else {}
        odds, contexts = _coverage(base, ledger_row)
        hours = (kickoff - now).total_seconds() / 3600.0 if kickoff is not None else 9999.0
        base.update(
            {
                "match_key": key,
                "kickoff_utc": kickoff.isoformat() if kickoff is not None else base.get("kickoff_utc"),
                "hours_to_kickoff": round(hours, 3),
                "odds_sources": odds,
                "context_sources": contexts,
                "odds_sources_count": len(odds),
                "context_sources_count": len(contexts),
                "line_deficit": max(0, MIN_ODDS_SOURCES - len(odds)),
                "context_deficit": max(0, MIN_CONTEXT_SOURCES - len(contexts)),
                "daily_cohort_fixed": True,
            }
        )
        result.append(base)
    return result[:TARGET_MATCHES]


def _rank(
    rows: list[dict[str, Any]],
    ledger: dict[str, Any],
    now: datetime,
    date_key: str,
) -> list[dict[str, Any]]:
    assert callable(_ORIGINAL_RANK)
    path = _path(date_key)
    state = load(path, {})
    stored = state.get("matches") if isinstance(state, dict) and isinstance(state.get("matches"), list) else []
    existing_keys = {row_key(row) for row in stored if row_key(row)}
    if len(stored) < TARGET_MATCHES:
        ranked = _ORIGINAL_RANK(rows, ledger, now, date_key)
        for row in ranked:
            key = row_key(row)
            if key and key not in existing_keys:
                existing_keys.add(key)
                stored.append(row)
                if len(stored) >= TARGET_MATCHES:
                    break
        atomic_write(
            path,
            {
                "date_local": date_key,
                "created_at_utc": state.get("created_at_utc") if isinstance(state, dict) else None
                or datetime.now(UTC).isoformat(),
                "updated_at_utc": datetime.now(UTC).isoformat(),
                "target_matches": TARGET_MATCHES,
                "frozen": len(stored) >= TARGET_MATCHES,
                "matches": stored[:TARGET_MATCHES],
                "publication_contract_relaxed": False,
            },
        )
    return _refresh(stored, rows, ledger, now)


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_RANK
    if _INSTALLED:
        return {"status": "already_installed"}
    from app.services import daily_coverage_ledger as ledger_module
    from app.services import daily_coverage_plan as plan_module
    from app.services import daily_coverage_ranking as ranking_module

    current = ranking_module.rank_inventory
    if getattr(current, "_harizon_fixed_daily_cohort", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_RANK = current
    _rank._harizon_fixed_daily_cohort = True
    ranking_module.rank_inventory = _rank
    plan_module.rank_inventory = _rank
    ledger_module.rank_inventory = _rank
    _INSTALLED = True
    return {
        "status": "installed",
        "target_matches": TARGET_MATCHES,
        "policy": "append_until_300_then_freeze_for_moscow_day",
        "past_matches_remain_in_daily_coverage_ledger": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
