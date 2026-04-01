from __future__ import annotations

from typing import Protocol

from app.schemas import Match, Offer, MatchContext


class EventsProvider(Protocol):
    async def fetch_matches(self) -> list[Match]: ...


class OddsProvider(Protocol):
    async def fetch_offers(self, matches: list[Match]) -> dict[str, list[Offer]]: ...


class ContextProvider(Protocol):
    async def fetch_context(self, matches: list[Match]) -> dict[str, MatchContext]: ...
