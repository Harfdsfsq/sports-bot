from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
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
            'events_matched': 0,
            'contexts_built': 0,
            'standings_requests': 0,
            'standings_loaded': 0,
            'history_requests': 0,
            'history_rows_fetched': 0,
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
        match_limit = max(1, int(getattr(self.settings, 'football_data_match_limit', 48) or 48))
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
            comp_meta_by_key: dict[str, dict[str, Any]] = {}
            comp_codes: list[str] = []
            comp_codes_seen: set[str] = set()
            league_competitions: list[dict[str, Any]] = []
            league_seen: set[int | str] = set()
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
                comp = row.get('competition') if isinstance(row.get('competition'), dict) else {}
                code = str((comp or {}).get('code') or '').strip()
                comp_id = (comp or {}).get('id')
                comp_type = str((comp or {}).get('type') or '').strip().upper()
                key = code or str(comp_id or '')
                if key:
                    comp_meta_by_key[key] = {
                        'id': comp_id,
                        'code': code,
                        'type': comp_type,
                        'name': str((comp or {}).get('name') or ''),
                    }
                if code and code not in comp_codes_seen:
                    comp_codes_seen.add(code)
                    comp_codes.append(code)
                if comp_type == 'LEAGUE' and (comp_id or code):
                    league_key = comp_id or code
                    if league_key not in league_seen:
                        league_seen.add(league_key)
                        league_competitions.append({'id': comp_id, 'code': code, 'key': key})

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

            history_by_comp_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
            recent_days = max(5, int(getattr(self.settings, 'football_data_recent_days', 21) or 21))
            history_comp_limit = max(1, int(getattr(self.settings, 'football_data_history_competitions_limit', 12) or 12))
            history_codes = [code for code in comp_codes[:history_comp_limit] if code]
            if history_codes:
                history_payload = await self._fetch_json(
                    client,
                    '/matches',
                    stats,
                    params={
                        'dateFrom': (start - timedelta(days=recent_days)).isoformat(),
                        'dateTo': max(start - timedelta(days=1), start - timedelta(days=recent_days)).isoformat(),
                        'status': 'FINISHED',
                        'competitions': ','.join(history_codes),
                        'limit': 300,
                    },
                )
                stats['history_requests'] += 1
                history_rows = self._extract_rows(history_payload, 'matches')
                stats['history_rows_fetched'] = len(history_rows)
                if history_rows:
                    preview['sample_history'] = history_rows[:3]
                for history_row in history_rows:
                    comp = history_row.get('competition') if isinstance(history_row.get('competition'), dict) else {}
                    key = str((comp or {}).get('code') or (comp or {}).get('id') or '').strip()
                    if key:
                        history_by_comp_key[key].append(history_row)

            standings_limit = max(1, int(getattr(self.settings, 'football_data_standings_limit', 6) or 6))
            standings_by_code: dict[str, list[dict[str, Any]]] = {}
            for comp in league_competitions[:standings_limit]:
                comp_ref = comp.get('id') or comp.get('code')
                if not comp_ref:
                    continue
                standings_payload = await self._fetch_json(client, f'/competitions/{comp_ref}/standings', stats)
                stats['standings_requests'] += 1
                table = self._extract_standings_table(standings_payload)
                if table:
                    key = str(comp.get('code') or comp.get('key') or comp_ref)
                    standings_by_code[key] = table
                    stats['standings_loaded'] += 1
                    if len(preview['sample_tables']) < 4:
                        preview['sample_tables'].append({'competition': key, 'rows': table[:3]})

        contexts: dict[str, MatchContext] = {}
        for item in mapping.values():
            match = item['match']
            row = item['row']
            comp = row.get('competition') if isinstance(row.get('competition'), dict) else {}
            key = str((comp or {}).get('code') or (comp or {}).get('id') or '').strip()
            history_rows = history_by_comp_key.get(key) or []
            context = self._build_context(match, row, history_rows, standings_by_code.get(key) or [])
            if context is None:
                continue
            contexts[match.match_key] = context
            stats['contexts_built'] += 1
            if len(preview['sample_contexts']) < 8:
                preview['sample_contexts'].append({
                    'match_key': match.match_key,
                    'expected_home': context.expected_home,
                    'expected_away': context.expected_away,
                    'confidence': context.confidence,
                    'history_rows': len(history_rows),
                    'standings_rows': len(standings_by_code.get(key) or []),
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
            'commence_time': commence,
        }

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
                event_league=event.get('league'),
                exact_time_tolerance_hours=exact_tol,
                fuzzy_time_tolerance_hours=fuzzy_tol,
            )
            if score > best_score:
                best_match = match
                best_score = score
                best_quality = quality
        if best_score < 0.52:
            return None, 0.0, None
        return best_match, best_score, best_quality

    def _build_context(
        self,
        match: Match,
        row: dict[str, Any],
        history_rows: list[dict[str, Any]],
        standings_rows: list[dict[str, Any]],
    ) -> MatchContext | None:
        home_team = match.home_team
        away_team = match.away_team
        match_dt = match.commence_time.astimezone(UTC)

        home_games: list[tuple[datetime, float, float]] = []
        away_games: list[tuple[datetime, float, float]] = []
        h2h_goals: list[float] = []
        for history_row in history_rows:
            parsed = self._row_history_entry(history_row)
            if parsed is None:
                continue
            row_dt, t1, t2, g1, g2 = parsed
            if row_dt >= match_dt:
                continue
            home_sim = team_similarity(home_team, t1)
            away_sim = team_similarity(away_team, t2)
            rev_home = team_similarity(home_team, t2)
            rev_away = team_similarity(away_team, t1)
            if home_sim >= 0.84 and away_sim >= 0.84:
                home_games.append((row_dt, g1, g2))
                away_games.append((row_dt, g2, g1))
                h2h_goals.append(g1 + g2)
                continue
            if rev_home >= 0.84 and rev_away >= 0.84:
                home_games.append((row_dt, g2, g1))
                away_games.append((row_dt, g1, g2))
                h2h_goals.append(g1 + g2)
                continue
            if team_similarity(home_team, t1) >= 0.84:
                home_games.append((row_dt, g1, g2))
            if team_similarity(home_team, t2) >= 0.84:
                home_games.append((row_dt, g2, g1))
            if team_similarity(away_team, t1) >= 0.84:
                away_games.append((row_dt, g1, g2))
            if team_similarity(away_team, t2) >= 0.84:
                away_games.append((row_dt, g2, g1))

        home_games.sort(key=lambda item: item[0], reverse=True)
        away_games.sort(key=lambda item: item[0], reverse=True)
        home_recent = home_games[:5]
        away_recent = away_games[:5]
        home_row = self._find_team_row(home_team, standings_rows)
        away_row = self._find_team_row(away_team, standings_rows)

        if len(home_recent) < 2 and home_row is None:
            return None
        if len(away_recent) < 2 and away_row is None:
            return None

        home_metrics = self._combine_metrics(home_recent, home_row)
        away_metrics = self._combine_metrics(away_recent, away_row)
        if home_metrics is None or away_metrics is None:
            return None

        home_rank = int(round(float(home_metrics.get('rank') or 0))) if home_metrics.get('rank') else None
        away_rank = int(round(float(away_metrics.get('rank') or 0))) if away_metrics.get('rank') else None
        home_ppg = float(home_metrics['ppg'])
        away_ppg = float(away_metrics['ppg'])
        home_form = float(home_metrics['form_score'])
        away_form = float(away_metrics['form_score'])
        expected_home = clamp(((home_metrics['gf_pg'] + away_metrics['ga_pg']) / 2.0) + 0.16, 0.30, 3.40)
        expected_away = clamp(((away_metrics['gf_pg'] + home_metrics['ga_pg']) / 2.0), 0.25, 3.20)
        rank_delta = 0.0
        if home_rank and away_rank:
            rank_delta = clamp((away_rank - home_rank) * 0.010, -0.12, 0.12)
        delta = clamp((home_ppg - away_ppg) * 0.14 + rank_delta + (home_form - away_form) * 0.08, -0.22, 0.22)
        draw = clamp(0.25 - abs(delta) * 0.18, 0.16, 0.30)
        home = 0.39 + delta
        away = 1.0 - home - draw
        probs = self._normalize_probs(home, away, draw)
        confidence_base = 54.0
        confidence_base += min(float(home_metrics.get('played') or 0), 8.0) * 0.5
        confidence_base += min(float(away_metrics.get('played') or 0), 8.0) * 0.5
        if home_row is not None and away_row is not None:
            confidence_base += 3.5
        if h2h_goals:
            confidence_base += 1.0
        confidence = clamp(confidence_base, 55.0, 68.5)
        details = {
            'home_rank': home_rank,
            'away_rank': away_rank,
            'home_ppg': round(home_ppg, 3),
            'away_ppg': round(away_ppg, 3),
            'home_form': round(home_form, 3),
            'away_form': round(away_form, 3),
            'home_attack': round(clamp(home_metrics['gf_pg'] / 2.2, 0.0, 1.0), 3),
            'away_attack': round(clamp(away_metrics['gf_pg'] / 2.2, 0.0, 1.0), 3),
            'home_defense': round(clamp(1.0 - home_metrics['ga_pg'] / 2.2, 0.0, 1.0), 3),
            'away_defense': round(clamp(1.0 - away_metrics['ga_pg'] / 2.2, 0.0, 1.0), 3),
            'draw_probability': round(probs['draw'], 4),
            'football_data_home_rank': home_rank,
            'football_data_away_rank': away_rank,
            'football_data_home_ppg': round(home_ppg, 3),
            'football_data_away_ppg': round(away_ppg, 3),
            'football_data_home_form': round(home_form, 3),
            'football_data_away_form': round(away_form, 3),
            'football_data_competition': str(((row.get('competition') or {}).get('name')) or match.league_name),
            'football_data_history_matches_home': len(home_recent),
            'football_data_history_matches_away': len(away_recent),
            'football_data_h2h_avg_goals': round(sum(h2h_goals) / len(h2h_goals), 3) if h2h_goals else None,
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

    @staticmethod
    def _row_history_entry(row: dict[str, Any]) -> tuple[datetime, str, str, float, float] | None:
        home = str(((row.get('homeTeam') or {}).get('name')) or '').strip()
        away = str(((row.get('awayTeam') or {}).get('name')) or '').strip()
        if not home or not away:
            return None
        try:
            row_dt = parse_datetime(str(row.get('utcDate') or row.get('date') or ''))
        except Exception:
            return None
        score = row.get('score') if isinstance(row.get('score'), dict) else {}
        full_time = score.get('fullTime') if isinstance(score, dict) else {}
        try:
            home_goals = float((full_time or {}).get('home'))
            away_goals = float((full_time or {}).get('away'))
        except Exception:
            return None
        return row_dt, home, away, home_goals, away_goals

    def _combine_metrics(self, recent_games: list[tuple[datetime, float, float]], standings_row: dict[str, Any] | None) -> dict[str, float] | None:
        standings_metrics = self._row_metrics(standings_row) if standings_row is not None else None
        if not recent_games and standings_metrics is None:
            return None
        if recent_games:
            gf_pg = sum(item[1] for item in recent_games) / len(recent_games)
            ga_pg = sum(item[2] for item in recent_games) / len(recent_games)
            points = sum(3.0 if gf > ga else 1.0 if gf == ga else 0.0 for _, gf, ga in recent_games)
            recent_ppg = points / len(recent_games)
            recent_form = recent_ppg / 3.0
            recent_metrics = {
                'played': float(len(recent_games)),
                'gf_pg': gf_pg,
                'ga_pg': ga_pg,
                'ppg': recent_ppg,
                'form_score': recent_form,
                'rank': 0.0,
            }
        else:
            recent_metrics = None

        if standings_metrics is None:
            return recent_metrics
        if recent_metrics is None:
            return standings_metrics
        return {
            'played': max(recent_metrics['played'], standings_metrics['played']),
            'gf_pg': (recent_metrics['gf_pg'] * 0.62) + (standings_metrics['gf_pg'] * 0.38),
            'ga_pg': (recent_metrics['ga_pg'] * 0.62) + (standings_metrics['ga_pg'] * 0.38),
            'ppg': (recent_metrics['ppg'] * 0.60) + (standings_metrics['ppg'] * 0.40),
            'form_score': (recent_metrics['form_score'] * 0.65) + (standings_metrics['form_score'] * 0.35),
            'rank': standings_metrics.get('rank', 0.0),
        }
