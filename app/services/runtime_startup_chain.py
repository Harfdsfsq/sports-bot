from __future__

"""Central runtime patch chain for the main HARIZON run.

Keep this out of report/fallback Python processes. Those processes read artifacts
created by the production run and must not reinstall model/provider wrappers,
because installer reports overwrite build-time diagnostics.
"""

from typing import Any

MODULES = [
    'app.services.provider_smoke_repair_env_guard',
    'app.services.runtime_provider_budget_guard',
    'app.services.core_coverage_quota_runtime_override',
    'app.services.free_context_runtime_enrichment',
    'app.services.api_matching_quality_runtime_guard',
    'app.services.bzzoiro_provider_runtime_fix',
    'app.services.market_family_publication_guard',
    'app.services.runtime_odds_inventory_matching_patch',
    'app.services.provider_payload_mining_runtime_patch_v2',
    'app.services.bzzoiro_direct_fetch_final_guard',
    'app.services.signal_stack_runtime_patch',
    'app.services.odds_movement_cache_bridge_patch',
    'app.services.sportlogic_query_runtime_guard',
    'app.services.api_runtime_enhancements',
    'app.services.secondary_odds_rescue_runtime_patch',
    'app.services.day_inventory_extra_fixture_sources',
    'app.services.day_inventory_bucketed_top_v3_runtime_patch',
    'app.services.day_inventory_runtime_guard',
    'app.services.near_window_priority_runtime_patch',
    'app.services.context_family_matching_runtime_patch',
    'app.services.runner_step_trace',
    'app.services.lifecycle_sent_index_runtime_guard',
    'app.services.fixture_expansion_runtime_guard',
    'app.services.api_health_runtime_guard',
    'app.services.post_run_analytics_runtime_guard',
    'app.services.rapidapi_probe_runtime_guard',
    'app.providers.rapidapi_odds_feed_patch',
    'app.providers.rapidapi_bridge_runtime_patch',
    'app.providers.sharpapi_runtime_patch',
    'app.services.sharpapi_text_runtime_patch',
    'app.services.secondary_odds_rescue_runtime_patch',
    'app.services.bzzoiro_final_odds_bridge_patch',
    'app.services.runner_bzzoiro_bridge_runtime_patch',
    'app.services.provider_smoke_repair_env_guard',
    'app.services.primary_provider_tier_runtime_guard',
    'app.services.core_odds_merge_safety_patch',
    'app.services.core_coverage_quota_runtime_override',
    'app.services.odds_movement_cache_bridge_patch',
    'app.services.prequality_final_consensus_bridge',
    'app.services.candidate_value_runtime_patch',
    'app.services.bookmaker_quorum_publication_policy',
    'app.services.windowed_core_coverage_finalizer',
    'app.services.windowed_core_report_and_sportlogic_final_guard',
    'app.services.progressive_coverage_runtime_patch',
    'app.services.progressive_core_sources_finalizer',
    'app.services.progressive_fetch_provider_signature_finalizer',
    'app.services.progressive_upcoming_gap_finalizer',
    'app.services.source_matrix_amplifier_runtime_patch',
    'app.services.bzzoiro_odds_comparison_bridge_patch',
    'app.services.bzzoiro_context_gap_finalizer',
    'app.services.bzzoiro_context_gap_timeout_guard',
    'app.services.bzzoiro_context_gap_source_id_finalizer',
    'app.services.bzzoiro_context_gap_relaxed_match_finalizer',
    'app.services.bzzoiro_gap_plan_targets_runtime_patch',
    'app.services.progressive_provider_alias_finalizer',
    'app.services.candidate_value_final_reinstall',
    'app.services.windowed_coverage_state_bridge',
    'app.services.odds_movement_cache_bridge_patch',
    'app.services.api_coverage_consensus_runtime_patch',
    'app.services.odds_api_io_account_source_split_patch',
    'app.services.quality_consensus_safe_relief_patch',
    'app.services.quality_data_missing_runtime_patch',
    'app.services.source_matrix_amplifier_runtime_patch',
    'app.services.bzzoiro_odds_comparison_bridge_patch',
    # Re-assert the final provider behavior after all legacy Bzzoiro/cache wrappers.
    'app.services.strict_coverage_runtime_repair',
    # Keep this after odds-api provider/account patches so it snapshots the final
    # parsed Offer rows that CandidateFactory will really receive.
    'app.services.odds_api_io_offer_snapshot_runtime_patch',
    # CandidateFactory wrappers must stay last and in this order: first materialize
    # exact provider hints into real Offer rows, then diagnose the final buckets.
    'app.services.bzzoiro_exact_offer_bridge_patch',
    'app.services.candidate_factory_runtime_diagnostics',
]


def _install(module_path: str) -> dict[str, Any]:
    try:
        module_name, attr = module_path.rsplit('.', 1)
        module = __import__(module_name, fromlist=[attr])
        installer = getattr(module, attr).install
        result = installer()
        return {'module': module_path, 'status': 'ok', 'result': result}
    except Exception as exc:
        return {'module': module_path, 'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}


def install_all() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    installed: set[str] = set()
    for module_path in MODULES:
        if module_path in installed:
            results.append({'module': module_path, 'status': 'skipped_duplicate'})
            continue
        installed.add(module_path)
        results.append(_install(module_path))
    return results
