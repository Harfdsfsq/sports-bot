from __future__ import annotations

from datetime import datetime
from typing import Any

from app.config import Settings
from app.schemas import Match, MatchContext


class ApiFootballContextProvider:
    """Removed provider stub.

    api-football is intentionally removed from active runtime. The class remains
    only to keep older runner imports safe; it never performs external HTTP.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": False,
            "removed": True,
            "provider": "api_football",
            "requests": 0,
            "contexts_built": 0,
            "reason": "removed_from_project",
        }
        return {}, stats, {"sample_contexts": []}

    def supports_match(self, match: Match) -> bool:
        return False

    def _cooldown_until(self) -> datetime | None:
        return None
