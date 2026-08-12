from __future__ import annotations

"""Disabled SportLogic provider shim.

Recent production reports show SportLogic consuming 30 requests per run while
returning 0 usable rows.  Until the endpoint/query contract is repaired, keep the
provider as an inert adapter so the rest of the pipeline can run without wasting
quota or producing noisy diagnostics.
"""

from typing import Any


class SportLogicProvider:
    BASE_URL = "https://api.sportlogic.io/api/v1"
    MAX_DAILY = 500

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings
        self.enabled = False
        self.max_requests_per_run = 0

    def _stats(self, mode: str) -> dict[str, Any]:
        return {
            "enabled": False,
            "mode": mode,
            "api_key_present": False,
            "requests": 0,
            "max_requests_per_run": 0,
            "response_errors": 0,
            "fixtures_fetched": 0,
            "games_fetched": 0,
            "matches_built": 0,
            "events_matched": 0,
            "odds_requests": 0,
            "offers_parsed": 0,
            "contexts_built": 0,
            "reason": "disabled_zero_rows_guard",
            "diagnosis": "sportlogic_disabled_zero_rows_guard",
        }

    async def fetch_matches(self):
        return [], self._stats("matches"), {"sample_fixtures": [], "sample_matches": [], "errors": []}

    async def fetch_offers(self, matches):
        return {}, self._stats("offers"), {"sample_fixtures": [], "sample_odds": [], "errors": []}

    async def fetch_context(self, matches):
        return {}, self._stats("context"), {"sample_contexts": [], "errors": []}

    def supports_match(self, match: Any) -> bool:
        return False

    def get_fixtures(self, date: str | None = None) -> list[Any]:
        return []

    def get_odds(self, fixture_id: int | str) -> list[Any]:
        return []

    def get_results(self, fixture_id: int | str) -> dict[str, Any]:
        return {}
