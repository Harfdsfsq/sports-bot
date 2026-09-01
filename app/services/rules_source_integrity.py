from __future__ import annotations

"""Rules-compliant source identity for publication evidence.

Internal inventory/model labels are not context providers. Aliases produced by
one upstream provider collapse to one independent source. Two odds-api.io
accounts likewise remain one provider source; bookmaker quorum is counted
separately by coverage_contract.
"""

INVALID_CONTEXT_SOURCES = {
    "dayinventory",
    "day_inventory",
    "inventory_context",
    "providerdaydiscoverycanonicalpool",
    "provider_day_discovery_canonical_pool",
    "model",
    "model_xg",
    "market",
    "market_signal",
    "market_implied_xg",
    "inventory",
    "ensemble",
    "unknown",
}

SOURCE_ALIASES = {
    "sstats_xg": "sstats",
    "sstats_form": "sstats",
    "sstats_current_odds": "sstats",
    "bzzoiro_v2": "bzzoiro",
    "bzzoiro_predictions": "bzzoiro",
    "odds_api_io_account1": "odds_api_io",
    "odds_api_io_account2": "odds_api_io",
    "account1": "odds_api_io",
    "account2": "odds_api_io",
}

_INSTALLED = False


def install() -> dict[str, object]:
    global _INSTALLED
    if _INSTALLED:
        return {"installed": True, "status": "already_installed"}
    from app.services import coverage_contract

    coverage_contract.AGGREGATE_CONTEXT_SOURCES.update(INVALID_CONTEXT_SOURCES)
    coverage_contract.ODDS_SOURCE_ALIASES.update(SOURCE_ALIASES)
    _INSTALLED = True
    return {
        "installed": True,
        "status": "installed",
        "invalid_context_sources": sorted(INVALID_CONTEXT_SOURCES),
        "source_aliases": dict(sorted(SOURCE_ALIASES.items())),
    }
