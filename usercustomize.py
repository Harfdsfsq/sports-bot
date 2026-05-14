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


# Controlled fallback runs after the main bot in a separate Python process. It must not
# reinstall model/runtime wrappers because those installers overwrite the main run's
# diagnostics (`latest-candidate-value-runtime-patch.json`, api coverage reports, etc.).
# sitecustomize still redirects the legacy fallback entrypoint and keeps Telegram safety.
if _is_fallback_publisher_process():
    raise SystemExit if False else None

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
    # Raise only the three core provider budgets before settings/providers are built:
    # odds_api_io 100+100 per run, sstats 150 per run, bzzoiro 150 per run.
    # Publication guards remain strict and still require verified price/context coverage.
    'app.services.core_coverage_quota_runtime_override',
    'app.services.free_context_runtime_enrichment',
    'app.services.api_matching_quality_runtime_guard',
    'app.services.bzzoiro_provider_runtime_fix',
    'app.services.market_family_publication_guard',
    'app.services.runtime_odds_inventory_matching_patch',
    'app.services.provider_payload_mining_runtime_patch_v2',
    'app.services.bzzoiro_direct_fetch_final_guard',
    'app.services.signal_stack_runtime_patch',
    # Persist odds movement snapshots into .data/cache and make windowed movement
    # read the durable cache as well as the current-run jsonl. This fixes
    # snapshot_count=0 caused by workflow resetting .data/odds_movement_snapshots.jsonl.
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
    # Reinstall rescue after later provider wrappers.
    'app.services.secondary_odds_rescue_runtime_patch',
    'app.services.bzzoiro_final_odds_bridge_patch',
    'app.services.runner_bzzoiro_bridge_runtime_patch',
    'app.services.provider_smoke_repair_env_guard',
    'app.services.primary_provider_tier_runtime_guard',
    # Keep SStats/Bzzoiro odds rescue sane before app.cli installs the merge wrapper:
    # limits requests, filters low-line/low-price injected offers, and fixes Bzzoiro
    # score_event_match compatibility.
    'app.services.core_odds_merge_safety_patch',
    # Re-apply core quotas after provider-tier/runtime-budget wrappers because some
    # older layers still write conservative Bzzoiro/SStats limits.
    'app.services.core_coverage_quota_runtime_override',
    # Re-apply snapshot cache bridge after any later signal/windowed wrappers. It is
    # idempotent, so this guarantees final movement checks read durable history.
    'app.services.odds_movement_cache_bridge_patch',
    # Keep near-zero candidates alive until final consensus validation. This must
    # run before candidate_value_runtime_patch so the pre-quality filter uses the
    # widened holding-pen thresholds, while final consensus still requires EV>=0.
    'app.services.prequality_final_consensus_bridge',
    # Value-first candidate ordering must be active before later candidate wrappers,
    # otherwise negative-EV raw model candidates dominate the pre-quality slice.
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
    # Absolute final wrapper: later modules can replace CandidateFactory again,
    # so reinstall value-first build_candidates as the outermost wrapper first.
    'app.services.candidate_value_final_reinstall',
    # Then bridge progressive coverage into the final CandidateFactory wrapper.
    # This must be AFTER candidate_value_final_reinstall; otherwise it is overwritten.
    'app.services.windowed_coverage_state_bridge',
    # Final re-application after windowed bridge, because movement checks are used in
    # candidate/publication filters installed by the windowed layers.
    'app.services.odds_movement_cache_bridge_patch',
    # True final publication-candidate gate: rebase odds to exact-line consensus and
    # reject candidates without verified price/context coverage.
    'app.services.api_coverage_consensus_runtime_patch',
    # Must be after api_coverage_consensus_runtime_patch: it changes exact price-source
    # accounting inside that module so odds-api.io account1/account2 are independent
    # sources when the exact line is confirmed by their separate bookmaker groups.
    'app.services.odds_api_io_account_source_split_patch',
    # Final quality safety relief: only after consensus/source validation wrappers are
    # installed. It can rescue a candidate rejected by calibration only if consensus
    # EV/edge remain >= 0 and 2+ odds/context sources are present.
    'app.services.quality_consensus_safe_relief_patch',
]:
    _install(_module)
