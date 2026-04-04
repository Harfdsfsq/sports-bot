from __future__ import annotations

from typing import Any

from app.config import Settings
from app.schemas import Match, Offer


class OddsApiIoProvider:
    """
    Безопасный провайдер для odds-api.io.

    В текущей бесплатной схеме бота основной поток офферов идёт через Bookies API.
    Этот провайдер оставлен как мягко-опциональный источник и не должен валить запуск,
    если ключ не задан, формат данных меняется или источник временно не используется.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def fetch_offers(
        self,
        matches: list[Match],
    ) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            'enabled': bool(self.settings.odds_api_io_key),
            'api_key_present': bool(self.settings.odds_api_io_key),
            'event_requests': 0,
            'odds_requests': 0,
            'response_errors': 0,
            'events_fetched': 0,
            'events_matched': 0,
            'matched_exact': 0,
            'matched_loose': 0,
            'matched_fuzzy': 0,
            'unmatched_offer_events': 0,
            'markets_parsed': 0,
            'offers_parsed': 0,
            'event_http_statuses': [],
            'odds_http_statuses': [],
            'payload_shapes': [],
            'bookmakers_seen': 0,
            'last_body_preview': None,
            'simulated_skipped': 0,
        }
        preview: dict[str, Any] = {
            'note': 'odds_api_io отключён как обязательный источник; основной поток идёт через bookies_api',
            'matches_considered': min(len(matches), self.settings.max_matches_for_pricing),
        }

        # Ничего не ломаем, даже если ключ не задан.
        if not self.settings.odds_api_io_key or not matches:
            return {}, stats, preview

        # Намеренно no-op: в текущем репозитории источник не должен быть критическим.
        return {}, stats, preview
