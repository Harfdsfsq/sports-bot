from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.daily_coverage_common import atomic_write, load, state_path

ROOT = Path(__file__).resolve().parents[2]
STATE_EXPORT = ROOT / ".data" / "exports" / "latest-daily-coverage-state.json"
COHORT_EXPORT = ROOT / ".data" / "exports" / "latest-daily-coverage-cohort.json"
_INSTALLED = False
_ORIGINAL_NEXT_RUN = None
_ORIGINAL_RANK = None


def _restore(path: Path, mirror: Path, date_key: str) -> bool:
    current = load(path, {})
    if isinstance(current, dict) and current.get("date_local") == date_key:
        return False
    saved = load(mirror, {})
    if not isinstance(saved, dict) or saved.get("date_local") != date_key:
        return False
    atomic_write(path, saved)
    return True


def _mirror(path: Path, mirror: Path, date_key: str) -> bool:
    payload = load(path, {})
    if not isinstance(payload, dict) or payload.get("date_local") != date_key:
        return False
    atomic_write(mirror, payload)
    return True


def _next_run(date_key: str, now: datetime) -> tuple[int, str]:
    assert callable(_ORIGINAL_NEXT_RUN)
    path = state_path(date_key)
    _restore(path, STATE_EXPORT, date_key)
    result = _ORIGINAL_NEXT_RUN(date_key, now)
    _mirror(path, STATE_EXPORT, date_key)
    return result


def _rank(
    rows: list[dict[str, Any]],
    ledger: dict[str, Any],
    now: datetime,
    date_key: str,
) -> list[dict[str, Any]]:
    assert callable(_ORIGINAL_RANK)
    from app.services import daily_coverage_fixed_cohort_patch as cohort_patch

    path = cohort_patch._path(date_key)
    _restore(path, COHORT_EXPORT, date_key)
    ranked = _ORIGINAL_RANK(rows, ledger, now, date_key)
    _mirror(path, COHORT_EXPORT, date_key)
    return ranked


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_NEXT_RUN, _ORIGINAL_RANK
    if _INSTALLED:
        return {"status": "already_installed"}

    from app.services import daily_coverage_ledger as ledger_module
    from app.services import daily_coverage_plan as plan_module
    from app.services import daily_coverage_ranking as ranking_module

    _ORIGINAL_NEXT_RUN = plan_module._next_run
    _ORIGINAL_RANK = plan_module.rank_inventory
    plan_module._next_run = _next_run
    plan_module.rank_inventory = _rank
    ranking_module.rank_inventory = _rank
    ledger_module.rank_inventory = _rank
    _INSTALLED = True
    return {
        "status": "installed",
        "state_mirror": str(STATE_EXPORT),
        "cohort_mirror": str(COHORT_EXPORT),
        "restores_after_cache_miss": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
