from __future__ import annotations

from typing import Any

_INSTALLED = False


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    from app.providers import sstats_pari_parser
    from app.services import sstats_pari_runtime_repair

    extractor = sstats_pari_parser.extract_odds
    sstats_pari_runtime_repair._extract_odds = extractor
    _INSTALLED = True
    return {
        "status": "installed",
        "runtime_extractor": "app.providers.sstats_pari_parser.extract_odds",
        "current_odds_supported": True,
        "nested_market_rows_supported": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
