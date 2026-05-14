from __future__ import annotations

"""Keep provider-smoke repair workflow env intact without overriding step env.

Several runtime/guard scripts write values into GITHUB_ENV.  The provider-smoke
workflow has two modes:

* provider-smoke-repair: broad repair/debug mode;
* provider-smoke-minimal-repair: low-quota API repair mode.

The minimal mode must never be expanded back to production-sized quotas by this
module's atexit GITHUB_ENV writer.  This guard is imported by usercustomize.py in
nearly every Python process, so the values below are authoritative for later
workflow steps.
"""

import atexit
import os
from pathlib import Path


BROAD_REPAIR_ENV = {
    "APP_ENV": "provider-smoke-repair",
    "HARIZON_PROVIDER_PROBE_MODE": "true",
    "HARIZON_FAST_INVENTORY_LOCK": "false",
    "DAY_INVENTORY_FAST_MODE": "false",
    "DAY_INVENTORY_FORCE_PROVIDER_MERGE": "true",
    "DAY_INVENTORY_FORCE_FULL_300": "true",
    "DAY_INVENTORY_FORCE_FULL_ALLOW_SSTATS_OVER_HARD_CAP": "true",
    "DAY_INVENTORY_EXTRA_FIXTURES_ENABLED": "true",
    "DAY_INVENTORY_ENABLE_BZZOIRO": "true",
    "DAY_INVENTORY_ENABLE_ALLSPORTSAPI": "true",
    "DAY_INVENTORY_ENABLE_SPORTLOGIC": "true",
    "DAY_INVENTORY_ENABLE_SSTATS": "true",
    "DAY_INVENTORY_TOP_MATCHES_ENABLED": "true",
    "DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED": "true",
    "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
    "BZZOIRO_PROVIDER_SMOKE_ENABLED": "true",
    "BZZOIRO_ENABLED": "true",
    "ENABLE_BZZOIRO": "true",
    "ENABLE_BZZOIRO_CONTEXT": "true",
    "SSTATS_ENABLED": "true",
    "ENABLE_SSTATS": "true",
    "ENABLE_SSTATS_CONTEXT": "true",
    "SPORTLOGIC_ENABLED": "true",
    "ENABLE_SPORTLOGIC": "true",
    "ALLSPORTSAPI_ENABLED": "true",
    "ENABLE_ALLSPORTSAPI": "true",
    "PROVIDER_SMOKE_MATCHING_DIAGNOSTICS_ENABLED": "true",
    "PROVIDER_SMOKE_MATCHING_PROVIDERS": "sstats,bzzoiro,football_data,allsportsapi",
    "API_FULL_SMOKE_ENABLED": "false",
    "API_FULL_SMOKE_BZZOIRO_ENABLED": "true",
    "API_FULL_SMOKE_FOOTBALL_DATA_ENABLED": "true",
    "API_FULL_SMOKE_ODDS_API_IO_ENABLED": "true",
    "API_FULL_SMOKE_SPORTLOGIC_ENABLED": "false",
    "API_FULL_SMOKE_SPORTLOGIC_DETAILS_ENABLED": "false",
    "PUBLICATION_ALLOWED_MARKET_FAMILIES": "totals,spreads",
    "HARIZON_ALLOWED_PUBLICATION_FAMILIES": "totals,spreads",
    "H2H_PUBLICATION_ENABLED": "false",
    "BTTS_PUBLICATION_ENABLED": "false",
    "DNB_PUBLICATION_ENABLED": "false",
    "DOUBLE_CHANCE_PUBLICATION_ENABLED": "false",
    "TEAM_TOTALS_PUBLICATION_ENABLED": "false",
    "TOTALS_PUBLICATION_ENABLED": "true",
    "SPREADS_PUBLICATION_ENABLED": "true",
}

