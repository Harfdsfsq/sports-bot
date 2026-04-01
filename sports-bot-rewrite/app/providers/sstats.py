from __future__ import annotations

from collections import defaultdict

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext


class SStatsContextProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def fetch_context(self, matches: list[Match]) -> dict[str, MatchContext]:
        if not self.settings.sstats_api_key:
            return {}
        contexts: dict[str, MatchContext] = {}
        by_date: dict[str, list[Match]] = defaultdict(list)
        for match in matches:
            if match.sport_key == 'soccer':
                by_date[match.commence_time.date().isoformat()].append(match)
        async with httpx.AsyncClient(timeout=30.0) as client:
            for date_key, items in by_date.items():
                response = await client.get(
                    'https://api.sstats.net/Games/list',
                    params={
                        'from': date_key,
                        'to': date_key,
                        'limit': 1000,
                        'apikey': self.settings.sstats_api_key,
                    },
                )
                if response.status_code != 200:
                    continue
                payload = response.json()
                rows = payload.get('data') or payload.get('results') or []
                for row in rows:
                    home = str(row.get('HomeTeam') or row.get('home') or '').strip().lower()
                    away = str(row.get('AwayTeam') or row.get('away') or '').strip().lower()
                    expected_home = row.get('xGHome') or row.get('ExpectedGoalsHome')
                    expected_away = row.get('xGAway') or row.get('ExpectedGoalsAway')
                    for match in items:
                        if home and away and home in match.home_team.lower() and away in match.away_team.lower():
                            contexts[match.match_key] = MatchContext(
                                source='sstats',
                                payload=row,
                                expected_home=float(expected_home) if expected_home is not None else None,
                                expected_away=float(expected_away) if expected_away is not None else None,
                            )
        return contexts
