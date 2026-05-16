from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.config import Settings
from app.schemas import Match, Offer


class OddsPapiProvider:
    """Safe OddsPapi compatibility provider.

    The production workflow currently keeps OddsPapi disabled unless a quota
    policy grants it.  This module preserves the provider interface and helper
    methods expected by tests and by PredictionRunner._safe_provider without
    spending quota accidentally.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = getattr(settings, "oddspapi_api_key", None)
        self.base_url = str(getattr(settings, "oddspapi_base_url", "https://api.oddspapi.io/v4")).rstrip("/")
        self.timeout = float(getattr(settings, "oddspapi_timeout_seconds", 12.0) or 12.0)
        self.match_limit = max(0, int(getattr(settings, "oddspapi_match_limit", 16) or 16))
        self.fixture_window_hours = max(1, int(getattr(settings, "oddspapi_fixture_window_hours", 48) or 48))
        self.per_run_max = max(0, int(getattr(settings, "oddspapi_per_run_max", 0) or 0))

    def _fixture_windows(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        if end <= start:
            return [(start, end)]
        windows: list[tuple[datetime, datetime]] = []
        cursor = start
        step = timedelta(hours=self.fixture_window_hours)
        while cursor < end:
            window_end = min(end, cursor + step)
            windows.append((cursor, window_end))
            cursor = window_end
        return windows

    async def fetch_matches(self) -> tuple[list[Match], dict[str, Any], dict[str, Any]]:
        stats = {
            "enabled": bool(self.api_key) and self.per_run_max > 0,
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "response_errors": 0,
            "matches_built": 0,
            "budget_exhausted": self.per_run_max <= 0,
        }
        return [], stats, {"sample_matches": []}

    async def fetch_offers(self, matches: list[Match]) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats = {
            "enabled": bool(self.api_key) and self.per_run_max > 0,
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "response_errors": 0,
            "matches_considered": len(matches or []),
            "offers_parsed": 0,
            "budget_exhausted": self.per_run_max <= 0,
        }
        return {}, stats, {"sample_offers": []}