MINIMAL_REPAIR_ENV = {
    **BROAD_REPAIR_ENV,
    "APP_ENV": "provider-smoke-minimal-repair",
    "HARIZON_PROVIDER_PROBE_MODE": "true",
    "DAY_INVENTORY_FORCE_PROVIDER_MERGE": "true",
    "DAY_INVENTORY_TARGET_SIZE": "300",
    "DAY_INVENTORY_MAX_MATCHES": "300",
    "DAY_INVENTORY_FORCE_TOP_300": "true",
    "DAY_INVENTORY_FORCE_FULL_300": "true",
    "DAY_INVENTORY_FORCE_FULL_ALLOW_SSTATS_OVER_HARD_CAP": "false",
    "DAY_INVENTORY_ENABLE_BZZOIRO": "true",
    "DAY_INVENTORY_ENABLE_ALLSPORTSAPI": "false",
    "DAY_INVENTORY_ENABLE_SPORTLOGIC": "false",
    "DAY_INVENTORY_ENABLE_SSTATS": "true",
    "DAY_INVENTORY_BZZOIRO_MAX_REQUESTS": "3",
    "DAY_INVENTORY_BZZOIRO_MAX_PAGES": "2",
    "DAY_INVENTORY_SSTATS_MAX_REQUESTS": "1",
    "DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT": "0",
    "DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS": "0",
    "PROVIDER_SMOKE_FAST_PROVIDERS": "odds_api_io,bzzoiro,sstats,sportlogic",
    "PROVIDER_SMOKE_MATCHING_PROVIDERS": "sstats,bzzoiro,sportlogic",
    "PROVIDER_SMOKE_MATCHING_ODDS_LIMIT": "60",
    "PROVIDER_SMOKE_MATCHING_ODDS_PAGES": "1",
    "PROVIDER_SMOKE_REPEATS": "1",

    # odds-api.io: event list + small odds/multi checks only.
    "ODDS_API_IO_PER_RUN_MAX": "6",
    "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": "4",
    "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": "2",
    "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": "6",
    "ODDS_API_IO_MAX_REQUESTS_PER_RUN": "6",
    "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "1",
    "ODDS_API_IO_MAX_PAGES_PER_SPORT": "1",
    "ODDS_API_IO_REQUEST_BUDGET_GRANTED": "6",
    "ODDS_API_IO_REQUESTS_MAX_PER_RUN": "6",
    "MAX_MATCHES_FOR_ODDS_FETCH": "20",

    # Bzzoiro: one events pass plus small optional prediction/odds checks.
    "BZZOIRO_PROVIDER_SMOKE_ENABLED": "true",
    "BZZOIRO_ENABLED": "true",
    "ENABLE_BZZOIRO": "true",
    "ENABLE_BZZOIRO_CONTEXT": "true",
    "BZZOIRO_CONTEXT_ENABLED": "true",
    "BZZOIRO_PER_RUN_MAX": "3",
    "BZZOIRO_MAX_REQUESTS_PER_RUN": "3",
    "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": "3",
    "BZZOIRO_REQUESTS_MAX_PER_RUN": "3",
    "BZZOIRO_REQUEST_BUDGET_GRANTED": "3",
    "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN": "2",
    "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN": "1",
    "BZZOIRO_PREDICTIONS_MAX_PAGES": "1",
    "BZZOIRO_MAX_PAGES": "2",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "40",

    # SStats: keep the cheap list endpoint; disable deep/detail enrichment in smoke.
    "SSTATS_ENABLED": "true",
    "ENABLE_SSTATS": "true",
    "ENABLE_SSTATS_CONTEXT": "true",
    "SSTATS_CONTEXT_ENABLED": "true",
    "SSTATS_PER_RUN_MAX": "2",
    "SSTATS_MAX_REQUESTS_PER_RUN": "2",
    "SSTATS_REQUESTS_MAX_PER_RUN": "2",
    "SSTATS_REQUEST_BUDGET_GRANTED": "2",
    "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": "2",
    "SSTATS_CONTEXT_MATCH_LIMIT": "60",
    "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "0",
    "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": "0",
    "SSTATS_DEEP_ENRICHMENT_ENABLED": "false",
    "SSTATS_DEEP_ENRICHMENT_AFTER_CROSSWALK": "false",
    "SSTATS_DEEP_SMOKE_DETAIL_GAMES": "0",
    "SSTATS_GAME_DETAIL_ENABLED": "false",
    "SSTATS_LAST_GAMES_STATS_ENABLED": "false",
    "SSTATS_INJURIES_ENABLED": "false",
    "SSTATS_GLICKO_ENABLED": "false",
    "SSTATS_ODDS_RESCUE_ENABLED": "false",

    # SportLogic is diagnosed by the minimal probe only.  Broad runtime use stays off.
    "SPORTLOGIC_ENABLED": "false",
    "ENABLE_SPORTLOGIC": "false",
    "SPORTLOGIC_PER_RUN_MAX": "4",
    "SPORTLOGIC_MATCH_LIMIT": "20",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "1",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES": "0",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT": "0",

    "API_FULL_SMOKE_ENABLED": "false",
    "API_FULL_SMOKE_SPORTLOGIC_ENABLED": "false",
    "API_FULL_SMOKE_SPORTLOGIC_DETAILS_ENABLED": "false",
    "API_FULL_SMOKE_ODDS_EXTRA_MAX_REQUESTS": "1",
    "API_FULL_SMOKE_ODDS_EVENT_LIMIT": "1",
    "PUBLISH_MIN_ODDS_SOURCES": "2",
    "PUBLISH_MIN_CONTEXT_SOURCES": "2",
    "MIN_CONTEXT_SOURCES_PUBLISH": "2",
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "2",
}

