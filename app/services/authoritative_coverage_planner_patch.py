"""Make the coverage planner consume strict synchronized provider evidence.

The strict inventory sync stores authoritative source lists in metadata. Some later
inventory rebuilds preserve that metadata while dropping the legacy top-level lists,
which made the planner think covered matches were empty and reassign all 300 rows on
every run. This patch merges only verified lists back into planner observations.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.daily_coverage_common import independent_sources

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".data" / "exports" / "latest-authoritative-coverage-planner.json"
_INSTALLED = False
_ORIGINAL_OBSERVED = None


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
    # Strict sync also writes the exact source list into coverage. Accept it only
    # when the row carries the sync marker, so proxy/legacy coverage is not promoted.
    if bool(coverage.get("daily_coverage_evidence_synced")):
        values.extend(_values(coverage, f"{role}_sources"))
    return independent_sources(values, role=role)


def _observed(row: dict[str, Any], ledger_row: dict[str, Any]) -> tuple[list[str], list[str]]:
    assert callable(_ORIGINAL_OBSERVED)
    odds, contexts = _ORIGINAL_OBSERVED(row, ledger_row)
    odds = independent_sources(
        list(odds) + _verified(row, "odds") + _verified(ledger_row, "odds"),
        role="odds",
    )
    contexts = independent_sources(
        list(contexts)
        + _verified(row, "context")
        + _verified(ledger_row, "context"),
        role="context",
    )
    return odds, contexts


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_OBSERVED
    if _INSTALLED:
        return {"status": "already_installed"}
    from app.services import daily_coverage_ranking

    current = daily_coverage_ranking._observed
    if getattr(current, "_harizon_authoritative_verified_evidence", False):
        _INSTALLED = True
        return {"status": "already_installed"}
    _ORIGINAL_OBSERVED = current
    _observed._harizon_authoritative_verified_evidence = True
    daily_coverage_ranking._observed = _observed
    _INSTALLED = True
    payload = {
        "status": "installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "planner_source_of_truth": "strict_sync_verified_metadata",
        "accepts_proxy_or_fixture_identity_as_evidence": False,
        "publication_contract_relaxed": False,
    }
    _write(payload)
    return payload


__all__ = ["install"]
