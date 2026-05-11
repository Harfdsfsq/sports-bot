from __future__ import annotations

"""Keep provider-smoke repair workflow env intact after quota guards.

Several runtime policy scripts intentionally optimize normal production runs and
write values such as DAY_INVENTORY_FORCE_PROVIDER_MERGE=false to GITHUB_ENV.
For provider-smoke this is harmful: the workflow is a diagnostic repair stand
and must keep broad source merge + Bzzoiro/SportLogic/SStats probes enabled.

This module is loaded from usercustomize.py. It applies values immediately for
the current Python process and appends them again at process exit so they win
over guard scripts that write conflicting values during the same process.
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
    "PROVIDER_SMOKE_MATCHING_PROVIDERS": "sstats,bzzoiro,football_data,sportlogic,allsportsapi",
    "API_FULL_SMOKE_ENABLED": "true",
    "API_FULL_SMOKE_BZZOIRO_ENABLED": "true",
    "API_FULL_SMOKE_FOOTBALL_DATA_ENABLED": "true",
    "API_FULL_SMOKE_ODDS_API_IO_ENABLED": "true",
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


def _is_provider_smoke_repair() -> bool:
    app_env = str(os.getenv("APP_ENV") or "").strip().lower()
    if app_env == "provider-smoke-repair":
        return True
    flag = str(os.getenv("PROVIDER_SMOKE_REPAIR_ENV_GUARD_ENABLED") or "").strip().lower()
    return flag in {"1", "true", "yes", "on", "force"}


def _write_github_env() -> None:
    path = os.getenv("GITHUB_ENV")
    if not path:
        return
    try:
        with Path(path).open("a", encoding="utf-8") as fh:
            for key in sorted(REPAIR_ENV):
                fh.write(f"{key}={REPAIR_ENV[key]}\n")
    except Exception:
        pass


def install() -> None:
    if not _is_provider_smoke_repair():
        return
    for key, value in REPAIR_ENV.items():
        os.environ[str(key)] = str(value)
    _write_github_env()
    atexit.register(_write_github_env)


install()
