from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".data" / "exports" / "latest-daily-coverage-runtime-patch.json"
_INSTALLED = False


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
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    try:
        from app.services.daily_coverage_bootstrap_restore_patch import (
            install as install_bootstrap_restore,
        )

        bootstrap_restore_result = install_bootstrap_restore()
        from app.services.daily_coverage_source_integrity_patch import (
            install as install_source_integrity,
        )

        source_integrity_result = install_source_integrity()
        from app.services.daily_coverage_fixed_cohort_patch import (
            install as install_fixed_cohort,
        )

        fixed_cohort_result = install_fixed_cohort()
        from app.services.daily_coverage_state_persistence_patch import (
            install as install_state_persistence,
        )

        state_persistence_result = install_state_persistence()
        from app.services.strict_coverage_preparation_hook import (
            install as install_strict_preparation,
        )

        strict_preparation_result = install_strict_preparation()
        from app.services.daily_coverage_plan import prepare_daily_coverage

        replanned = prepare_daily_coverage()
        from app.services.clubelo_strict_match_patch import (
            install as install_clubelo_strict,
        )
        from app.services.sstats_context_runtime_cache_patch import (
            install as install_sstats_context_cache,
        )
        from app.services.sstats_pari_current_odds_patch import (
            install as install_sstats_pari_current_odds,
        )
        from app.services.sstats_pari_runtime_repair import (
            install as install_sstats_pari_repair,
        )
        from app.services.sstats_team_form_alias_patch import (
            install as install_sstats_alias,
        )

        clubelo_result = install_clubelo_strict()
        sstats_pari_result = install_sstats_pari_repair()
        sstats_pari_current_odds_result = install_sstats_pari_current_odds()
        sstats_alias_result = install_sstats_alias()
        sstats_context_cache_result = install_sstats_context_cache()
        from app.services import evidence
        from app.services import runner as runner_module
        from app.services.coverage_planner import CoveragePlanner
        from app.services.daily_coverage_freshness_patch import (
            install as install_freshness,
        )
        from app.services.daily_coverage_runtime_boundary import (
            install as install_boundary,
        )
        from app.services.daily_coverage_runtime_providers import (
            install as install_providers,
        )
        from app.services.runner import PredictionRunner
        from app.services.strict_coverage_metrics import (
            install as install_strict_metrics,
        )

        strict_result = install_strict_metrics()
        freshness_result = install_freshness()
        provider_result = install_providers(PredictionRunner, CoveragePlanner)
        from app.services.daily_coverage_core_target_patch import (
            install as install_core_targets,
        )

        core_target_result = install_core_targets(PredictionRunner)
        boundary_result = install_boundary(PredictionRunner, runner_module, evidence)
        from app.services.daily_coverage_evidence_stamp_patch import (
            install as install_evidence_stamp,
        )

        stamp_result = install_evidence_stamp(PredictionRunner)
        from app.services.daily_coverage_full_inventory_provider_patch import (
            install as install_full_inventory_providers,
        )

        full_inventory_provider_result = install_full_inventory_providers(PredictionRunner)
        from app.services.focused_alpha_filter_contract_patch import (
            install as install_focused_alpha_filter_contract,
        )

        focused_alpha_filter_contract_result = install_focused_alpha_filter_contract(
            PredictionRunner
        )
    except Exception as exc:
        payload = {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
        _write(payload)
        return payload
    _INSTALLED = True
    payload = {
        "status": "installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "bootstrap_restore": bootstrap_restore_result,
        "source_integrity": source_integrity_result,
        "fixed_cohort": fixed_cohort_result,
        "state_persistence": state_persistence_result,
        "strict_coverage_preparation": strict_preparation_result,
        "replanned_after_source_repair": {
            "status": replanned.get("status"),
            "run_index": replanned.get("run_index"),
            "phase_cumulative_target": replanned.get("phase_cumulative_target"),
            "coverage_before": replanned.get("coverage_before"),
            "provider_assignments": {
                provider: {role: len(keys or []) for role, keys in roles.items()}
                for provider, roles in (replanned.get("assignments") or {}).items()
                if isinstance(roles, dict)
            },
        },
        "sstats_pari_repair": sstats_pari_result,
        "sstats_pari_current_odds": sstats_pari_current_odds_result,
        "sstats_team_form_alias": sstats_alias_result,
        "sstats_context_cache": sstats_context_cache_result,
        "clubelo_strict_match": clubelo_result,
        "strict_metrics": strict_result,
        "freshness": freshness_result,
        "providers": provider_result,
        "core_targets": core_target_result,
        "boundary": boundary_result,
        "evidence_stamp": stamp_result,
        "full_inventory_provider_scope": full_inventory_provider_result,
        "focused_alpha_filter_contract": focused_alpha_filter_contract_result,
        "publication_contract_relaxed": False,
    }
    _write(payload)
    return payload


__all__ = ["install"]
