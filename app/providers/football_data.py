from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import clamp, parse_datetime, score_event_match, team_similarity


class FootballDataContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = str(settings.football_data_base_url or 'https://api.football-data.org/v4').rstrip('/')
        self.timeout = float(settings.football_data_timeout_seconds or 20.0)

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            'enabled': bool(getattr(self.settings, 'enable_football_data_context', True)),
            'api_key_present': bool(getattr(self.settings, 'football_data_api_key', None)),
            'requests': 0,
            'response_errors': 0,
            'matches_fetched': 0,
            'history_rows_fetched': 0,
            'events_matched': 0,
            'contexts_built': 0,
            'standings_requests': 0,
            'standings_loaded': 0,
            'history_requests': 0,
            'history_contexts_built': 0,
            'matched_exact': 0,
            'matched_loose': 0,
            'matched_fuzzy': 0,
            'unmatched_rows': 0,
            'http_statuses': [],
            'last_body_preview': None,
        }
        preview: dict[str, Any] = {
            'sample_matches': [],
            'matched_examples': [],
            'sample_contexts': [],
            'sample_tables': [],
            'sample_history': [],
        }

        if not stats['enabled'] or not stats['api_key_present']:
            return {}, stats, preview

        soccer_matches = [item for item in matches if item.sport_key == 'soccer']
        if not soccer_matches:
            return {}, stats, preview
        match_limit = max(1, int(getattr(self.settings, 'football_data_match_limit', 36) or 36))
        soccer_matches = soccer_matches[:match_limit]

        start = min(item.commence_time for item in soccer_matches).astimezone(UTC).date()
        end = max(item.commence_time for item in soccer_matches).astimezone(UTC).date()
        days_ahead = max(1, int(getattr(self.settings, 'football_data_days_ahead', 2) or 2))
        end = max(end, start + timedelta(days=days_ahead))

        headers = {'X-Auth-Token': str(self.settings.football_data_api_key)}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            payload = await self._fetch_json(
                client,
                '/matches',
                stats,
                params={
                    'dateFrom': start.isoformat(),
                    'dateTo': end.isoformat(),
                    'status': 'SCHEDULED',
                    'limit': 200,
                },
            )
            rows = self._extract_rows(payload, 'matches')
            stats['matches_fetched'] = len(rows)
            if rows:
                preview['sample_matches'] = rows[:3]
            if not rows:
                return {}, stats, preview

            mapping: dict[str, dict[str, Any]] = {}
            competition_refs: list[tuple[int | str, int | None, str]] = []
            seen_competitions: set[tuple[int | str, int | None, str]] = set()
            for row in rows:
                event = self._row_to_event(row)
                if event is None:
                    stats['unmatched_rows'] += 1
                    continue
                matched, score, quality = self._match_event(event, soccer_matches)
                if matched is None:
                    stats['unmatched_rows'] += 1
                    continue
                existing = mapping.get(matched.match_key)
                if existing is None or score > float(existing['score']):
                    mapping[matched.match_key] = {'match': matched, 'row': row, 'score': score, 'quality': quality}
                comp_ref = (event.get('competition_id') or event.get('competition_code') or '', event.get('season_year'), event.get('competition_code') or '')
                if comp_ref[0] and comp_ref not in seen_competitions:
                    seen_competitions.add(comp_ref)
                    competition_refs.append(comp_ref)

            stats['events_matched'] = len(mapping)
            for payload_row in mapping.values():
                quality = payload_row.get('quality')
                if quality == 'exact':
                    stats['matched_exact'] += 1
                elif quality == 'loose':
                    stats['matched_loose'] += 1
                elif quality == 'fuzzy':
                    stats['matched_fuzzy'] += 1
                if len(preview['matched_examples']) < 8:
                    match = payload_row['match']
                    row = payload_row['row']
                    preview['matched_examples'].append({
                        'match_key': match.match_key,
                        'home_team': match.home_team,
                        'away_team': match.away_team,
                        'competition': ((row.get('competition') or {}).get('name')),
                        'quality': quality,
                        'score': round(float(payload_row.get('score') or 0.0), 2),
                    })

            standings_limit = max(1, int(getattr(self.settings, 'football_data_standings_limit', 6) or 6))
            history_limit = max(1, int(getattr(self.settings, 'football_data_history_limit', 4) or 4))
            standings_by_code: dict[str, list[dict[str, Any]]] = {}
            history_by_competition: dict[tuple[int | str, int | None], list[dict[str, Any]]] = {}

            for comp_ref in competition_refs[:max(standings_limit, history_limit)]:
                competition_id_or_code, season_year, competition_code = comp_ref
                if competition_code and len(standings_by_code) < standings_limit:
                    standings_payload = await self._fetch_json(client, f'/competitions/{competition_code}/standings', stats)
                    stats['standings_requests'] += 1
                    table = self._extract_standings_table(standings_payload)
                    if table:
                        standings_by_code[competition_code] = table
                        stats['standings_loaded'] += 1
                        if len(preview['sample_tables']) < 4:
                            preview['sample_tables'].append({'competition_code': competition_code, 'rows': table[:3]})

                history_key = (competition_id_or_code, season_year)
                if history_key not in history_by_competition and len(history_by_competition) < history_limit:
                    params = {'status': 'FINISHED'}
                    if season_year:
                        params['season'] = season_year
                    history_payload = await self._fetch_json(
                        client,
                        f'/competitions/{competition_id_or_code}/matches',
                        stats,
                        params=params,
                    )
                    stats['history_requests'] += 1
                    history_rows = self._extract_rows(history_payload, 'matches')
                    if history_rows:
                        history_by_competition[history_key] = history_rows
                        stats['history_rows_fetched'] += len(history_rows)
                        if len(preview['sample_history']) < 3:
                            preview['sample_history'].append({'competition': competition_id_or_code, 'rows': history_rows[:2]})

        contexts: dict[str, MatchContext] = {}
        for item in mapping.values():
            match = item['match']
            row = item['row']
            code = str(((row.get('competition') or {}).get('code')) or '').strip()
            competition_id = ((row.get('competition') or {}).get('id')) or code
            season_year = self._extract_season_year(row)
            history_rows = history_by_competition.get((competition_id, season_year), [])

            context = self._build_context(
                match=match,
                row=row,
                table=standings_by_code.get(code) or [],
                history_rows=history_rows,
            )
            if context is None:
                continue
            contexts[match.match_key] = context
            stats['contexts_built'] += 1
            if context.source == 'football_data_history':
                stats['history_contexts_built'] += 1
            if len(preview['sample_contexts']) < 8:
                preview['sample_contexts'].append({
                    'match_key': match.match_key,
                    'source': context.source,
                    'expected_home': context.expected_home,
                    'expected_away': context.expected_away,
                    'confidence': context.confidence,
                })

        return contexts, stats, preview

    async def _fetch_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        stats: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> Any | None:
        stats['requests'] += 1
        try:
            response = await client.get(f'{self.base_url}{path}', params=params)
        except Exception as exc:
            stats['response_errors'] += 1
            stats['last_body_preview'] = f'request failed: {exc}'
            return None
        stats['http_statuses'].append(response.status_code)
        stats['last_body_preview'] = response.text[:1800]
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

    @staticmethod
    def _extract_standings_table(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        standings = payload.get('standings')
        if not isinstance(standings, list):
            return []
        for block in standings:
            if not isinstance(block, dict):
                continue
            table = block.get('table')
            if isinstance(table, list) and table:
                return [row for row in table if isinstance(row, dict)]
        return []

    def _row_to_event(self, row: dict[str, Any]) -> dict[str, Any] | None:
        home = str(((row.get('homeTeam') or {}).get('name')) or '').strip()
        away = str(((row.get('awayTeam') or {}).get('name')) or '').strip()
        league = str(((row.get('competition') or {}).get('name')) or '').strip()
        code = str(((row.get('competition') or {}).get('code')) or '').strip()
        competition_id = (row.get('competition') or {}).get('id')
        if not home or not away:
            return None
        try:
            commence = parse_datetime(str(row.get('utcDate') or row.get('date') or ''))
        except Exception:
            return None
        return {
            'id': row.get('id'),
            'home': home,
            'away': away,
            'league': league,
            'competition_code': code,
            'competition_id': competition_id,
            'season_year': self._extract_season_year(row),
            'commence_time': commence,
        }

    @staticmethod
    def _extract_season_year(row: dict[str, Any]) -> int | None:
        season = row.get('season') or {}
        start_date = str(season.get('startDate') or '').strip()
        if len(start_date) >= 4 and start_date[:4].isdigit():
            return int(start_date[:4])
        try:
            value = season.get('year')
            return int(value) if value is not None else None
        except Exception:
            return None

    def _match_event(self, event: dict[str, Any], matches: list[Match]) -> tuple[Match | None, float, str | None]:
        best_match: Match | None = None
        best_score = 0.0
        best_quality: str | None = None
        exact_tol = float(getattr(self.settings, 'match_start_tolerance_hours', 12) or 12)
        fuzzy_tol = float(getattr(self.settings, 'fallback_match_start_tolerance_hours', 8) or 8)
        for match in matches:
            score, quality = score_event_match(
                sport=match.sport_key,
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home=event['home'],
                event_away=event['away'],
                event_start=event['commence_time'],
                event_league=event['league'],
                exact_tolerance_hours=exact_tol,
                fuzzy_tolerance_hours=fuzzy_tol,
            )
            if score > best_score:
                best_score = score
                best_quality = quality
                best_match = match
        if best_match is None or best_score < 46.0:
            return None, 0.0, None
        return best_match, best_score, best_quality

    def _build_context(
        self,
        match: Match,
        row: dict[str, Any],
        table: list[dict[str, Any]],
        history_rows: list[dict[str, Any]],
    ) -> MatchContext | None:
        home_row = self._find_team_row(match.home_team, table)
        away_row = self._find_team_row(match.away_team, table)
        if home_row is not None and away_row is not None:
            context = self._build_table_context(match, row, home_row, away_row)
            if context is not None:
                return context
        return self._build_history_context(match, row, history_rows)

    def _build_table_context(self, match: Match, row: dict[str, Any], home_row: dict[str, Any], away_row: dict[str, Any]) -> MatchContext | None:
        home_stats = self._row_metrics(home_row)
        away_stats = self._row_metrics(away_row)
        home_ppg = home_stats['ppg']
        away_ppg = away_stats['ppg']
        home_rank = int(home_stats['rank'] or 0)
        away_rank = int(away_stats['rank'] or 0)
        home_form = home_stats['form_score']
        away_form = away_stats['form_score']
        expected_home = clamp(((home_stats['gf_pg'] + away_stats['ga_pg']) / 2.0) + 0.16, 0.30, 3.40)
        expected_away = clamp(((away_stats['gf_pg'] + home_stats['ga_pg']) / 2.0), 0.25, 3.20)
        delta = clamp((home_ppg - away_ppg) * 0.14 + (away_rank - home_rank) * 0.010 + (home_form - away_form) * 0.06, -0.20, 0.20)
        draw = clamp(0.25 - abs(delta) * 0.18, 0.16, 0.30)
        home = 0.39 + delta
        away = 1.0 - home - draw
        probs = self._normalize_probs(home, away, draw)
        confidence = clamp(58.0 + min(home_stats['played'], away_stats['played']) * 0.35, 56.0, 67.0)
        details = {
            'home_rank': home_rank,
            'away_rank': away_rank,
            'home_ppg': round(home_ppg, 3),
            'away_ppg': round(away_ppg, 3),
            'home_form': round(home_form, 3),
            'away_form': round(away_form, 3),
            'home_attack': round(clamp(home_stats['gf_pg'] / 2.2, 0.0, 1.0), 3),
            'away_attack': round(clamp(away_stats['gf_pg'] / 2.2, 0.0, 1.0), 3),
            'home_defense': round(clamp(1.0 - home_stats['ga_pg'] / 2.2, 0.0, 1.0), 3),
            'away_defense': round(clamp(1.0 - away_stats['ga_pg'] / 2.2, 0.0, 1.0), 3),
            'draw_probability': round(probs['draw'], 4),
            'football_data_home_rank': home_rank,
            'football_data_away_rank': away_rank,
            'football_data_home_ppg': round(home_ppg, 3),
            'football_data_away_ppg': round(away_ppg, 3),
            'football_data_home_form': round(home_form, 3),
            'football_data_away_form': round(away_form, 3),
            'football_data_competition': str(((row.get('competition') or {}).get('name')) or match.league_name),
            'football_data_context_mode': 'standings',
        }
        return MatchContext(
            source='football_data',
            payload=row,
            expected_home=round(expected_home, 3),
            expected_away=round(expected_away, 3),
            home_win_probability=round(probs['home'], 4),
            away_win_probability=round(probs['away'], 4),
            confidence=float(round(confidence, 2)),
            details=details,
        )

    def _build_history_context(self, match: Match, row: dict[str, Any], history_rows: list[dict[str, Any]]) -> MatchContext | None:
        if not history_rows:
            return None
        cutoff = match.commence_time.astimezone(UTC)
        home_team_id = (row.get('homeTeam') or {}).get('id')
        away_team_id = (row.get('awayTeam') or {}).get('id')
        home_metrics = self._history_metrics(match.home_team, home_team_id, history_rows, cutoff)
        away_metrics = self._history_metrics(match.away_team, away_team_id, history_rows, cutoff)
        if home_metrics is None or away_metrics is None:
            return None
        min_played = min(home_metrics['played'], away_metrics['played'])
        if min_played < 2:
            return None

        expected_home = clamp(((home_metrics['gf_pg'] + away_metrics['ga_pg']) / 2.0) + 0.14, 0.35, 3.60)
        expected_away = clamp(((away_metrics['gf_pg'] + home_metrics['ga_pg']) / 2.0), 0.30, 3.40)
        delta = clamp(
            (home_metrics['ppg'] - away_metrics['ppg']) * 0.16
            + (home_metrics['form_score'] - away_metrics['form_score']) * 0.12,
            -0.22,
            0.22,
        )
        draw = clamp(0.24 - abs(delta) * 0.16, 0.16, 0.30)
        home = 0.39 + delta
        away = 1.0 - home - draw
        probs = self._normalize_probs(home, away, draw)
        confidence = clamp(54.0 + min_played * 1.8, 55.0, 66.0)
        details = {
            'football_data_competition': str(((row.get('competition') or {}).get('name')) or match.league_name),
            'football_data_context_mode': 'history',
            'football_data_home_history_played': home_metrics['played'],
            'football_data_away_history_played': away_metrics['played'],
            'football_data_home_ppg': round(home_metrics['ppg'], 3),
            'football_data_away_ppg': round(away_metrics['ppg'], 3),
            'football_data_home_form': round(home_metrics['form_score'], 3),
            'football_data_away_form': round(away_metrics['form_score'], 3),
            'football_data_home_gf_pg': round(home_metrics['gf_pg'], 3),
            'football_data_home_ga_pg': round(home_metrics['ga_pg'], 3),
            'football_data_away_gf_pg': round(away_metrics['gf_pg'], 3),
            'football_data_away_ga_pg': round(away_metrics['ga_pg'], 3),
            'draw_probability': round(probs['draw'], 4),
        }
        return MatchContext(
            source='football_data_history',
            payload=row,
            expected_home=round(expected_home, 3),
            expected_away=round(expected_away, 3),
            home_win_probability=round(probs['home'], 4),
            away_win_probability=round(probs['away'], 4),
            confidence=float(round(confidence, 2)),
            details=details,
        )

    @staticmethod
    def _normalize_probs(home: float, away: float, draw: float) -> dict[str, float]:
        home = clamp(float(home), 0.05, 0.90)
        away = clamp(float(away), 0.05, 0.90)
        draw = clamp(float(draw), 0.05, 0.40)
        total = home + away + draw
        return {'home': home / total, 'away': away / total, 'draw': draw / total}

    @staticmethod
    def _row_metrics(row: dict[str, Any]) -> dict[str, float]:
        def f(key: str) -> float:
            try:
                return float(row.get(key) or 0.0)
            except Exception:
                return 0.0

        played = max(f('playedGames'), 1.0)
        wins = f('won')
        draws = f('draw')
        gf = f('goalsFor')
        ga = f('goalsAgainst')
        rank = f('position')
        form_text = str(row.get('form') or '').upper()
        if form_text:
            values = [1.0 if ch == 'W' else 0.5 if ch == 'D' else 0.0 for ch in form_text if ch in {'W', 'D', 'L'}]
            form_score = sum(values) / len(values) if values else 0.5
        else:
            form_score = clamp(((wins * 3.0 + draws) / (played * 3.0)), 0.0, 1.0)
        return {
            'played': played,
            'rank': rank,
            'ppg': (f('points') / played) if row.get('points') is not None else ((wins * 3.0 + draws) / played),
            'gf_pg': gf / played,
            'ga_pg': ga / played,
            'form_score': form_score,
        }

    def _history_metrics(
        self,
        team_name: str,
        team_id: int | None,
        history_rows: list[dict[str, Any]],
        cutoff,
    ) -> dict[str, float] | None:
        selected: list[dict[str, Any]] = []
        for row in history_rows:
            try:
                match_time = parse_datetime(str(row.get('utcDate') or row.get('date') or ''))
            except Exception:
                continue
            if match_time.astimezone(UTC) >= cutoff:
                continue
            status = str(row.get('status') or '').upper()
            if status != 'FINISHED':
                continue
            home_team = row.get('homeTeam') or {}
            away_team = row.get('awayTeam') or {}
            home_id = home_team.get('id')
            away_id = away_team.get('id')
            home_name = str(home_team.get('name') or '').strip()
            away_name = str(away_team.get('name') or '').strip()
            matched = False
            is_home = False
            if team_id is not None:
                if team_id == home_id:
                    matched = True
                    is_home = True
                elif team_id == away_id:
                    matched = True
            if not matched:
                if team_similarity(team_name, home_name) >= 0.76:
                    matched = True
                    is_home = True
                elif team_similarity(team_name, away_name) >= 0.76:
                    matched = True
            if not matched:
                continue
            selected.append({'row': row, 'time': match_time.astimezone(UTC), 'is_home': is_home})

        if not selected:
            return None
        selected.sort(key=lambda item: item['time'], reverse=True)
        sample = selected[:6]
        goals_for = 0.0
        goals_against = 0.0
        points = 0.0
        form_values: list[float] = []
        played = 0
        for item in sample:
            row = item['row']
            score = row.get('score') or {}
            full_time = score.get('fullTime') or {}
            home_goals = full_time.get('home')
            away_goals = full_time.get('away')
            if home_goals is None or away_goals is None:
                continue
            try:
                hg = float(home_goals)
                ag = float(away_goals)
            except Exception:
                continue
            is_home = item['is_home']
            gf = hg if is_home else ag
            ga = ag if is_home else hg
            goals_for += gf
            goals_against += ga
            played += 1
            if gf > ga:
                points += 3.0
                form_values.append(1.0)
            elif gf == ga:
                points += 1.0
                form_values.append(0.5)
            else:
                form_values.append(0.0)
        if played == 0:
            return None
        form_score = sum(form_values) / len(form_values) if form_values else 0.5
        return {
            'played': float(played),
            'ppg': points / played,
            'gf_pg': goals_for / played,
            'ga_pg': goals_against / played,
            'form_score': form_score,
        }

    def _find_team_row(self, team_name: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        best_row: dict[str, Any] | None = None
        best_score = 0.0
        for row in rows:
            row_name = str(((row.get('team') or {}).get('name')) or row.get('name') or '').strip()
            if not row_name:
                continue
            score = team_similarity(team_name, row_name)
            if score > best_score:
                best_score = score
                best_row = row
        return best_row if best_score >= 0.72 else None
