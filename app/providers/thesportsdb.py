from __future__ import annotations

from collections import Counter
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import canonicalize_league_name, canonicalize_team_name, clamp, team_similarity

LEAGUE_ALIASES = {
    'italy serie a': 'italian serie a',
    'italy serie b': 'italian serie b',
    'italy serie c': 'italian serie c',
    'italy serie c girone a': 'italian serie c',
    'italy serie c girone b': 'italian serie c',
    'italy serie c girone c': 'italian serie c',
    'italy serie c group a': 'italian serie c',
    'italy serie c group b': 'italian serie c',
    'italy serie c group c': 'italian serie c',
    'italian serie c girone a': 'italian serie c',
    'italian serie c girone b': 'italian serie c',
    'italian serie c girone c': 'italian serie c',
    'italian serie c group a': 'italian serie c',
    'italian serie c group b': 'italian serie c',
    'italian serie c group c': 'italian serie c',
    'england premier league': 'english premier league',
    'england championship': 'english league championship',
    'england league one': 'english league one',
    'england league two': 'english league two',
    'spain la liga': 'spanish la liga',
    'spain la liga 2': 'spanish la liga 2',
    'germany bundesliga': 'german bundesliga',
    'germany bundesliga 2': 'german bundesliga 2',
    'france ligue 1': 'french ligue 1',
    'france ligue 2': 'french ligue 2',
    'netherlands eredivisie': 'dutch eredivisie',
    'belgium pro league': 'belgian pro league',
    'portugal primeira liga': 'portuguese primeira liga',
    'scotland premiership': 'scottish premiership',
    'league one': 'english league one',
    'league 1': 'english league one',
    'league two': 'english league two',
    'league 2': 'english league two',
    'championship': 'english league championship',
    'bundesliga 2': 'german bundesliga 2',
    '2. bundesliga': 'german bundesliga 2',
    'eerste divisie': 'dutch eerste divisie',
    'scottish championship': 'scottish championship',
    'scottish league one': 'scottish league one',
    'scottish league two': 'scottish league two',
    'international clubs copa libertadores': 'copa libertadores',
    'conmebol copa libertadores': 'copa libertadores',
    'copa conmebol libertadores': 'copa libertadores',
    'international clubs copa sudamericana': 'copa sudamericana',
    'conmebol copa sudamericana': 'copa sudamericana',
    'copa conmebol sudamericana': 'copa sudamericana',
    'international clubs uefa champions league': 'uefa champions league',
    'international clubs uefa europa league': 'uefa europa league',
    'international clubs uefa europa conference league': 'uefa europa conference league',
}


class TheSportsDbContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        api_key = str(settings.thesportsdb_api_key or '123').strip() or '123'
        self.base_url = f"{(settings.thesportsdb_base_url or 'https://www.thesportsdb.com/api/v1/json').rstrip('/')}/{api_key}"
        self.timeout = float(settings.thesportsdb_timeout_seconds or 20.0)

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            'enabled': bool(self.settings.enable_thesportsdb_context),
            'requests': 0,
            'response_errors': 0,
            'leagues_resolved': 0,
            'tables_fetched': 0,
            'contexts_built': 0,
            'missing_table_rows': 0,
            'partial_contexts_built': 0,
            'last_body_preview': None,
            'http_statuses': [],
        }
        preview: dict[str, Any] = {
            'sample_leagues': [],
            'sample_tables': [],
            'sample_contexts': [],
        }
        if not self.settings.enable_thesportsdb_context:
            return {}, stats, preview

        soccer_matches = [match for match in matches if match.sport_key == 'soccer']
        if not soccer_matches:
            return {}, stats, preview

        counts = Counter(match.league_name for match in soccer_matches)
        league_names = [name for name, _ in sorted(counts.items(), key=lambda item: (-item[1], self._normalize_league_key(item[0])))]
        max_leagues = max(1, int(self.settings.thesportsdb_max_leagues or 24))
        if len(league_names) > max_leagues:
            league_names = league_names[:max_leagues]
            stats['league_limit_applied'] = max_leagues

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            all_leagues = await self._fetch_json(client, '/all_leagues.php', stats)
            league_rows = self._extract_rows(all_leagues, 'leagues')
            if league_rows and not preview['sample_leagues']:
                preview['sample_leagues'] = league_rows[:5]

            league_map = self._resolve_league_ids(league_names, league_rows)
            stats['leagues_resolved'] = len(league_map)

            tables_by_league: dict[str, list[dict[str, Any]]] = {}
            for league_name, league_id in league_map.items():
                payload = await self._fetch_json(client, '/lookuptable.php', stats, params={'l': league_id})
                rows = self._extract_rows(payload, 'table')
                if not rows:
                    continue
                tables_by_league[league_name] = rows
                stats['tables_fetched'] += 1
                if len(preview['sample_tables']) < 3:
                    preview['sample_tables'].append({'league_name': league_name, 'rows': rows[:3]})

        contexts: dict[str, MatchContext] = {}
        for match in soccer_matches:
            table_rows = tables_by_league.get(match.league_name)
            if not table_rows:
                continue
            home_row = self._match_table_row(match.home_team, table_rows)
            away_row = self._match_table_row(match.away_team, table_rows)
            partial = False
            if home_row is None or away_row is None:
                stats['missing_table_rows'] += 1
                if not getattr(self.settings, 'thesportsdb_allow_partial_context', True):
                    continue
                avg_row = self._league_average_row(table_rows)
                if home_row is None and away_row is None:
                    continue
                if home_row is None:
                    home_row = avg_row
                    partial = True
                if away_row is None:
                    away_row = avg_row
                    partial = True
            context = self._rows_to_context(match, home_row, away_row, partial=partial)
            contexts[match.match_key] = context
            stats['contexts_built'] += 1
            if partial:
                stats['partial_contexts_built'] += 1
            if len(preview['sample_contexts']) < 6:
                preview['sample_contexts'].append(
                    {
                        'match_key': match.match_key,
                        'league_name': match.league_name,
                        'expected_home': context.expected_home,
                        'expected_away': context.expected_away,
                        'partial': partial,
                    }
                )

        return contexts, stats, preview

    async def _fetch_json(self, client: httpx.AsyncClient, path: str, stats: dict[str, Any], params: dict[str, Any] | None = None) -> Any | None:
        stats['requests'] += 1
        try:
            response = await client.get(f'{self.base_url}{path}', params=params)
        except Exception as exc:
            stats['response_errors'] += 1
            stats['last_body_preview'] = f'request failed: {exc}'
            return None
        stats['http_statuses'].append(response.status_code)
        stats['last_body_preview'] = response.text[:1200]
        if response.status_code != 200:
            stats['response_errors'] += 1
            return None
        try:
            return response.json()
        except Exception:
            stats['response_errors'] += 1
            return None

    @staticmethod
    def _extract_rows(payload: Any, key: str) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        rows = payload.get(key)
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _resolve_league_ids(self, league_names: list[str], league_rows: list[dict[str, Any]]) -> dict[str, str]:
        resolved: dict[str, str] = {}
        normalized_rows: list[tuple[str, str]] = []
        for row in league_rows:
            if str(row.get('strSport') or '').lower() != 'soccer':
                continue
            row_name = str(row.get('strLeague') or '')
            row_alt = str(row.get('strLeagueAlternate') or '')
            keys = {self._normalize_league_key(row_name)}
            if row_alt:
                keys.add(self._normalize_league_key(row_alt))
            for row_key in keys:
                if row_key:
                    normalized_rows.append((str(row.get('idLeague') or ''), row_key))
        for league_name in league_names:
            target = self._normalize_league_key(league_name)
            best_id: str | None = None
            best_score = 0.0
            for league_id, row_key in normalized_rows:
                if row_key == target:
                    score = 1.0
                elif row_key in target or target in row_key:
                    score = 0.95
                else:
                    score = team_similarity(target, row_key)
                if score > best_score:
                    best_score = score
                    best_id = league_id
            threshold = float(getattr(self.settings, 'thesportsdb_league_match_threshold', 0.60) or 0.60)
            if best_id and best_score >= threshold:
                resolved[league_name] = best_id
        return resolved

    def _match_table_row(self, team_name: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        target = self._normalize_team_key(team_name)
        best_row: dict[str, Any] | None = None
        best_score = 0.0
        for row in rows:
            row_name = str(row.get('strTeam') or row.get('name') or row.get('team') or '')
            row_key = self._normalize_team_key(row_name)
            score = team_similarity(target, row_key)
            if row_key == target:
                score = 1.0
            if score > best_score:
                best_score = score
                best_row = row
        threshold = float(getattr(self.settings, 'thesportsdb_partial_match_threshold', 0.58) or 0.58)
        return best_row if best_score >= threshold else None

    @staticmethod
    def _league_average_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
        numeric_keys = ['intPlayed', 'intPoints', 'intGoalsFor', 'intGoalsAgainst', 'intWin', 'intDraw', 'intLoss']
        aggregate: dict[str, str] = {'strTeam': 'League Average'}
        valid_rows = [row for row in rows if isinstance(row, dict)]
        for key in numeric_keys:
            values = []
            for row in valid_rows:
                try:
                    values.append(float(row.get(key)))
                except Exception:
                    pass
            if values:
                aggregate[key] = str(round(sum(values) / len(values), 3))
        return aggregate

    def _normalize_team_key(self, team_name: str) -> str:
        key = canonicalize_team_name(team_name)
        replacements = {
            ' football club ': ' ',
            ' futbol club ': ' ',
            ' soccer club ': ' ',
            ' athletic club ': ' ',
            ' association football club ': ' ',
            ' fc ': ' ',
            ' cf ': ' ',
            ' ac ': ' ',
            ' sc ': ' ',
            ' afc ': ' ',
            ' u23 ': ' ',
            ' u21 ': ' ',
            ' u19 ': ' ',
            ' women ': ' ',
            ' wfc ': ' ',
            ' calcio ': ' ',
            ' united ': ' utd ',
            ' utd ': ' united ',
            ' saint ': ' st ',
            ' st ': ' saint ',
            ' deportivo ': ' depo ',
            ' athletic ': ' ath ',
            ' atletico ': ' atleti ',
        }
        padded = f' {key} '
        for src, dst in replacements.items():
            padded = padded.replace(src, dst)
        return ' '.join(padded.split())

    def _rows_to_context(self, match: Match, home_row: dict[str, Any], away_row: dict[str, Any], partial: bool = False) -> MatchContext:
        home_played = max(self._to_float(home_row, 'intPlayed', 'played', 'gamesPlayed', 'games') or 0.0, 1.0)
        away_played = max(self._to_float(away_row, 'intPlayed', 'played', 'gamesPlayed', 'games') or 0.0, 1.0)

        home_points = self._to_float(home_row, 'intPoints', 'points', 'pts') or 0.0
        away_points = self._to_float(away_row, 'intPoints', 'points', 'pts') or 0.0
        home_gf = self._to_float(home_row, 'intGoalsFor', 'goalsfor', 'gf', 'for') or 0.0
        home_ga = self._to_float(home_row, 'intGoalsAgainst', 'goalsagainst', 'ga', 'against') or 0.0
        away_gf = self._to_float(away_row, 'intGoalsFor', 'goalsfor', 'gf', 'for') or 0.0
        away_ga = self._to_float(away_row, 'intGoalsAgainst', 'goalsagainst', 'ga', 'against') or 0.0
        home_wins = self._to_float(home_row, 'intWin', 'wins', 'win') or 0.0
        home_draws = self._to_float(home_row, 'intDraw', 'draws', 'draw') or 0.0
        away_wins = self._to_float(away_row, 'intWin', 'wins', 'win') or 0.0
        away_draws = self._to_float(away_row, 'intDraw', 'draws', 'draw') or 0.0

        home_ppg = home_points / home_played
        away_ppg = away_points / away_played
        home_gf_pg = home_gf / home_played
        home_ga_pg = home_ga / home_played
        away_gf_pg = away_gf / away_played
        away_ga_pg = away_ga / away_played
        home_form_score = (home_wins + 0.5 * home_draws) / home_played
        away_form_score = (away_wins + 0.5 * away_draws) / away_played

        expected_home = clamp(((home_gf_pg + away_ga_pg) / 2.0) + 0.16, 0.35, 3.2)
        expected_away = clamp(((away_gf_pg + home_ga_pg) / 2.0) - 0.04, 0.25, 3.0)

        strength_delta = clamp((home_ppg - away_ppg) * 0.18 + 0.08 + (home_form_score - away_form_score) * 0.10, -0.40, 0.40)
        draw_prob = clamp(0.25 - abs(strength_delta) * 0.16, 0.14, 0.30)
        home_prob = 0.38 + strength_delta
        away_prob = 1.0 - home_prob - draw_prob
        probs = self._normalize_probs(home_prob, away_prob, draw_prob)

        confidence = 57.0
        if home_gf_pg > 0 and away_gf_pg > 0:
            confidence += 3.0
        if home_ppg > 0 and away_ppg > 0:
            confidence += 2.0
        if home_form_score > 0 and away_form_score > 0:
            confidence += 1.0
        if partial:
            confidence -= 6.0
        confidence = clamp(confidence, 51.0, 66.0)

        return MatchContext(
            source='thesportsdb',
            payload={'home_row': home_row, 'away_row': away_row},
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=probs['home'],
            away_win_probability=probs['away'],
            confidence=confidence,
            details={
                'thesportsdb_draw_probability': probs['draw'],
                'thesportsdb_home_ppg': round(home_ppg, 3),
                'thesportsdb_away_ppg': round(away_ppg, 3),
                'thesportsdb_home_gf_pg': round(home_gf_pg, 3),
                'thesportsdb_home_ga_pg': round(home_ga_pg, 3),
                'thesportsdb_away_gf_pg': round(away_gf_pg, 3),
                'thesportsdb_away_ga_pg': round(away_ga_pg, 3),
                'thesportsdb_home_form_score': round(home_form_score, 3),
                'thesportsdb_away_form_score': round(away_form_score, 3),
                'home_form': round(home_form_score, 3),
                'away_form': round(away_form_score, 3),
                'home_ppg': round(home_ppg, 3),
                'thesportsdb_partial_context': partial,
                'away_ppg': round(away_ppg, 3),
                'home_gf_pg': round(home_gf_pg, 3),
                'home_ga_pg': round(home_ga_pg, 3),
                'away_gf_pg': round(away_gf_pg, 3),
                'away_ga_pg': round(away_ga_pg, 3),
                'thesportsdb_home_rank': self._to_float(home_row, 'intRank', 'rank', 'position'),
                'thesportsdb_away_rank': self._to_float(away_row, 'intRank', 'rank', 'position'),
                'home_rank': self._to_float(home_row, 'intRank', 'rank', 'position'),
                'away_rank': self._to_float(away_row, 'intRank', 'rank', 'position'),
            },
        )

    @staticmethod
    def _normalize_probs(home: float, away: float, draw: float) -> dict[str, float]:
        home_value = clamp(float(home or 0.0), 0.05, 0.90)
        away_value = clamp(float(away or 0.0), 0.05, 0.90)
        draw_value = clamp(float(draw or 0.0), 0.06, 0.35)
        total = home_value + away_value + draw_value
        if total <= 0:
            return {'home': 0.40, 'away': 0.32, 'draw': 0.28}
        return {'home': home_value / total, 'away': away_value / total, 'draw': draw_value / total}

    @staticmethod
    def _to_float(row: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = row.get(key)
            try:
                if value in (None, ''):
                    continue
                return float(str(value).replace(',', '.'))
            except Exception:
                continue
        return None

    @staticmethod
    def _normalize_league_key(value: str) -> str:
        key = canonicalize_league_name(value)
        for old, new in LEAGUE_ALIASES.items():
            if key == old:
                key = new
                break
        for token in (' group a', ' group b', ' group c', ' girone a', ' girone b', ' girone c'):
            if key.endswith(token):
                key = key[: -len(token)]
        key = key.replace(' italy ', ' italian ').replace(' england ', ' english ').replace(' spain ', ' spanish ').replace(' germany ', ' german ').replace(' france ', ' french ').replace(' netherlands ', ' dutch ').replace(' portugal ', ' portuguese ')
        key = ' '.join(key.split())
        return key
