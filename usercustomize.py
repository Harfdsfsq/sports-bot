from sitecustomize import *

# Provider-smoke is a diagnostic repair workflow. Install this first and last so
# normal runtime quota guards cannot silently disable Bzzoiro/SportLogic/SStats
# or provider-merge while the smoke job is trying to test API coverage.
try:
    from app.services import provider_smoke_repair_env_guard
    provider_smoke_repair_env_guard.install()
except Exception:
    pass

try:
    from app.services import runtime_provider_budget_guard
    runtime_provider_budget_guard.install()
except Exception:
    pass

try:
    from app.services import free_context_runtime_enrichment
    free_context_runtime_enrichment.install()
except Exception:
    pass

try:
    from app.services import api_matching_quality_runtime_guard
    api_matching_quality_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import bzzoiro_provider_runtime_fix
    bzzoiro_provider_runtime_fix.install()
except Exception:
    pass

try:
    from app.services import market_family_publication_guard
    market_family_publication_guard.install()
except Exception:
    pass

try:
    from app.services import runtime_odds_inventory_matching_patch
    runtime_odds_inventory_matching_patch.install()
except Exception:
    pass

try:
    from app.services import provider_payload_mining_runtime_patch_v2
    provider_payload_mining_runtime_patch_v2.install()
except Exception:
    pass

# Must run after provider_payload_mining_runtime_patch_v2 because that layer can
# wrap the old direct Bzzoiro fetch. This final guard disables the duplicate
# direct fetch while keeping the SStats-integrated Bzzoiro signal path alive.
try:
    from app.services import bzzoiro_direct_fetch_final_guard
    bzzoiro_direct_fetch_final_guard.install()
except Exception:
    pass

try:
    from app.services import signal_stack_runtime_patch
    signal_stack_runtime_patch.install()
except Exception:
    pass

try:
    from app.services import sportlogic_query_runtime_guard
    sportlogic_query_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import api_runtime_enhancements
    api_runtime_enhancements.install()
except Exception:
    pass

# Early install is kept for compatibility, but the definitive wrap is repeated
# at the very end because later runtime patches can replace _fetch_provider.
try:
    from app.services import secondary_odds_rescue_runtime_patch
    secondary_odds_rescue_runtime_patch.install()
except Exception:
    pass

try:
    from app.services import day_inventory_extra_fixture_sources
    day_inventory_extra_fixture_sources.install()
except Exception:
    pass

# Day inventory top selection must be handled by the hard-capped selector only.
# Older selectors are intentionally not installed here because they can select
# 300 matches before SStats caps are enforced.
try:
    from app.services import day_inventory_bucketed_top_v3_runtime_patch
    day_inventory_bucketed_top_v3_runtime_patch.install()
except Exception:
    pass

try:
    from app.services import day_inventory_runtime_guard
    day_inventory_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import near_window_priority_runtime_patch
    near_window_priority_runtime_patch.install()
except Exception:
    pass

try:
    from app.services import context_family_matching_runtime_patch
    context_family_matching_runtime_patch.install()
except Exception:
    pass

try:
    from app.services import runner_step_trace
    runner_step_trace.install()
except Exception:
    pass

try:
    from app.services import lifecycle_sent_index_runtime_guard
    lifecycle_sent_index_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import fixture_expansion_runtime_guard
    fixture_expansion_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import api_health_runtime_guard
    api_health_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import post_run_analytics_runtime_guard
    post_run_analytics_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import rapidapi_probe_runtime_guard
    rapidapi_probe_runtime_guard.install()
except Exception:
    pass

try:
    from app.providers import rapidapi_odds_feed_patch
    rapidapi_odds_feed_patch.install()
except Exception:
    pass

try:
    from app.providers import rapidapi_bridge_runtime_patch
    rapidapi_bridge_runtime_patch.install()
except Exception:
    pass

try:
    from app.providers import sharpapi_runtime_patch
    sharpapi_runtime_patch.install()
except Exception:
    pass

try:
    from app.services import sharpapi_text_runtime_patch
    sharpapi_text_runtime_patch.install()
except Exception:
    pass

# Definitive install: this must be the last _fetch_provider wrapper so secondary
# odds rescue is not silently overwritten by other runtime modules.
try:
    from app.services import secondary_odds_rescue_runtime_patch
    secondary_odds_rescue_runtime_patch.install()
except Exception:
    pass

# Class-level CandidateFactory wrapper. Kept for compatibility with paths that
# instantiate CandidateFactory outside PredictionRunner.
try:
    from app.services import bzzoiro_final_odds_bridge_patch
    bzzoiro_final_odds_bridge_patch.install()
except Exception:
    pass

# Absolute final layer: wraps the concrete PredictionRunner.factory instance
# used by run_once. This is the effective Bzzoiro odds bridge for rescue/main
# candidate generation and must remain after generic bridge layers.
try:
    from app.services import runner_bzzoiro_bridge_runtime_patch
    runner_bzzoiro_bridge_runtime_patch.install()
except Exception:
    pass

# Final repair-env assertion for provider-smoke. The same module also registers
# an atexit writer, so values appended by policy scripts are overridden again
# before each Python process exits.
try:
    from app.services import provider_smoke_repair_env_guard
    provider_smoke_repair_env_guard.install()
except Exception:
    pass

# Absolute final provider-tier contract. The three primary providers must carry
# inventory, odds and context: odds-api.io + Bzzoiro + SStats. Supplemental APIs
# are shortlist/backfill-only and must not consume broad-run quota.
try:
    from app.services import primary_provider_tier_runtime_guard
    primary_provider_tier_runtime_guard.install()
except Exception:
    pass

# The true final layer must be installed after every provider and runner wrapper.
# This finalizer resets wrapper markers and force-reinstalls windowed coverage.
try:
    from app.services import windowed_core_coverage_finalizer
    windowed_core_coverage_finalizer.install()
except Exception:
    pass

# Absolute final diagnostics/policy guard. It preserves the rich windowed-core
# candidate audit and enables SportLogic only as a controlled near-window
# secondary odds source when a key is present.
try:
    from app.services import windowed_core_report_and_sportlogic_final_guard
    windowed_core_report_and_sportlogic_final_guard.install()
except Exception:
    pass

# Progressive coverage planner must wrap _fetch_provider after all provider
# bridges/rescue layers. It accumulates per-match line/context coverage across
# 2-hour runs and sorts targets by remaining gaps.
try:
    from app.services import progressive_coverage_runtime_patch
    progressive_coverage_runtime_patch.install()
except Exception:
    pass

# Final contract for progressive coverage: core line/odds sources are
# odds_api_io + bzzoiro + sstats; core context sources are sstats + bzzoiro.
try:
    from app.services import progressive_core_sources_finalizer
    progressive_core_sources_finalizer.install()
except Exception:
    pass

# Last compatibility layer: progressive coverage must not break bootstrap
# _fetch_provider calls that do not pass an explicit match list.
try:
    from app.services import progressive_fetch_provider_signature_finalizer
    progressive_fetch_provider_signature_finalizer.install()
except Exception:
    pass

# Operational finalizer: gap plan should prioritize upcoming matches only and
# retry Bzzoiro/SStats sooner when core context gaps remain.
try:
    from app.services import progressive_upcoming_gap_finalizer
    progressive_upcoming_gap_finalizer.install()
except Exception:
    pass

# Targeted fix for the current v8 bottleneck: many matches have SStats context
# but miss Bzzoiro. This pass enriches only upcoming progressive context gaps.
try:
    from app.services import bzzoiro_context_gap_finalizer
    bzzoiro_context_gap_finalizer.install()
except Exception:
    pass
