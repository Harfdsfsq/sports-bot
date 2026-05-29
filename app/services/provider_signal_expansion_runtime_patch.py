from __future__ import annotations

"""Per-run signal expansion defaults for HARIZON.

This patch does not relax publication guards.  It only sets conservative per-run
budgets and targeting knobs so scarce providers are used on B-tier/A-tier gaps,
near-misses and soon-to-start matches instead of broad low-value scans.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS = Path(".data/exports/latest-provider-signal-expansion-runtime-patch.json")
UTC = timezone.utc
PATCH_MARKER = "_harizon_provider_signal_expansion_v1"

DEFAULTS: dict[str, str] = {
    # Stronger, but still per-run only.  Bzzoiro is the main reachable second
    # odds/context provider while SportLogic remains probe-only.
    "TARGETED_ENRICHMENT_BZZOIRO_MATCH_LIMIT": "96",
    "TARGETED_ENRICHMENT_SSTATS_MATCH_LIMIT": "140",
    "TARGETED_ENRICHMENT_THESPORTSDB_MATCH_LIMIT": "36",
    "TARGETED_ENRICHMENT_FOOTBALL_DATA_MATCH_LIMIT": "18",
    "TARGETED_ENRICHMENT_API_FOOTBALL_MATCH_LIMIT": "12",
    "TARGETED_ENRICHMENT_ALLSPORTSAPI_MATCH_LIMIT": "12",
    "TARGETED_ENRICHMENT_NEWSAPI_MATCH_LIMIT": "4",
    "TARGETED_ENRICHMENT_GNEWS_MATCH_LIMIT": "4",
    "WEATHER_CONTEXT_MATCH_LIMIT": "6",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "120",
    "BZZOIRO_CONTEXT_GAP_MATCH_LIMIT": "120",
    "BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT": "80",
    "BZZOIRO_EXACT_BRIDGE_MIN_SECOND_SOURCE_TARGET": "25",
    "BZZOIRO_V2_FETCH_EVENT_ODDS": "true",
    "BZZOIRO_V2_FETCH_EVENT_STATS": "true",
    "BZZOIRO_V2_FETCH_EVENT_METADATA": "true",
    "BZZOIRO_V2_FETCH_EVENT_LINEUPS": "true",
    "SSTATS_TEAM_FORM_CACHE_ENABLED": "true",
    "SSTATS_TEAM_FORM_CACHE_MAX_CONTEXTS": "80",
    "EXTERNAL_SIGNAL_PROBES_ENABLED": "true",
    "SECONDARY_PROVIDER_PROBE_MAX_REQUESTS_PER_RUN": "12",
    "HIGHLIGHTLY_PROBE_MAX_REQUESTS": "3",
    "ALLSPORTSAPI_PROBE_MAX_REQUESTS": "3",
    "API_FOOTBALL_PROBE_MAX_REQUESTS": "4",
    "PERFORMANCE_LEDGER_ENABLED": "true",
    "REJECTED_NEAR_MISS_LEDGER_ENABLED": "true",
    # Keep SportLogic safe until /games starts returning current fixtures.
    "SPORTLOGIC_ACTIVE_ODDS_FALLBACK_ENABLED": "false",
    "SPORTLOGIC_ACTIVE_ODDS_TARGETED_CONFIRMATION_ENABLED": "false",
    "SPORTLOGIC_BROAD_FALLBACK_ENABLED": "false",
}


def _write(payload: dict[str, Any]) -> None:
    try:
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _set_default(key: str, value: str) -> bool:
    if os.getenv(key) in (None, ""):
        os.environ[key] = value
        return True
    return False


def install() -> bool:
    if os.getenv("PROVIDER_SIGNAL_EXPANSION_PATCH_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on", "force"}:
        _write({"status": "disabled"})
        return False
    if os.environ.get(PATCH_MARKER) == "1":
        _write({"status": "already_installed", "patch_marker": PATCH_MARKER})
        return True
    changed: dict[str, str] = {}
    preserved: dict[str, str] = {}
    for key, value in DEFAULTS.items():
        if _set_default(key, value):
            changed[key] = value
        else:
            preserved[key] = str(os.getenv(key) or "")
    os.environ[PATCH_MARKER] = "1"
    _write({
        "status": "installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "changed_defaults": changed,
        "preserved_existing": preserved,
        "notes": [
            "Per-run limits only; no daily/monthly provider blocks are introduced here.",
            "Publication thresholds are unchanged; this only improves source/context collection.",
            "SportLogic stays probe-only until current fixtures appear from /games.",
        ],
    })
    return True
