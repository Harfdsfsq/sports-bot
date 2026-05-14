from sitecustomize import *

import os
import sys
from pathlib import Path


def _is_fallback_publisher_process() -> bool:
    name = Path(str(sys.argv[0] or "")).name
    return name in {
        "publish_controlled_fallback.py",
        "publish_controlled_fallback_guarded.py",
    } or os.getenv("HARIZON_CONTROLLED_FALLBACK_REDIRECTED") == "1"


def _is_stdin_env_helper_process() -> bool:
    """Do not install noisy runtime wrappers for `python -` helper snippets.

    GitHub Actions writes env-vars by redirecting stdout into $GITHUB_ENV in a few
    workflow steps. Full runtime installers can print JSON diagnostics during
    startup; if that output is redirected to $GITHUB_ENV, GitHub fails the step
    with `Invalid format '{'`. The real app/scripts still receive all runtime
    patches because their sys.argv[0] is an actual script/module path.
    """
    return str(sys.argv[0] or "").strip() == "-" or os.getenv("HARIZON_SKIP_USERCUSTOMIZE_INSTALLERS") == "1"


_SKIP_RUNTIME_INSTALLERS = _is_fallback_publisher_process() or _is_stdin_env_helper_process()


def _install(module_path: str) -> None:
    try:
        module_name, attr = module_path.rsplit('.', 1)
        module = __import__(module_name, fromlist=[attr])
        getattr(module, attr).install()
    except Exception:
        pass


if not _SKIP_RUNTIME_INSTALLERS:
    for _module in [
        'app.services.provider_smoke_repair_env_guard',
        'app.services.runtime_provider_budget_guard',
        'app.services.core_coverage_quota_runtime_override',
        'app.services.sportlogic_daily_limit_guard',
        'app.services.core_provider_inventory_bridge',
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
        'app.services.sportlogic_daily_limit_guard',
        'app.services.core_provider_inventory_bridge',
        'app.services.odds_movement_cache_bridge_patch',
        'app.services.prequality_final_consensus_bridge',
        'app.services.candidate_value_runtime_patch',
        'app.services.windowed_core_coverage_finalizer',
        'app.services.windowed_core_report_and_sportlogic_final_guard',
        'app.services.progressive_coverage_runtime_patch',
        'app.services.progressive_core_sources_finalizer',
        'app.services.progressive_fetch_provider_signature_finalizer',
        'app.services.progressive_upcoming_gap_finalizer',
        'app.services.bzzoiro_context_gap_finalizer',
        'app.services.bzzoiro_context_gap_timeout_guard',
        'app.services.bzzoiro_context_gap_source_id_finalizer',
        'app.services.bzzoiro_context_gap_relaxed_match_finalizer',
        'app.services.progressive_provider_alias_finalizer',
        'app.services.candidate_value_final_reinstall',
        'app.services.windowed_coverage_state_bridge',
        'app.services.odds_movement_cache_bridge_patch',
        'app.services.api_coverage_consensus_runtime_patch',
        'app.services.odds_api_io_account_source_split_patch',
        'app.services.quality_consensus_safe_relief_patch',
    ]:
        _install(_module)
