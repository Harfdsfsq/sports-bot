from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
UTC = timezone.utc
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import canonicalize_team_name, clamp, parse_datetime, soft_contains_team, team_similarity


class OpenLigaDbContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = str(settings.openligadb_base_url or 'https://api.openligadb.de').rstrip('/')
        self.timeout = float(settings.openligadb_timeout_seconds or 12.0)
        self.league_map = self._parse_competition_map(getattr(settings, 'openligadb_competition_map', []) or [])
        self.alias_map = {
            'germany bundesliga': 'bl1',
            'german bundesliga': 'bl1',
            '1 bundesliga': 'bl1',
            '1. bundesliga': 'bl1',
            'germany bundesliga 2': 'bl2',
            'german bundesliga 2': 'bl2',
            '2 bundesliga': 'bl2',
            '2. bundesliga': 'bl2',
            'germany 3 liga': 'bl3',
            'german 3 liga': 'bl3',
            '3 liga': 'bl3',
            '3. liga': 'bl3',
            'uefa champions league': 'ucl',
            'champions league': 'ucl',
            'dfb pokal': 'dfb',
        }

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            'enabled': bool(getattr(self.settings, 'enable_openligadb_context', True)),
            'requests': 0,
            'response_errors': 0,
            'datasets_loaded': 0,
            'tables_loaded': 0,
            'contexts_built': 0,
            'history_contexts_built': 0,
            'standings_contexts_built': 0,
            'matched_exact': 0,
            'matched_loose': 0,
            'matched_fuzzy': 0,
            'http_statuses': [],
            'last_body_preview': None,
        }
        preview: dict[str, Any] = {'sample_datasets': [], 'sample_tables': [], 'sample_contexts': []}
        if not stats['enabled']:
            return {}, stats, preview

        soccer_matches = [item for item in matches if item.sport_key == 'soccer']
        if not soccer_matches:
            return {}, stats, preview

        match_limit = max(1, int(getattr(self.settings, 'openligadb_match_limit', 24) or 24))
        soccer_matches = soccer_matches[:match_limit]
        grouped: dict[str, list[Match]] = defaultdict(list)
        for match in soccer_matches:
            comp_key = self._competition_key(match.league_name)
            if comp_key:
                grouped[comp_key].append(match)

        datasets: dict[tuple[str, int], dict[str, Any]] = {}
        dataset_limit = max(1, int(getattr(self.settings, 'openligadb_dataset_limit', 8) or 8))
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for comp_key, match_group in list(grouped.items())[:dataset_limit]:
                data_rows: list[dict[str, Any]] | None = None
                table_rows: list[dict[str, Any]] = []
                used_season: int | None = None
                for season in self._season_candidates(match_group[0].commence_time.astimezone(UTC)):
                    match_payload = await self._fetch_json(client, f'/getmatchdata/{comp_key}/{season}', stats)
                    rows = [row for row in (match_payload or []) if isinstance(row, dict)] if isinstance(match_payload, list) else []
                    if not rows:
                        continue
                    table_payload = await self._fetch_json(client, f'/getbltable/{comp_key}/{season}', stats, soft_fail=True)
                    table_rows = [row for row in (table_payload or []) if isinstance(row, dict)] if isinstance(table_payload, list) else []
                    data_rows = rows
                    used_season = season
                    break
                if not data_rows or used_season is None:
                    continue
                datasets[(comp_key, used_season)] = {'matches': data_rows, 'table': table_rows}
                stats['datasets_loaded'] += 1
                if table_rows:
                    stats['tables_loaded'] += 1
                if len(preview['sample_datasets']) < 4:
                    preview['sample_datasets'].append({'competition_key': comp_key, 'season': used_season, 'matches': data_rows[:2]})
                if table_rows and len(preview['sample_tables']) < 3:
                    preview['sample_tables'].append({'competition_key': comp_key, 'season': used_season, 'rows': table_rows[:3]})

        contexts: dict[str, MatchContext] = {}
        for (comp_key, season), payload in datasets.items():
            rows = list(payload.get('matches') or [])
            table_rows = list(payload.get('table') or [])
            target_matches = [m for m in soccer_matches if self._competition_key(m.league_name) == comp_key]
            for match in target_matches:
                context, quality, mode = self._build_context(match, rows, table_rows, comp_key=comp_key, season=season)
                if context is None:
                    continue
                contexts[match.match_key] = context
                stats['contexts_built'] += 1
                if mode in {'history', 'blended'}:
                    stats['history_contexts_built'] += 1
                if mode in {'standings', 'blended'}:
                    stats['standings_contexts_built'] += 1
                if quality == 'exact':
                    stats['matched_exact'] += 1
                elif quality == 'loose':
                    stats['matched_loose'] += 1
                elif quality == 'fuzzy':
                    stats['matched_fuzzy'] += 1
                if len(preview['sample_contexts']) < 8:
                    preview['sample_contexts'].append({
                        'match_key': match.match_key,
                        'competition_key': comp_key,
                        'season': season,
                        'mode': mode,
                        'expected_home': context.expected_home,
                        'expected_away': context.expected_away,
                        'confidence': context.confidence,
                    })
        return contexts, stats, preview

    def supports_match(self, match: Match) -> bool:
        return str(getattr(match, 'sport_key', '') or '') == 'soccer' and bool(self._competition_key(match.league_name))

    async def _fetch_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        stats: dict[str, Any],
        *,
        soft_fail: bool = False,
    ) -> Any | None:
        stats['requests'] += 1
        try:
            response = await client.get(f'{self.base_url}{path}')
        except Exception as exc:
            stats['response_errors'] += 1
            stats['last_body_preview'] = f'request failed: {exc}'
            return None
        stats['http_statuses'].append(response.status_code)
        stats['last_body_preview'] = response.text[:1200]
        if response.status_code != 200:
            if not soft_fail:
                stats['response_errors'] += 1
            return None
        try:
            return response.json()
        except Exception:
            stats['response_errors'] += 1
            return None

    @staticmethod
    def _parse_competition_map(items: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in items:
            text = str(item or '').strip()
            if not text or '=' not in text:
                continue
            left, right = text.split('=', 1)
            result[left.strip().lower()] = right.strip().lower()
        return result

    def _competition_key(self, league_name: str) -> str | None:
        raw = self._normalize_league_name(league_name)
        if not raw:
            return None
        for source in (raw,):
            if source in self.league_map:
                return self.league_map[source]
            if source in self.alias_map:
                return self.alias_map[source]
        for source in (raw,):
            for left, right in {**self.league_map, **self.alias_map}.items():
                if left in source or source in left:
                    return right
        return None

    @staticmethod
    def _normalize_league_name(name: str) -> str:
        text = ' '.join(str(name or '').lower().replace('_', ' ').replace('/', ' ').replace('.', ' ').split())
        for ch in ',:;()[]{}':
            text = text.replace(ch, ' ')
        return ' '.join(text.split())

    @staticmethod
    def _season_candidates(dt: datetime) -> list[int]:
        year = dt.year
        if dt.month >= 7:
            values = [year, year + 1, year - 1]
        else:
            values = [year - 1, year, year - 2]
        seen: list[int] = []
        for value in values:
            if value > 2000 and value not in seen:
                seen.append(value)
        return seen

    @staticmethod
    def _team_aliases(payload: dict[str, Any]) -> list[str]:
        aliases: list[str] = []
        for value in (
            str(payload.get('teamName') or '').strip(),
            str(payload.get('shortName') or '').strip(),
            str(payload.get('teamGroupName') or '').strip(),
        ):
            if value and value not in aliases:
                aliases.append(value)
        return aliases

    @staticmethod
    def _team_match_score(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        if soft_contains_team(a, b):
            return 1.0
        ca = canonicalize_team_name(a)
        cb = canonicalize_team_name(b)
        if not ca or not cb:
            return 0.0
        if ca == cb:
            return 1.0
        if ca.startswith(cb) or cb.startswith(ca):
            return 0.95
        return team_similarity(a, b)

    def _build_context(
        self,
        match: Match,
        rows: list[dict[str, Any]],
        table_rows: list[dict[str, Any]],
        *,
        comp_key: str,
        season: int,
    ) -> tuple[MatchContext | None, str | None, str | None]:
        history_payload, quality = self._history_metrics(match, rows)
        standings_payload = self._standings_metrics(match, table_rows)
        if history_payload is None and standings_payload is None:
            return None, None, None

        expected_home_values: list[tuple[float, float]] = []
        expected_away_values: list[tuple[float, float]] = []
        home_prob_values: list[tuple[float, float]] = []
        away_prob_values: list[tuple[float, float]] = []
        detail_mode = 'history'
        confidence = 52.0
        details: dict[str, Any] = {
            'openligadb_competition_key': comp_key,
            'openligadb_season': season,
        }
        payload: dict[str, Any] = {}

        if history_payload is not None:
            expected_home_values.append((float(history_payload['expected_home']), 0.68))
            expected_away_values.append((float(history_payload['expected_away']), 0.68))
            home_prob_values.append((float(history_payload['home_prob']), 0.68))
            away_prob_values.append((float(history_payload['away_prob']), 0.68))
            confidence = max(confidence, float(history_payload['confidence']))
            payload['history'] = history_payload['payload']
            details.update(history_payload['details'])
        if standings_payload is not None:
            expected_home_values.append((float(standings_payload['expected_home']), 0.32 if history_payload is not None else 0.62))
            expected_away_values.append((float(standings_payload['expected_away']), 0.32 if history_payload is not None else 0.62))
            home_prob_values.append((float(standings_payload['home_prob']), 0.32 if history_payload is not None else 0.62))
            away_prob_values.append((float(standings_payload['away_prob']), 0.32 if history_payload is not None else 0.62))
            confidence = max(confidence, float(standings_payload['confidence']))
            payload['table'] = standings_payload['payload']
            details.update(standings_payload['details'])
            detail_mode = 'blended' if history_payload is not None else 'standings'

        expected_home = self._weighted_average(expected_home_values)
        expected_away = self._weighted_average(expected_away_values)
        home_prob = self._weighted_average(home_prob_values)
        away_prob = self._weighted_average(away_prob_values)
        details['openligadb_context_mode'] = detail_mode

        return MatchContext(
            source='openligadb',
            payload=payload,
            expected_home=round(expected_home, 3) if expected_home is not None else None,
            expected_away=round(expected_away, 3) if expected_away is not None else None,
            home_win_probability=round(home_prob, 4) if home_prob is not None else None,
            away_win_probability=round(away_prob, 4) if away_prob is not None else None,
            confidence=float(round(clamp(confidence, 52.0, 69.0), 2)),
            details=details,
        ), quality, detail_mode

    def _history_metrics(self, match: Match, rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
        match_dt = match.commence_time.astimezone(UTC)
        home_games: list[tuple[datetime, float, float]] = []
        away_games: list[tuple[datetime, float, float]] = []
        h2h_goals: list[float] = []
        best_quality: str | None = None
        for row in rows:
            row_dt = parse_datetime(str(row.get('matchDateTimeUTC') or row.get('matchDateTime') or ''))
            if row_dt is None or row_dt >= match_dt:
                continue
            team1 = row.get('team1') or {}
            team2 = row.get('team2') or {}
            team1_aliases = self._team_aliases(team1 if isinstance(team1, dict) else {})
            team2_aliases = self._team_aliases(team2 if isinstance(team2, dict) else {})
            score = self._extract_final_score(row)
            if score is None or not team1_aliases or not team2_aliases:
                continue
            goals1, goals2 = score
            home_sim = max(self._team_match_score(match.home_team, alias) for alias in team1_aliases)
            away_sim = max(self._team_match_score(match.away_team, alias) for alias in team2_aliases)
            rev_home = max(self._team_match_score(match.home_team, alias) for alias in team2_aliases)
            rev_away = max(self._team_match_score(match.away_team, alias) for alias in team1_aliases)
            if home_sim >= 0.78 and away_sim >= 0.78:
                best_quality = best_quality or 'exact'
                home_games.append((row_dt, goals1, goals2))
                away_games.append((row_dt, goals2, goals1))
                h2h_goals.append(goals1 + goals2)
                continue
            if rev_home >= 0.78 and rev_away >= 0.78:
                best_quality = best_quality or 'loose'
                home_games.append((row_dt, goals2, goals1))
                away_games.append((row_dt, goals1, goals2))
                h2h_goals.append(goals1 + goals2)
                continue
            if home_sim >= 0.72:
                best_quality = best_quality or 'fuzzy'
                home_games.append((row_dt, goals1, goals2))
            elif rev_home >= 0.72:
                best_quality = best_quality or 'fuzzy'
                home_games.append((row_dt, goals2, goals1))
            if away_sim >= 0.72:
                best_quality = best_quality or 'fuzzy'
                away_games.append((row_dt, goals2, goals1))
            elif rev_away >= 0.72:
                best_quality = best_quality or 'fuzzy'
                away_games.append((row_dt, goals1, goals2))

        home_games.sort(key=lambda item: item[0], reverse=True)
        away_games.sort(key=lambda item: item[0], reverse=True)
        if len(home_games) < 2 or len(away_games) < 2:
            return None, best_quality

        home_recent = home_games[:5]
        away_recent = away_games[:5]
        home_gf = sum(item[1] for item in home_recent) / len(home_recent)
        home_ga = sum(item[2] for item in home_recent) / len(home_recent)
        away_gf = sum(item[1] for item in away_recent) / len(away_recent)
        away_ga = sum(item[2] for item in away_recent) / len(away_recent)
        home_ppg = self._ppg(home_recent)
        away_ppg = self._ppg(away_recent)
        expected_home = clamp(((home_gf + away_ga) / 2.0) + 0.12, 0.32, 3.45)
        expected_away = clamp(((away_gf + home_ga) / 2.0), 0.25, 3.25)
        delta = clamp((home_ppg - away_ppg) * 0.11, -0.18, 0.18)
        draw = clamp(0.26 - abs(delta) * 0.14, 0.16, 0.31)
        home_prob = 0.38 + delta
        away_prob = 1.0 - home_prob - draw
        total = home_prob + away_prob + draw
        home_prob /= total
        away_prob /= total
        confidence = clamp(53.0 + min(len(home_recent), len(away_recent)) * 2.1 + (2.0 if h2h_goals else 0.0), 53.0, 66.0)
        return {
            'expected_home': expected_home,
            'expected_away': expected_away,
            'home_prob': home_prob,
            'away_prob': away_prob,
            'confidence': confidence,
            'payload': {'recent_home': home_recent, 'recent_away': away_recent},
            'details': {
                'openligadb_home_ppg': round(home_ppg, 3),
                'openligadb_away_ppg': round(away_ppg, 3),
                'openligadb_history_home_sample': len(home_recent),
                'openligadb_history_away_sample': len(away_recent),
                'openligadb_h2h_avg_goals': round(sum(h2h_goals) / len(h2h_goals), 3) if h2h_goals else None,
                'openligadb_draw_probability': round(draw, 4),
            },
        }, best_quality

    def _standings_metrics(self, match: Match, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not rows:
            return None
        home_row = self._find_table_row(match.home_team, rows)
        away_row = self._find_table_row(match.away_team, rows)
        if home_row is None or away_row is None:
            return None
        home_matches = max(float(home_row.get('matches') or 0.0), 1.0)
        away_matches = max(float(away_row.get('matches') or 0.0), 1.0)
        home_gf = float(home_row.get('goals') or 0.0) / home_matches
        home_ga = float(home_row.get('opponentGoals') or 0.0) / home_matches
        away_gf = float(away_row.get('goals') or 0.0) / away_matches
        away_ga = float(away_row.get('opponentGoals') or 0.0) / away_matches
        home_ppg = float(home_row.get('points') or 0.0) / home_matches
        away_ppg = float(away_row.get('points') or 0.0) / away_matches
        expected_home = clamp(((home_gf + away_ga) / 2.0) + 0.15, 0.35, 3.5)
        expected_away = clamp(((away_gf + home_ga) / 2.0), 0.22, 3.2)
        delta = clamp((home_ppg - away_ppg) * 0.10, -0.16, 0.16)
        draw = clamp(0.25 - abs(delta) * 0.14, 0.17, 0.30)
        home_prob = 0.39 + delta
        away_prob = 1.0 - home_prob - draw
        total = home_prob + away_prob + draw
        home_prob /= total
        away_prob /= total
        confidence = clamp(55.0 + min(home_matches, away_matches) * 0.35, 55.0, 63.0)
        return {
            'expected_home': expected_home,
            'expected_away': expected_away,
            'home_prob': home_prob,
            'away_prob': away_prob,
            'confidence': confidence,
            'payload': {'home_row': home_row, 'away_row': away_row},
            'details': {
                'openligadb_table_home_ppg': round(home_ppg, 3),
                'openligadb_table_away_ppg': round(away_ppg, 3),
                'openligadb_table_home_matches': int(home_matches),
                'openligadb_table_away_matches': int(away_matches),
            },
        }

    def _find_table_row(self, team_name: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        best_row: dict[str, Any] | None = None
        best_score = 0.0
        for row in rows:
            aliases = []
            for value in (str(row.get('teamName') or '').strip(), str(row.get('shortName') or '').strip()):
                if value and value not in aliases:
                    aliases.append(value)
            if not aliases:
                continue
            score = max(self._team_match_score(team_name, alias) for alias in aliases)
            if score > best_score:
                best_score = score
                best_row = row
        return best_row if best_score >= 0.7 else None

    @staticmethod
    def _extract_final_score(row: dict[str, Any]) -> tuple[float, float] | None:
        results = row.get('matchResults')
        if not isinstance(results, list):
            return None
        best: dict[str, Any] | None = None
        best_rank = -1
        for item in results:
            if not isinstance(item, dict):
                continue
            result_type = int(item.get('resultTypeID') or 0)
            result_order = int(item.get('resultOrderID') or 0)
            rank = 100 if result_type == 2 else result_order
            if rank > best_rank:
                best_rank = rank
                best = item
        if best is None:
            return None
        try:
            return float(best.get('pointsTeam1')), float(best.get('pointsTeam2'))
        except Exception:
            return None

    @staticmethod
    def _ppg(rows: list[tuple[datetime, float, float]]) -> float:
        if not rows:
            return 0.0
        points = 0.0
        for _, gf, ga in rows:
            if gf > ga:
                points += 3.0
            elif gf == ga:
                points += 1.0
        return points / len(rows)

    @staticmethod
    def _weighted_average(values: list[tuple[float, float]]) -> float | None:
        clean = [(float(value), float(weight)) for value, weight in values if value is not None and weight > 0]
        if not clean:
            return None
        total_weight = sum(weight for _, weight in clean)
        if total_weight <= 0:
            return None
        return sum(value * weight for value, weight in clean) / total_weight
