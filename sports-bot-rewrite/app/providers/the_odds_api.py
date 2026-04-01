from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from app.config import Settings
from app.schemas import Match
from app.utils import dedupe_key, is_low_tier_league, normalize_text

SPORT_KEYS = {
    'soccer': ['soccer_epl', 'soccer_spain_la_liga', 'soccer_italy_serie_a', 'soccer_brazil_campeonato'],
    'basketball': ['basketball_nba'],
    'baseball': ['baseball_mlb'],
    'icehockey': ['icehockey_nhl'],
}


class TheOddsEventsProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = 'https://api.the-odds-api.com/v4'

    async def fetch_matches(self) -> list[Match]:
        if not self.settings.the_odds_api_key:
            return []
        matches: list[Match] = []
        now = datetime.now(UTC)
        cutoff = now + timedelta(days=self.settings.run_days_ahead)
        async with httpx.AsyncClient(timeout=30.0) as client:
            for sport_key in self.settings.run_sports:
                for league_key in SPORT_KEYS.get(sport_key, []):
                    url = f'{self.base_url}/sports/{league_key}/odds/'
                    params = {
                        'apiKey': self.settings.the_odds_api_key,
                        'regions': 'eu,uk',
                        'markets': 'h2h,totals,spreads',
                        'oddsFormat': 'decimal',
                        'dateFormat': 'iso',
                    }
                    response = await client.get(url, params=params)
                    if response.status_code != 200:
                        continue
                    for item in response.json():
                        commence = datetime.fromisoformat(item['commence_time'].replace('Z', '+00:00'))
                        if not now <= commence <= cutoff:
                            continue
                        league_name = item.get('sport_title', league_key)
                        if not self.settings.allow_low_tier and is_low_tier_league(league_name):
                            continue
                        matches.append(
                            Match(
                                source='the_odds_api',
                                source_event_id=str(item['id']),
                                sport_key=sport_key,
                                league_name=league_name,
                                home_team=item['home_team'],
                                away_team=item['away_team'],
                                commence_time=commence,
                                home_team_norm=normalize_text(item['home_team']),
                                away_team_norm=normalize_text(item['away_team']),
                                tier='low' if is_low_tier_league(league_name) else 'top',
                                metadata={'source_key': league_key, 'match_key': dedupe_key(sport_key, item['home_team'], item['away_team'], commence.date().isoformat())},
                            )
                        )
        return matches