PRESERVE_IF_SET = {
    "DAY_INVENTORY_TARGET_DATE",
    "PROVIDER_SMOKE_TARGET_DATE",
    "PROVIDER_SMOKE_COVERAGE_TARGET",
    "PROVIDER_SMOKE_FAST_MAX_SECONDS",
    "PROVIDER_SMOKE_FAST_TIMEOUT",
    "PROVIDER_SMOKE_FAST_CONCURRENCY",
    "PROVIDER_API_MIN_PROBE_TIMEOUT",
    "PROVIDER_SMOKE_SHOW_OK_SAMPLES",
}


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _is_minimal_mode() -> bool:
    app_env = str(os.getenv("APP_ENV") or "").strip().lower()
    return app_env == "provider-smoke-minimal-repair" or _is_truthy(os.getenv("PROVIDER_SMOKE_MINIMAL_REPAIR"))


def _is_provider_smoke_repair() -> bool:
    app_env = str(os.getenv("APP_ENV") or "").strip().lower()
    if app_env in {"provider-smoke-repair", "provider-smoke-minimal-repair"}:
        return True
    return _is_truthy(os.getenv("PROVIDER_SMOKE_REPAIR_ENV_GUARD_ENABLED"))


def _base_env() -> dict[str, str]:
    return dict(MINIMAL_REPAIR_ENV if _is_minimal_mode() else BROAD_REPAIR_ENV)


def _effective_env() -> dict[str, str]:
    values: dict[str, str] = {}
    base = _base_env()
    for key, default in base.items():
        current = os.getenv(key)
        if key in PRESERVE_IF_SET and current not in (None, ""):
            values[key] = str(current)
        else:
            values[key] = str(default)
    return values


def _write_github_env() -> None:
    path = os.getenv("GITHUB_ENV")
    if not path:
        return
    values = _effective_env()
    try:
        with Path(path).open("a", encoding="utf-8") as fh:
            for key in sorted(values):
                fh.write(f"{key}={values[key]}\n")
    except Exception:
        pass


def install() -> None:
    if not _is_provider_smoke_repair():
        return
    for key, value in _effective_env().items():
        os.environ[str(key)] = str(value)
    _write_github_env()
    atexit.register(_write_github_env)


install()
