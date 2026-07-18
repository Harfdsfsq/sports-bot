from __future__ import annotations

"""Re-rank and replan all 300 fixtures after discovery and before providers run."""

from typing import Any

_INSTALLED = False
_ORIGINAL_PREPARE = None


def _replan() -> dict[str, Any]:
    from app.services import daily_coverage_plan
    from app.services.strict_coverage_inventory_sync import sync

    daily_coverage_plan.PHASE_TARGETS = (300, 300, 300)
    synced = sync()
    plan = daily_coverage_plan.prepare_daily_coverage()
    return {
        "inventory_sync": synced,
        "plan_status": plan.get("status"),
        "phase_cumulative_target": plan.get("phase_cumulative_target"),
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
    from app.services.runtime_preflight import RuntimePreflight
    from app.services.strict_coverage_inventory_sync import install as install_inventory_sync

    daily_coverage_plan.PHASE_TARGETS = (300, 300, 300)
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
            return {"status": "ok", "discovery_result": result, "strict_coverage_replan": replan}

        prepare_discovery_first_inventory._harizon_strict_coverage_replan = True
        RuntimePreflight.prepare_discovery_first_inventory = prepare_discovery_first_inventory

    _INSTALLED = True
    return {
        "status": "installed",
        "phase_targets": [300, 300, 300],
        "inventory_sync": inventory_result,
        "replan_after_discovery": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
