from __future__ import annotations

"""Keep provider-smoke repair workflow env intact without overriding step env.

Production quota scripts can write values such as
DAY_INVENTORY_FORCE_PROVIDER_MERGE=false to GITHUB_ENV. Provider-smoke needs the
repair defaults below, but step-level values in provider-smoke.yml must win.
That is important for quota-sensitive probes such as SportLogic and for keeping
full-data disabled inside the fast smoke step.
"""

import atexit
import os
from pathlib import Path


REPAIR_ENV = {
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
    # SportLogic is intentionally excluded from broad matching smoke by default:
    # the latest run exhausted the 500/day SportLogic quota. Use a dedicated
    # SportLogic repair run/env flag when debugging that provider.
    "PROVIDER_SMOKE_MATCHING_PROVIDERS": "sstats,bzzoiro,football_data,allsportsapi",
    # Default is safe; workflow steps explicitly set true only for the single
    # full-data probe step.
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

# These keys are commonly overridden by individual workflow steps. If present in
# the process environment, preserve the current value rather than replacing it.
PRESERVE_IF_SET = {
    "API_FULL_SMOKE_ENABLED",
    "API_FULL_SMOKE_SPORTLOGIC_ENABLED",
    "API_FULL_SMOKE_SPORTLOGIC_DETAILS_ENABLED",
    "PROVIDER_SMOKE_MATCHING_PROVIDERS",
    "PROVIDER_SMOKE_REPEATS",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT",
}


def _is_provider_smoke_repair() -> bool:
    app_env = str(os.getenv("APP_ENV") or "").strip().lower()
    if app_env == "provider-smoke-repair":
        return True
    flag = str(os.getenv("PROVIDER_SMOKE_REPAIR_ENV_GUARD_ENABLED") or "").strip().lower()
    return flag in {"1", "true", "yes", "on", "force"}


def _effective_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for key, default in REPAIR_ENV.items():
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
