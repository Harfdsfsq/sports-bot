from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
REPORT_PATH = Path('.data/exports/latest-runtime-startup-chain.json')

MODULES = [
    'app.services.unified_provider_match_identity_runtime',
    'app.services.bzzoiro_artifact_persistence_runtime_patch',
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
    'app.services.strict_coverage_runtime_repair',
    'app.services.odds_api_io_offer_snapshot_runtime_patch',
    'app.services.bzzoiro_exact_offer_bridge_patch',
    'app.services.max_coverage_api_matching_patch',
    'app.services.context_coverage_bridge_runtime',
    'app.services.candidate_factory_runtime_diagnostics',
    'app.services.rules_compliant_pipeline',
    'app.services.production_contract_runtime_guard',
]

# Policy keys owned by the deployment (run-bot.yml / config env files).
#
# Many modules in MODULES apply their defaults with `os.environ[key] = value`
# instead of `setdefault`, so importing them silently discards the workflow
# configuration. Whatever the process was started with wins: the chain restores
# these keys once every module has been installed, and runtime patches only get
# to fill in the ones the deployment left unset.
WORKFLOW_OWNED_ENV_KEYS = (
    # Publication window and horizon
    'PUBLISH_WINDOW_HOURS',
    'CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS',
    'RUN_DAYS_AHEAD',
    'MIN_KICKOFF_LEAD_MINUTES',
    # Windowed core coverage guard
    'CORE_COVERAGE_WINDOW_HOURS',
    'CORE_COVERAGE_CRON_INTERVAL_HOURS',
    'CORE_COVERAGE_RECHECK_LEAD_MINUTES',
    'CORE_COVERAGE_MIN_ODDS_SOURCES',
    'CORE_COVERAGE_MIN_CONTEXT_SOURCES',
    'CORE_COVERAGE_MIN_CORE_PROVIDERS',
    'CORE_COVERAGE_MIN_MOVEMENT_SNAPSHOTS',
    'CORE_COVERAGE_CONTEXT_TARGET_LIMIT',
    # Tier contract
    'PUBLISH_ALLOW_B_TIER',
    'PUBLISH_TIER_A_MIN_BOOKS',
    'PUBLISH_TIER_A_MIN_ODDS_SOURCES',
    'PUBLISH_TIER_A_MIN_CONTEXT_SOURCES',
    'PUBLISH_TIER_B_MIN_BOOKS',
    'PUBLISH_TIER_B_MIN_ODDS_SOURCES',
    'PUBLISH_TIER_B_MIN_CONTEXT_SOURCES',
    'PUBLISH_MIN_ODDS_SOURCES',
    'PUBLISH_MIN_CONTEXT_SOURCES',
    'MIN_CONTEXT_SOURCES_PUBLISH',
    'MIN_SOURCES_PUBLISH',
    'MIN_BOOKS_PUBLISH',
    'PUBLICATION_ALLOWED_MARKET_FAMILIES',
    # Line movement lifecycle
    'PUBLISH_REQUIRE_LINE_MOVEMENT',
    'LINE_MOVEMENT_MIN_SNAPSHOTS',
    'LINE_MOVEMENT_MAX_STALE_MINUTES',
    'LINE_MOVEMENT_MIN_MINUTES_BETWEEN_SNAPSHOTS',
    # Inventory shape
    'DAY_INVENTORY_TARGET_SIZE',
    'DAY_INVENTORY_MAX_MATCHES',
    'DAY_INVENTORY_STARTED_GRACE_MINUTES',
    'DAY_INVENTORY_MIN_UPCOMING_MATCHES',
    'CONTEXT_ENRICHMENT_MATCH_LIMIT',
    'CONTEXT_ENRICHMENT_REQUIRES_OFFERS',
    'PREMIUM_CONTEXT_SHORTLIST_LIMIT',
    # Provider budgets
    'MAX_MATCHES_FOR_ODDS_FETCH',
    'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN',
    'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX',
    'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX',
    'ODDS_API_IO_MAX_ODDS_EVENTS_PER_RUN',
    'SSTATS_MAX_HTTP_REQUESTS_PER_RUN',
    'SSTATS_CONTEXT_MATCH_LIMIT',
    'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN',
    'BZZOIRO_CONTEXT_MATCH_LIMIT',
    'BZZOIRO_V2_MATCH_LIMIT',
    'BZZOIRO_RUNTIME_PROVIDER_DEADLINE_SECONDS',
    'WEATHER_CONTEXT_MATCH_LIMIT',
    'FINAL_ENRICHMENT_ONLY_FOR_VALUE_CANDIDATES',
    'FINAL_ENRICHMENT_FALLBACK_NEAREST_MATCH_LIMIT',
)


def _snapshot_owned_env() -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for key in WORKFLOW_OWNED_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None and str(value).strip() != '':
            snapshot[key] = str(value)
    return snapshot


def _restore_owned_env(snapshot: dict[str, str]) -> dict[str, dict[str, str]]:
    restored: dict[str, dict[str, str]] = {}
    for key, original in snapshot.items():
        current = os.environ.get(key)
        if current is not None and str(current) == original:
            continue
        os.environ[key] = original
        restored[key] = {
            'overridden_by_runtime_patch_to': '' if current is None else str(current),
            'restored_to': original,
        }
    return restored


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    except Exception:
        pass


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
    started_at = datetime.now(UTC)
    owned_env = _snapshot_owned_env()
    results: list[dict[str, Any]] = []
    installed: set[str] = set()
    for module_path in MODULES:
        if module_path in installed:
            results.append({'module': module_path, 'status': 'skipped_duplicate'})
            continue
        installed.add(module_path)
        results.append(_install(module_path))
    restored_env = _restore_owned_env(owned_env)
    errors = [row for row in results if row.get('status') == 'error']
    _write_report({
        'started_at_utc': started_at.isoformat(),
        'finished_at_utc': datetime.now(UTC).isoformat(),
        'modules_total': len(MODULES),
        'modules_installed': sum(1 for row in results if row.get('status') == 'ok'),
        'modules_failed': len(errors),
        'modules_skipped_duplicate': sum(1 for row in results if row.get('status') == 'skipped_duplicate'),
        'failed_modules': [{'module': row.get('module'), 'error': row.get('error')} for row in errors],
        'workflow_owned_env_snapshot': owned_env,
        'workflow_owned_env_restored': restored_env,
        'workflow_owned_env_restored_count': len(restored_env),
        'effective_policy_env': {key: os.environ.get(key) for key in owned_env},
    })
    return results
