from __future__ import annotations

from typing import Protocol

from app.schemas import Match, MatchContext, Offer


class SnapshotProvider(Protocol):
    async def fetch(self) -> dict: ...


class OddsProvider(Protocol):
    async def fetch_offers(self, matches: list[Match]) -> tuple[dict[str, list[Offer]], dict, dict]: ...


class ContextProvider(Protocol):
    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict, dict]: ...
