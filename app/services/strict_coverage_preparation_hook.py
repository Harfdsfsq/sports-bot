"""Rebuild, re-rank and replan all 300 real fixtures before providers run."""

from __future__ import annotations

import os
from typing import Any

_INSTALLED = False
_ORIGINAL_PREPARE = None


def _present(name: str) -> bool:
    return bool(str(os.getenv(name) or "").strip())


def _set_full_cohort_runtime() -> dict[str, bool]:
    values = {
        "RUNBOT_DISCOVERY_FIRST_MAX_SECONDS": "240",
        "RUNBOT_DISCOVERY_FIRST_FINAL_RESERVE_SECONDS": "20",
        "HARIZON_SSTATS_PARI_WALL_SECONDS": "180",
        "SSTATS_PARI_RATE_LIMIT_PER_MINUTE": "140",
        "SSTATS_PARI_RATE_LIMIT_WINDOW_SECONDS": "60",
        "SSTATS_PARI_CONCURRENCY": "16",
        "SSTATS_PARI_DETAIL_MATCH_LIMIT": "300",
        "BZZOIRO_RUNTIME_DETAIL_MATCH_LIMIT": "300",
        "MAX_MATCHES_FOR_ODDS_FETCH": "300",
        "CONTEXT_ENRICHMENT_MATCH_LIMIT": "300",
    }
    activated = {"api_football": _present("API_FOOTBALL_KEY")}
    if activated["api_football"]:
        values.update(
            {
                "API_FOOTBALL_ENABLED": "true",
                "ENABLE_API_FOOTBALL": "true",
                "API_FOOTBALL_PER_RUN_MAX": "7",
                "API_FOOTBALL_MAX_HTTP_REQUESTS_PER_RUN": "7",
                "API_FOOTBALL_CONTEXT_MATCH_LIMIT": "300",
            }
        )
    for key, value in values.items():
        os.environ[key] = value
    try:
        from app.services import runtime_preflight

        runtime_preflight.AUTONOMOUS_ACCUMULATION_POLICY.update(values)
    except Exception:
        pass
    return activated


def _rebuild_real_inventory() -> dict[str, Any]:
    try:
        from app.services.strict_real_fixture_inventory import rebuild

        return rebuild()
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _replan() -> dict[str, Any]:
    from app.services import daily_coverage_plan
    from app.services.strict_coverage_inventory_sync import sync

    activated = _set_full_cohort_runtime()
    daily_coverage_plan.PHASE_TARGETS = (300, 300, 300)
    real_inventory = _rebuild_real_inventory()
    synced = sync()
    plan = daily_coverage_plan.prepare_daily_coverage()
    return {
        "real_fixture_inventory": real_inventory,
        "inventory_sync": synced,
        "plan_status": plan.get("status"),
        "inventory_rows_seen": plan.get("inventory_rows_seen"),
        "phase_cumulative_target": plan.get("phase_cumulative_target"),
        "keyed_independent_providers": activated,
        "provider_assignments": {
            provider: {role: len(keys or []) for role, keys in roles.items()}
            for provider, roles in (plan.get("assignments") or {}).items()
            if isinstance(roles, dict)
        },
        "publication_contract_relaxed": False,
    }


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_PREPARE
    if _INSTALLED:
        return {"status": "already_installed"}

    from app.services import daily_coverage_plan
    from app.services.allsportsapi_full_cohort_patch import (
        install as install_allsportsapi_patch,
    )
    from app.services.runtime_preflight import RuntimePreflight
    from app.services.strict_coverage_inventory_sync import install as install_inventory_sync

    activated = _set_full_cohort_runtime()
    daily_coverage_plan.PHASE_TARGETS = (300, 300, 300)
    allsportsapi_result = install_allsportsapi_patch()
    initial_real_inventory = _rebuild_real_inventory()
    inventory_result = install_inventory_sync()

    current = RuntimePreflight.prepare_discovery_first_inventory
    if not getattr(current, "_harizon_strict_coverage_replan", False):
        _ORIGINAL_PREPARE = current

        def prepare_discovery_first_inventory(self: Any) -> dict[str, Any]:
            assert callable(_ORIGINAL_PREPARE)
            result = _ORIGINAL_PREPARE(self)
            try:
                replan = _replan()
            except Exception as exc:
                replan = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            if isinstance(result, dict):
                result = dict(result)
                result["strict_coverage_replan"] = replan
                return result
            return {
                "status": "ok",
                "discovery_result": result,
                "strict_coverage_replan": replan,
            }

        prepare_discovery_first_inventory._harizon_strict_coverage_replan = True
        RuntimePreflight.prepare_discovery_first_inventory = (
            prepare_discovery_first_inventory
        )

    _INSTALLED = True
    return {
        "status": "installed",
        "phase_targets": [300, 300, 300],
        "discovery_wall_seconds": 240,
        "sstats_pari_wall_seconds": 180,
        "sstats_pari_rate_limit_per_minute": 140,
        "keyed_independent_providers": activated,
        "allsportsapi_full_cohort": allsportsapi_result,
        "initial_real_fixture_inventory": initial_real_inventory,
        "inventory_sync": inventory_result,
        "replan_after_discovery": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
