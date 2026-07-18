from __future__ import annotations

"""Prevent a partial AllSportsAPI cache from blocking the remaining cohort."""

import os
from typing import Any

_INSTALLED = False
_ORIGINAL_LOAD = None
_ORIGINAL_PRIORITIZE = None


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_LOAD, _ORIGINAL_PRIORITIZE
    if _INSTALLED:
        return {"status": "already_installed"}

    from app.providers.allsportsapi import AllSportsApiOddsProvider

    _ORIGINAL_LOAD = AllSportsApiOddsProvider._load_cached_offers
    _ORIGINAL_PRIORITIZE = AllSportsApiOddsProvider._prioritize_matches

    def _load_cached_offers(self: Any, matches: list[Any]):
        assert callable(_ORIGINAL_LOAD)
        cached = _ORIGINAL_LOAD(self, matches)
        if not cached:
            return None
        wanted = {str(getattr(match, "match_key", "")) for match in matches if getattr(match, "match_key", "")}
        # Returning a 20-match cache for a 300-match request caused the provider to
        # skip the other 280. Reuse only when the requested cohort is fully present.
        return cached if wanted and wanted.issubset(set(cached)) else None

    def _prioritize_matches(self: Any, matches: list[Any]) -> list[Any]:
        assert callable(_ORIGINAL_PRIORITIZE)
        ranked = list(_ORIGINAL_PRIORITIZE(self, matches) or [])
        if len(ranked) <= 1:
            return ranked
        try:
            run_id = int(str(os.getenv("GITHUB_RUN_ID") or os.getenv("GITHUB_RUN_NUMBER") or "0"))
        except Exception:
            run_id = 0
        # Rotate by the provider's actual request capacity so consecutive runs cover
        # different fixtures while preserving the original quality order in blocks.
        try:
            block = max(1, int(getattr(self, "max_http_requests", 96) or 96) - 1)
        except Exception:
            block = 95
        offset = (run_id * block) % len(ranked)
        return ranked[offset:] + ranked[:offset]

    AllSportsApiOddsProvider._load_cached_offers = _load_cached_offers
    AllSportsApiOddsProvider._prioritize_matches = _prioritize_matches
    _INSTALLED = True
    return {
        "status": "installed",
        "partial_cache_never_short_circuits_full_cohort": True,
        "request_block_rotation": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
