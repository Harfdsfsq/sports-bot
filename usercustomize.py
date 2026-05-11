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
# candidate generation and must remain the last runtime install.
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
