from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.utils import candidate_selection_key, parse_datetime, score_event_match


class SettlementService:
    url = 'https://api.sstats.net/Games/list'
    football_data_url = 'https://api.football-data.org/v4/matches'

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def settle_pending_bets(self, bets: list[dict[str, Any]], now_utc: datetime) -> dict[str, Any]:
        if not getattr(self.settings, 'settlement_enabled', True):
            return {'checked': 0, 'items': []}
        pending = [item for item in bets if self._eligible(item, now_utc)]
        has_sstats = bool(getattr(self.settings, 'sstats_api_key', None))
        has_football_data = bool(getattr(self.settings, 'football_data_api_key', None))
        if not pending or (not has_sstats and not has_football_data):
            return {'checked': len(pending), 'items': []}
        start_date = (min(parse_datetime(item['commence_time']) for item in pending).astimezone(UTC) - timedelta(days=1)).date().isoformat()
        end_date = now_utc.date().isoformat()
        rows: list[dict[str, Any]] = []
        if has_sstats:
            rows.extend(await self._fetch_sstats_rows(start_date, end_date))
        if has_football_data:
            rows.extend(await self._fetch_football_data_rows(start_date, end_date))
        items: list[dict[str, Any]] = []
        for bet in pending:
            row = self._match_row(bet, rows)
            if row is None:
                continue
            home_goals = self._extract_result(row, 'home')
            away_goals = self._extract_result(row, 'away')
            if home_goals is None or away_goals is None:
                continue
            outcome, pnl = self._grade_bet(bet, float(home_goals), float(away_goals))
            if outcome is None:
                continue
            items.append({
                'fingerprint': str(bet.get('fingerprint') or ''),
                'outcome': outcome,
                'pnl': round(pnl, 2),
                'final_home_goals': float(home_goals),
                'final_away_goals': float(away_goals),
                'settled_at': now_utc.isoformat(),
                'source': str(row.get('_settlement_source') or 'unknown'),
                'note': f"{self._extract_team_name(row, 'home')} {int(home_goals)}:{int(away_goals)} {self._extract_team_name(row, 'away')}",
            })
        return {'checked': len(pending), 'items': items}

    def _eligible(self, bet: dict[str, Any], now_utc: datetime) -> bool:
        if str(bet.get('status') or '') != 'pending':
            return False
        try:
            commence = parse_datetime(str(bet.get('commence_time'))).astimezone(UTC)
        except Exception:
            return False
        grace = timedelta(minutes=int(getattr(self.settings, 'settlement_grace_minutes', 180) or 180))
        lookback = timedelta(days=int(getattr(self.settings, 'settlement_lookback_days', 5) or 5))
        return commence + grace <= now_utc <= commence + lookback

    async def _fetch_sstats_rows(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        limit = 1000
        total_count: int | None = None
        seen_signatures: set[tuple[Any, ...]] = set()
        try:
            async with httpx.AsyncClient(timeout=float(getattr(self.settings, 'sstats_timeout_seconds', 25.0) or 25.0)) as client:
                while True:
                    response = await client.get(
                        self.url,
                        params={
                            'from': start_date,
                            'to': end_date,
                            'limit': limit,
                            'offset': offset,
                            'apikey': str(self.settings.sstats_api_key),
                        },
                        headers={'X-API-Key': str(self.settings.sstats_api_key)},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if isinstance(payload, dict):
                        batch = payload.get('data') or payload.get('results') or []
                        raw_total = payload.get('count')
                        if raw_total not in (None, ''):
                            try:
                                total_count = int(raw_total)
                            except Exception:
                                total_count = total_count
                    elif isinstance(payload, list):
                        batch = payload
                    else:
                        batch = []
                    if not isinstance(batch, list) or not batch:
                        break
                    added = 0
                    for item in batch:
                        if not isinstance(item, dict):
                            continue
                        signature = (
                            item.get('id'),
                            item.get('flashId'),
                            item.get('date'),
                            self._extract_team_name(item, 'home'),
                            self._extract_team_name(item, 'away'),
                        )
                        if signature in seen_signatures:
                            continue
                        seen_signatures.add(signature)
                        rows.append({**item, '_settlement_source': 'sstats'})
                        added += 1
                    if len(batch) < limit or added == 0:
                        break
                    offset += len(batch)
                    if total_count is not None and offset >= total_count:
                        break
        except Exception:
            return []
        return rows

    async def _fetch_football_data_rows(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(
                timeout=float(getattr(self.settings, 'football_data_timeout_seconds', 20.0) or 20.0),
                headers={'X-Auth-Token': str(self.settings.football_data_api_key)},
            ) as client:
                response = await client.get(
                    self.football_data_url,
                    params={
                        'dateFrom': start_date,
                        'dateTo': end_date,
                        'status': 'FINISHED',
                        'limit': 200,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return []
        rows = payload.get('matches') if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return []
        return [{**item, '_settlement_source': 'football_data'} for item in rows if isinstance(item, dict)]

    def _match_row(self, bet: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        best_row = None
        best_key: tuple[int, float, int] | None = None
        match_start = parse_datetime(str(bet.get('commence_time')))
        for row in rows:
            event_start = self._extract_start(row)
            if event_start is None:
                continue
            score, _quality = score_event_match(
                sport=str(bet.get('sport_key') or 'soccer'),
                match_home=str(bet.get('home_team') or ''),
                match_away=str(bet.get('away_team') or ''),
                match_start=match_start,
                match_league=str(bet.get('league_name') or ''),
                event_home=self._extract_team_name(row, 'home'),
                event_away=self._extract_team_name(row, 'away'),
                event_start=event_start,
                event_league=self._extract_league_name(row),
                exact_tolerance_hours=24,
                fuzzy_tolerance_hours=60,
            )
            has_score = int(self._extract_result(row, 'home') is not None and self._extract_result(row, 'away') is not None)
            source_priority = 2 if str(row.get('_settlement_source') or '') == 'sstats' else 1
            key = (has_score, score, source_priority)
            if best_key is None or key > best_key:
                best_key = key
                best_row = row
        if best_row is None or best_key is None or best_key[1] < 70.0:
            return None
        return best_row

    def _grade_bet(self, bet: dict[str, Any], home_goals: float, away_goals: float) -> tuple[str | None, float]:
        family = str(bet.get('family') or '')
        selection = str(bet.get('selection') or '')
        selection_key = str(
            bet.get('selection_key')
            or candidate_selection_key(
                family,
                selection,
                point=bet.get('point'),
                team_side=bet.get('team_side'),
                home_team=str(bet.get('home_team') or ''),
                away_team=str(bet.get('away_team') or ''),
            )
        ).strip().lower()
        point = bet.get('point')
        point = float(point) if point not in (None, '') else None
        odds = float(bet.get('odds') or 0.0)
        stake = float(bet.get('stake_amount') or 0.0)
        if odds <= 1.0 or stake <= 0:
            return None, 0.0
        if family == 'h2h':
            target = selection_key if selection_key in {'home', 'away', 'draw'} else self._side_from_selection(selection, bet)
            win = (target == 'home' and home_goals > away_goals) or (target == 'away' and away_goals > home_goals) or (target == 'draw' and home_goals == away_goals)
            return ('won', round((odds - 1.0) * stake, 2)) if win else ('lost', round(-stake, 2))
        if family == 'dnb':
            target = selection_key if selection_key in {'home', 'away'} else self._side_from_selection(selection, bet)
            if home_goals == away_goals:
                return 'push', 0.0
            win = (target == 'home' and home_goals > away_goals) or (target == 'away' and away_goals > home_goals)
            return ('won', round((odds - 1.0) * stake, 2)) if win else ('lost', round(-stake, 2))
        if family == 'doubleChance':
            if selection_key == 'home_draw':
                win = home_goals >= away_goals
            elif selection_key == 'away_draw':
                win = away_goals >= home_goals
            elif selection_key == '12':
                win = home_goals != away_goals
            else:
                return None, 0.0
            return ('won', round((odds - 1.0) * stake, 2)) if win else ('lost', round(-stake, 2))
        if family == 'btts':
            yes = home_goals > 0 and away_goals > 0
            if selection_key not in {'yes', 'no'}:
                return None, 0.0
            wants_yes = selection_key == 'yes'
            win = yes if wants_yes else not yes
            return ('won', round((odds - 1.0) * stake, 2)) if win else ('lost', round(-stake, 2))
        if family == 'totals':
            return self._grade_total(selection_key, point, home_goals + away_goals, odds, stake)
        if family == 'teamTotals':
            side = str(bet.get('team_side') or self._side_from_selection(selection, bet))
            team_goals = home_goals if side == 'home' else away_goals
            return self._grade_total(selection_key, point, team_goals, odds, stake)
        if family == 'spreads':
            side = selection_key if selection_key in {'home', 'away'} else self._side_from_selection(selection, bet)
            margin = (home_goals - away_goals) if side == 'home' else (away_goals - home_goals)
            return self._grade_margin(margin + float(point or 0.0), odds, stake)
        return None, 0.0

    def _grade_total(self, token: str, point: float | None, value: float, odds: float, stake: float) -> tuple[str, float]:
        if point is None:
            return 'void', 0.0
        if token not in {'over', 'under'}:
            return 'void', 0.0
        wants_over = token == 'over'
        margin = value - point if wants_over else point - value
        return self._grade_margin(margin, odds, stake)

    def _grade_margin(self, margin: float, odds: float, stake: float) -> tuple[str, float]:
        frac = round(abs(margin - int(margin)), 2)
        if frac in {0.25, 0.75}:
            left = self._grade_margin_base(margin - 0.25, odds, stake / 2.0)
            right = self._grade_margin_base(margin + 0.25, odds, stake / 2.0)
            pnl = round(left[1] + right[1], 2)
            outcomes = {left[0], right[0]}
            if outcomes == {'won'}:
                return 'won', pnl
            if outcomes == {'lost'}:
                return 'lost', pnl
            if outcomes == {'push'}:
                return 'push', pnl
            if 'won' in outcomes and 'push' in outcomes:
                return 'half_won', pnl
            if 'lost' in outcomes and 'push' in outcomes:
                return 'half_lost', pnl
            return 'void', pnl
        return self._grade_margin_base(margin, odds, stake)

    @staticmethod
    def _grade_margin_base(margin: float, odds: float, stake: float) -> tuple[str, float]:
        if margin > 0:
            return 'won', round((odds - 1.0) * stake, 2)
        if margin == 0:
            return 'push', 0.0
        return 'lost', round(-stake, 2)

    def _side_from_selection(self, selection: str, bet: dict[str, Any]) -> str:
        key = candidate_selection_key(
            str(bet.get('family') or ''),
            selection,
            point=bet.get('point'),
            team_side=bet.get('team_side'),
            home_team=str(bet.get('home_team') or ''),
            away_team=str(bet.get('away_team') or ''),
        )
        if key in {'home', 'away', 'draw'}:
            return key
        return 'away'

    @staticmethod
    def _extract_team_name(row: dict[str, Any], side: str) -> str:
        nested_key = 'homeTeam' if side == 'home' else 'awayTeam'
        nested = row.get(nested_key)
        if isinstance(nested, dict) and nested.get('name'):
            return str(nested.get('name')).strip()
        keys = ['HomeTeam', 'home', 'home_name', 'Home', 'team_home', 'homeTeamName'] if side == 'home' else ['AwayTeam', 'away', 'away_name', 'Away', 'team_away', 'awayTeamName']
        for key in keys:
            value = row.get(key)
            if value:
                return str(value).strip()
        return ''

    @staticmethod
    def _extract_league_name(row: dict[str, Any]) -> str:
        competition = row.get('competition')
        if isinstance(competition, dict) and competition.get('name'):
            return str(competition.get('name')).strip()
        season = row.get('season')
        if isinstance(season, dict):
            league = season.get('league')
            if isinstance(league, dict) and league.get('name'):
                return str(league.get('name')).strip()
        for key in ['League', 'league', 'Tournament', 'CompetitionName', 'competition']:
            value = row.get(key)
            if isinstance(value, dict):
                if value.get('name'):
                    return str(value.get('name')).strip()
                continue
            if value:
                return str(value).strip()
        return ''

    @staticmethod
    def _extract_start(row: dict[str, Any]) -> Any | None:
        for key in ['utcDate', 'date', 'Date', 'GameStart', 'StartTime', 'datetime', 'MatchDate']:
            value = row.get(key)
            if value not in (None, ''):
                try:
                    return parse_datetime(str(value))
                except Exception:
                    continue
        return None

    @staticmethod
    def _extract_result(row: dict[str, Any], side: str) -> float | None:
        score = row.get('score')
        if isinstance(score, dict):
            full_time = score.get('fullTime')
            if isinstance(full_time, dict):
                key = 'home' if side == 'home' else 'away'
                value = full_time.get(key)
                if value not in (None, ''):
                    try:
                        return float(value)
                    except Exception:
                        pass
        keys = ['homeResult', 'homeFTResult', 'HomeScore'] if side == 'home' else ['awayResult', 'awayFTResult', 'AwayScore']
        for key in keys:
            value = row.get(key)
            if value in (None, ''):
                continue
            try:
                return float(value)
            except Exception:
                continue
        return None
