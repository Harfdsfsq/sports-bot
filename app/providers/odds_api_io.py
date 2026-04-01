from __future__ import annotations

from collections import defaultdict

import httpx

from app.config import Settings
from app.schemas import Match, Offer

SPORT_MAP = {
    'soccer': 'football',
    'basketball': 'basketball',
    'baseball': 'baseball',
    'icehockey': 'ice-hockey',
}


class OddsApiIoProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = 'https://api.odds-api.io/v3'

    async def fetch_offers(self, matches: list[Match]) -> dict[str, list[Offer]]:
        if not self.settings.odds_api_io_key:
            return {}
        grouped: dict[str, list[Match]] = defaultdict(list)
        for match in matches[: self.settings.max_matches_for_pricing]:
            grouped[match.sport_key].append(match)

        offers_by_match: dict[str, list[Offer]] = defaultdict(list)
        async with httpx.AsyncClient(timeout=30.0) as client:
            for sport_key, sport_matches in grouped.items():
                ids = [m.source_event_id for m in sport_matches if m.source == 'the_odds_api']
                if not ids:
                    continue
                for chunk_start in range(0, len(ids), 10):
                    chunk = ids[chunk_start: chunk_start + 10]
                    response = await client.get(
                        f'{self.base_url}/odds/multi',
                        params={
                            'apikey': self.settings.odds_api_io_key,
                            'eventIds': ','.join(chunk),
                            'bookmakers': 'Bet365,Unibet,Pinnacle,Betfair',
                        },
                    )
                    if response.status_code != 200:
                        continue
                    payload = response.json()
                    if isinstance(payload, dict):
                        for event_id, event in payload.items():
                            match = next((m for m in sport_matches if m.source_event_id == str(event_id)), None)
                            if not match:
                                continue
                            for bookmaker in event.get('bookmakers', []):
                                book_name = bookmaker.get('name', 'unknown')
                                for market in bookmaker.get('markets', []):
                                    key = market.get('key')
                                    if key not in {'h2h', 'totals', 'spreads'}:
                                        continue
                                    for outcome in market.get('outcomes', []):
                                        selection = outcome.get('name', '')
                                        price = float(outcome.get('price', 0) or 0)
                                        point = outcome.get('point')
                                        if price <= 1.01:
                                            continue
                                        offers_by_match[match.match_key].append(
                                            Offer(
                                                source='odds_api_io',
                                                bookmaker=book_name,
                                                family=key,
                                                selection=selection,
                                                price=price,
                                                point=float(point) if point is not None else None,
                                                source_event_id=str(event_id),
                                            )
                                        )
        return offers_by_match
