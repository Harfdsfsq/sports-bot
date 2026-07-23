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
from app.services.daily_coverage_identity import (
    build_ledger_identity_index,
    lookup_ledger_row,
)

_INSTALLED = False
_ORIGINAL_RANK = None


def _path(date_key: str) -> Path:
    return DAY_DIR / f"daily-coverage-cohort-{date_key}.json"


def _values(box: Any, key: str) -> list[str]:
    if not isinstance(box, dict):
        return []
    value = box.get(key)
    if isinstance(value, dict):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _verified(row: Any, role: str) -> list[str]:
    if not isinstance(row, dict):
        return []
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    key = "verified_odds_sources" if role == "odds" else "verified_context_sources"
    values = _values(metadata, key)
    if bool(coverage.get("daily_coverage_evidence_synced")):
        values.extend(_values(coverage, f"{role}_sources"))
    return independent_sources(values, role=role)


def _coverage(
    row: dict[str, Any], ledger_row: dict[str, Any]
) -> tuple[list[str], list[str]]:
    odds = independent_sources(
        list(ledger_row.get("odds_sources") or [])
        + _verified(row, "odds")
        + _verified(ledger_row, "odds"),
        role="odds",
    )
    contexts = independent_sources(
        list(ledger_row.get("context_sources") or [])
        + _verified(row, "context")
        + _verified(ledger_row, "context"),
        role="context",
    )
    return odds, contexts


def _refresh(
    cohort: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    ledger: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    current_by_key = {row_key(row): row for row in rows if row_key(row)}
    ledger_rows = ledger.get("matches") if isinstance(ledger.get("matches"), dict) else {}
    identity_index = build_ledger_identity_index(ledger_rows)
    result: list[dict[str, Any]] = []
    for stored in cohort:
        key = row_key(stored)
        if not key:
            continue
        base = dict(stored)
        base.update(current_by_key.get(key) or {})
        kickoff = row_kickoff(base)
        ledger_row = lookup_ledger_row(
            base,
            key,
            kickoff,
            ledger_rows,
            identity_index,
        )
        odds, contexts = _coverage(base, ledger_row)
        hours = (
            (kickoff - now).total_seconds() / 3600.0
            if kickoff is not None
            else 9999.0
        )
        assignment_eligible = kickoff is not None and hours >= -0.25
        base.update(
            {
                "match_key": key,
                "kickoff_utc": (
                    kickoff.isoformat()
                    if kickoff is not None
                    else base.get("kickoff_utc")
                ),
                "hours_to_kickoff": round(hours, 3),
                "odds_sources": odds,
                "context_sources": contexts,
                "odds_sources_count": len(odds),
                "context_sources_count": len(contexts),
                "line_deficit": max(0, MIN_ODDS_SOURCES - len(odds)),
                "context_deficit": max(0, MIN_CONTEXT_SOURCES - len(contexts)),
                "daily_cohort_fixed": True,
                "ledger_identity_match": bool(ledger_row),
                "provider_assignment_eligible": assignment_eligible,
                "provider_assignment_skip_reason": (
                    None if assignment_eligible else "kickoff_expired_or_missing"
                ),
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
    stored = (
        state.get("matches")
        if isinstance(state, dict) and isinstance(state.get("matches"), list)
        else []
    )
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
                "created_at_utc": (
                    state.get("created_at_utc") if isinstance(state, dict) else None
                )
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
        "policy": "retain_daily_300_but_assign_only_active_verified_gaps",
        "past_matches_remain_in_daily_coverage_ledger": True,
        "past_matches_excluded_from_provider_assignments": True,
        "strict_verified_evidence_restored": True,
        "semantic_ledger_identity_lookup": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
