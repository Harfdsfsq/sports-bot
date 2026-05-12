from sitecustomize import *

# Runtime patch installer. Keep order: broad guards first, concrete runner/provider
# wrappers next, progressive/final accounting fixes last.
def _install(module_path: str) -> None:
    try:
        module_name, attr = module_path.rsplit('.', 1)
        module = __import__(module_name, fromlist=[attr])
        getattr(module, attr).install()
    except Exception:
        pass

for _module in [
    'app.services.provider_smoke_repair_env_guard',
    'app.services.runtime_provider_budget_guard',
    'app.services.free_context_runtime_enrichment',
    'app.services.api_matching_quality_runtime_guard',
    'app.services.bzzoiro_provider_runtime_fix',
    'app.services.market_family_publication_guard',
    'app.services.runtime_odds_inventory_matching_patch',
    'app.services.provider_payload_mining_runtime_patch_v2',
    'app.services.bzzoiro_direct_fetch_final_guard',
    'app.services.signal_stack_runtime_patch',
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
    # Reinstall rescue after later provider wrappers.
    'app.services.secondary_odds_rescue_runtime_patch',
    'app.services.bzzoiro_final_odds_bridge_patch',
    'app.services.runner_bzzoiro_bridge_runtime_patch',
    'app.services.provider_smoke_repair_env_guard',
    'app.services.primary_provider_tier_runtime_guard',
    # Value-first candidate ordering must be active before windowed audit wraps
    # CandidateFactory, otherwise negative-EV raw model candidates dominate the
    # pre-quality slice.
    'app.services.candidate_value_runtime_patch',
    'app.services.windowed_core_coverage_finalizer',
    'app.services.windowed_core_report_and_sportlogic_final_guard',
    'app.services.progressive_coverage_runtime_patch',
    'app.services.progressive_core_sources_finalizer',
    'app.services.progressive_fetch_provider_signature_finalizer',
    'app.services.progressive_upcoming_gap_finalizer',
    'app.services.bzzoiro_context_gap_finalizer',
    'app.services.bzzoiro_context_gap_timeout_guard',
    # Bridge inventory source_ids into Bzzoiro context-gap matching before final
    # progressive accounting is recomputed.
    'app.services.bzzoiro_context_gap_source_id_finalizer',
    # Controlled relaxed matching only inside Bzzoiro context gap-pass.
    'app.services.bzzoiro_context_gap_relaxed_match_finalizer',
    # Absolute last accounting repair: bzzoiro_predictions_v2 => bzzoiro.
    'app.services.progressive_provider_alias_finalizer',
    # Final bridge: let windowed publication audit reuse progressive coverage
    # evidence computed in the same run and relieve impossible next-cron movement
    # checks for final-pre-kickoff matches.
    'app.services.windowed_coverage_state_bridge',
    # Absolute final wrapper: later modules can replace CandidateFactory again,
    # so reinstall value-first build_candidates as the outermost wrapper.
    'app.services.candidate_value_final_reinstall',
]:
    _install(_module)
