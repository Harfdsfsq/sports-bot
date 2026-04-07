from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import canonicalize_team_name, clamp, parse_datetime, score_event_match, soft_contains_team, team_similarity


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
            'sample_history': [],
            'matched_examples': [],
            'sample_contexts': [],
            'sample_tables': [],
        }

        if not stats['enabled'] or not stats['api_key_present']:
            return {}, stats, preview

        soccer_matches = [item for item in matches if item.sport_key == 'soccer']
        if not soccer_matches:
            return {}, stats, preview
        match_limit = max(1, int(getattr(self.settings, 'football_data_match_limit', 36) or 36))
        soccer_matches = soccer_matches[:match_limit]

        start_date = min(item.commence_time for item in soccer_matches).astimezone(UTC).date()
        end_date = max(item.commence_time for item in soccer_matches).astimezone(UTC).date()
        days_ahead = max(1, int(getattr(self.settings, 'football_data_days_ahead', 2) or 2))
        end_date = max(end_date, start_date + timedelta(days=days_ahead))

        headers = {'X-Auth-Token': str(self.settings.football_data_api_key)}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            payload = await self._fetch_json(
                client,
                '/matches',
                stats,
                params={
                    'dateFrom': start_date.isoformat(),
                    'dateTo': end_date.isoformat(),
                    'status': 'SCHEDULED,TIMED',
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
            competition_types: dict[str, str] = {}
            competition_seasons: dict[str, int] = {}
            competition_counts: dict[str, int] = defaultdict(int)
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
                comp_ref = str(event.get('competition_ref') or '').strip().upper()
                if comp_ref:
                    competition_counts[comp_ref] += 1
                    if event.get('competition_type'):
                        competition_types[comp_ref] = str(event.get('competition_type') or '').strip().upper()
                    season_year = event.get('season_start_year')
                    if isinstance(season_year, int) and season_year > 0:
                        competition_seasons[comp_ref] = season_year

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

            standings_limit = max(1, int(getattr(self.settings, 'football_data_standings_limit', 4) or 4))
            history_limit = max(1, int(getattr(self.settings, 'football_data_history_competitions_limit', 4) or 4))
            competition_match_limit = max(50, int(getattr(self.settings, 'football_data_competition_match_limit', 120) or 120))

            standings_by_ref: dict[str, list[dict[str, Any]]] = {}
            league_refs = [
                ref
                for ref, _ in sorted(competition_counts.items(), key=lambda item: (-item[1], item[0]))
                if competition_types.get(ref) in {'LEAGUE', 'LEAGUE_CUP'}
            ]
            for ref in league_refs[:standings_limit]:
                standings_payload = await self._fetch_json(
                    client,
                    f'/competitions/{ref}/standings',
                    stats,
                    soft_fail_statuses={400, 403, 404},
                )
                stats['standings_requests'] += 1
                table = self._extract_standings_table(standings_payload)
                if table:
                    standings_by_ref[ref] = table
                    stats['standings_loaded'] += 1
                    if len(preview['sample_tables']) < 4:
                        preview['sample_tables'].append({'competition_ref': ref, 'rows': table[:3]})

            history_by_ref: dict[str, list[dict[str, Any]]] = defaultdict(list)
            history_refs = [
                ref
                for ref, _ in sorted(competition_counts.items(), key=lambda item: (-item[1], item[0]))
                if ref not in standings_by_ref
            ]
            for ref in history_refs[:history_limit]:
                params: dict[str, Any] = {'limit': competition_match_limit}
                season_year = competition_seasons.get(ref)
                if season_year:
                    params['season'] = season_year
                history_payload = await self._fetch_json(
                    client,
                    f'/competitions/{ref}/matches',
                    stats,
                    params=params,
                    soft_fail_statuses={400, 403, 404},
                )
                stats['history_requests'] += 1
                history_rows = self._extract_rows(history_payload, 'matches')
                if history_rows:
                    history_by_ref[ref].extend(history_rows)
                    stats['history_rows_fetched'] += len(history_rows)
                    if len(preview['sample_history']) < 3:
                        preview['sample_history'].extend(history_rows[: max(0, 3 - len(preview['sample_history']))])

        contexts: dict[str, MatchContext] = {}
        for item in mapping.values():
            match = item['match']
            row = item['row']
            comp = row.get('competition') or {}
            comp_ref = str((comp.get('code') or comp.get('id') or '')).strip().upper()
            table = standings_by_ref.get(comp_ref) or []
            context = self._build_context_from_standings(match, row, table) if table else None
            if context is None:
                history_rows = history_by_ref.get(comp_ref) or []
                if history_rows:
                    context = self._build_context_from_history(match, row, history_rows)
                    if context is not None:
                        stats['history_contexts_built'] += 1
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
                    'source': context.source,
                })

        return contexts, stats, preview

    async def _fetch_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        stats: dict[str, Any],
        params: dict[str, Any] | None = None,
        soft_fail_statuses: set[int] | None = None,
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
            if soft_fail_statuses and response.status_code in soft_fail_statuses:
                return None
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
        if not home or not away:
            return None
        dt = parse_datetime(str(row.get('utcDate') or ''))
        if dt is None:
            return None
        competition = row.get('competition') or {}
        season = row.get('season') or {}
        season_start = str(season.get('startDate') or '').strip()
        season_start_year: int | None = None
        if season_start[:4].isdigit():
            season_start_year = int(season_start[:4])
        code = str((competition.get('code') or '')).strip().upper()
        comp_id = competition.get('id')
        comp_ref = code or (str(comp_id).strip().upper() if comp_id is not None else '')
        return {
            'home': home,
            'away': away,
            'commence_time': dt,
            'league': str((competition.get('name') or '')).strip(),
            'competition_code': code,
            'competition_ref': comp_ref,
            'competition_type': str((competition.get('type') or '')).strip().upper(),
            'season_start_year': season_start_year,
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

    def _build_context_from_standings(self, match: Match, row: dict[str, Any], table: list[dict[str, Any]]) -> MatchContext | None:
        home_row = self._find_team_row(match.home_team, table)
        away_row = self._find_team_row(match.away_team, table)
        if home_row is None or away_row is None:
            return None

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
            'football_data_mode': 'standings',
            'football_data_competition': str(((row.get('competition') or {}).get('name')) or match.league_name),
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

    def _build_context_from_history(self, match: Match, row: dict[str, Any], rows: list[dict[str, Any]]) -> MatchContext | None:
        match_dt = match.commence_time.astimezone(UTC)
        home_games: list[tuple[datetime, float, float]] = []
        away_games: list[tuple[datetime, float, float]] = []
        h2h_totals: list[float] = []
        for hist in rows:
            try:
                dt = parse_datetime(str(hist.get('utcDate') or ''))
            except Exception:
                dt = None
            if dt is None or dt >= match_dt:
                continue
            score = hist.get('score') or {}
            full = score.get('fullTime') if isinstance(score, dict) else None
            if not isinstance(full, dict):
                continue
            try:
                home_goals = float(full.get('home') if full.get('home') is not None else full.get('homeTeam'))
                away_goals = float(full.get('away') if full.get('away') is not None else full.get('awayTeam'))
            except Exception:
                continue
            hist_home = str(((hist.get('homeTeam') or {}).get('name')) or '').strip()
            hist_away = str(((hist.get('awayTeam') or {}).get('name')) or '').strip()
            if not hist_home or not hist_away:
                continue

            home_sim = self._team_match_score(match.home_team, hist_home)
            away_sim = self._team_match_score(match.away_team, hist_away)
            rev_home = self._team_match_score(match.home_team, hist_away)
            rev_away = self._team_match_score(match.away_team, hist_home)

            if home_sim >= 0.78:
                home_games.append((dt, home_goals, away_goals))
            elif rev_home >= 0.78:
                home_games.append((dt, away_goals, home_goals))

            if away_sim >= 0.78:
                away_games.append((dt, away_goals, home_goals))
            elif rev_away >= 0.78:
                away_games.append((dt, home_goals, away_goals))

            if (home_sim >= 0.76 and away_sim >= 0.76) or (rev_home >= 0.76 and rev_away >= 0.76):
                h2h_totals.append(home_goals + away_goals)

        home_games.sort(key=lambda item: item[0], reverse=True)
        away_games.sort(key=lambda item: item[0], reverse=True)
        if len(home_games) < 2 or len(away_games) < 2:
            return None

        home_recent = home_games[:5]
        away_recent = away_games[:5]
        home_gf = sum(item[1] for item in home_recent) / len(home_recent)
        home_ga = sum(item[2] for item in home_recent) / len(home_recent)
        away_gf = sum(item[1] for item in away_recent) / len(away_recent)
        away_ga = sum(item[2] for item in away_recent) / len(away_recent)
        home_points = sum(3.0 if gf > ga else 1.0 if gf == ga else 0.0 for _, gf, ga in home_recent)
        away_points = sum(3.0 if gf > ga else 1.0 if gf == ga else 0.0 for _, gf, ga in away_recent)
        home_ppg = home_points / len(home_recent)
        away_ppg = away_points / len(away_recent)
        home_form = home_ppg / 3.0
        away_form = away_ppg / 3.0
        expected_home = clamp(((home_gf + away_ga) / 2.0) + 0.14, 0.30, 3.25)
        expected_away = clamp(((away_gf + home_ga) / 2.0), 0.25, 3.10)
        delta = clamp((home_ppg - away_ppg) * 0.12 + (home_form - away_form) * 0.08, -0.18, 0.18)
        draw = clamp(0.26 - abs(delta) * 0.16, 0.17, 0.31)
        home = 0.38 + delta
        away = 1.0 - home - draw
        probs = self._normalize_probs(home, away, draw)
        confidence = clamp(54.0 + min(len(home_recent), len(away_recent)) * 1.6, 53.0, 62.0)
        details = {
            'football_data_mode': 'history_fallback',
            'football_data_home_ppg': round(home_ppg, 3),
            'football_data_away_ppg': round(away_ppg, 3),
            'football_data_home_form': round(home_form, 3),
            'football_data_away_form': round(away_form, 3),
            'football_data_h2h_avg_goals': round(sum(h2h_totals) / len(h2h_totals), 3) if h2h_totals else None,
            'football_data_competition': str(((row.get('competition') or {}).get('name')) or match.league_name),
        }
        return MatchContext(
            source='football_data',
            payload={'scheduled': row, 'recent_home': home_recent, 'recent_away': away_recent},
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
            score = self._team_match_score(team_name, row_name)
            if score > best_score:
                best_score = score
                best_row = row
        return best_row if best_score >= 0.72 else None

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
