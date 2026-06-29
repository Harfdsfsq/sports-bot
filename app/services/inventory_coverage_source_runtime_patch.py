from __future__ import annotations

"""Runtime helpers for fuller inventory and evidence coverage.

The patch is intentionally conservative: it does not create predictions and it
does not relax publication rules. It only makes provider discovery/enrichment use
configured evidence sources more effectively.
"""

import os
from typing import Any

_INSTALLED = False

ENV_DEFAULTS = {
    "DAY_INVENTORY_EXTRA_FIXTURES_ENABLED": "true",
    "DAY_INVENTORY_ENABLE_BZZOIRO": "true",
    "DAY_INVENTORY_ENABLE_SSTATS": "true",
    "DAY_INVENTORY_ENABLE_SPORTLOGIC": "true",
    "DAY_INVENTORY_ENABLE_ALLSPORTSAPI": "true",
    "DAY_INVENTORY_TARGET_SIZE": "300",
    "DAY_INVENTORY_MAX_MATCHES": "300",
    "DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES": "300",
    "DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES": "300",
    "DAY_INVENTORY_BZZOIRO_MAX_PAGES": "30",
    "DAY_INVENTORY_BZZOIRO_MAX_REQUESTS": "220",
    "DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT": "300",
    "DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS": "36",
    "SPORTLOGIC_ENABLED": "true",
    "ENABLE_SPORTLOGIC": "true",
    "SPORTLOGIC_PER_RUN_MAX": "80",
    "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "80",
    "SPORTLOGIC_REQUESTS_MAX_PER_RUN": "80",
    "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "80",
    "SPORTLOGIC_MATCH_LIMIT": "300",
    "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "150",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "150",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "300",
    "BZZOIRO_ODDS_MATCH_LIMIT": "300",
    "BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT": "220",
    "SSTATS_CONTEXT_MATCH_LIMIT": "300",
    "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": "160",
    "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "80",
    "SSTATS_LOOKBACK_DAYS": "45",
    "SSTATS_RECENT_MATCHES": "8",
    "SSTATS_FORM_MIN_SAMPLE_PER_TEAM": "2",
}


def _int_env(name: str, default: int) -> int:
    try:
        return int(float(str(os.getenv(name) or default)))
    except Exception:
        return default


def _setattr_safe(obj: Any, name: str, value: Any) -> None:
    try:
        object.__setattr__(obj, name, value)
    except Exception:
        try:
            setattr(obj, name, value)
        except Exception:
            pass


def _patch_sstats() -> dict[str, Any]:
    try:
        from app.providers.sstats import SStatsContextProvider
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    if getattr(SStatsContextProvider, "_harizon_inventory_coverage_patch", False):
        return {"status": "already_patched"}
    original_init = SStatsContextProvider.__init__

    def __init__(self: Any, settings: Any) -> None:
        _setattr_safe(settings, "sstats_lookback_days", _int_env("SSTATS_LOOKBACK_DAYS", 45))
        _setattr_safe(settings, "sstats_recent_matches", _int_env("SSTATS_RECENT_MATCHES", 8))
        _setattr_safe(settings, "sstats_form_min_sample_per_team", _int_env("SSTATS_FORM_MIN_SAMPLE_PER_TEAM", 2))
        _setattr_safe(settings, "sstats_request_chunk_days", _int_env("SSTATS_REQUEST_CHUNK_DAYS", 5))
        original_init(self, settings)

    SStatsContextProvider.__init__ = __init__
    SStatsContextProvider._harizon_inventory_coverage_patch = True
    return {"status": "patched", "target": "SStatsContextProvider.__init__"}


def install() -> dict[str, Any]:
    global _INSTALLED
    for key, value in ENV_DEFAULTS.items():
        os.environ[key] = value
    if _INSTALLED:
        return {"status": "already_installed", "env_applied": len(ENV_DEFAULTS)}
    _INSTALLED = True
    return {
        "status": "installed",
        "env_applied": len(ENV_DEFAULTS),
        "sstats": _patch_sstats(),
    }
