from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import canonicalize_team_name, clamp, parse_datetime, soft_contains_team, team_similarity


class OpenFootballContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = str(settings.openfootball_base_url or 'https://raw.githubusercontent.com/openfootball/football.json/master').rstrip('/')
        self.timeout = float(settings.openfootball_timeout_seconds or 15.0)
        self.league_map = self._parse_competition_map(getattr(settings, 'openfootball_competition_map', []) or [])
        self.alias_map = {
            'greece super league': 'gr.1',
            'greece - super league': 'gr.1',
            'greek super league': 'gr.1',
            'norway - eliteserien': 'no.1',
            'norway - 1st division': 'no.2',
            'italy - serie a': 'it.1',
            'italy - serie b': 'it.2',
            'italy - serie c': 'it.3',
            'italy - serie c group a': 'it.3',
            'italy - serie c group b': 'it.3',
            'italy - serie c group c': 'it.3',
            'england - premier league': 'en.1',
            'england - championship': 'en.2',
            'england - league one': 'en.3',
            'england - league two': 'en.4',
            'germany - bundesliga': 'de.1',
            'germany - bundesliga 2': 'de.2',
            'spain - la liga': 'es.1',
            'spain - la liga 2': 'es.2',
            'france - ligue 1': 'fr.1',
            'france - ligue 2': 'fr.2',
            'portugal - primeira liga': 'pt.1',
            'belgium - pro league': 'be.1',
            'switzerland - super league': 'ch.1',
            'switzerland - challenge league': 'ch.2',
        }

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            'enabled': bool(getattr(self.settings, 'enable_openfootball_context', True)),
            'requests': 0,
            'response_errors': 0,
            'datasets_loaded': 0,
            'contexts_built': 0,
            'matched_exact': 0,
            'matched_loose': 0,
            'matched_fuzzy': 0,
            'http_statuses': [],
            'last_body_preview': None,
        }
        preview: dict[str, Any] = {'sample_datasets': [], 'sample_contexts': []}
        if not stats['enabled']:
            return {}, stats, preview
        soccer_matches = [item for item in matches if item.sport_key == 'soccer']
        if not soccer_matches:
            return {}, stats, preview
        match_limit = max(1, int(getattr(self.settings, 'openfootball_match_limit', 24) or 24))
        soccer_matches = soccer_matches[:match_limit]
        grouped: dict[str, list[Match]] = defaultdict(list)
        for match in soccer_matches:
            comp_key = self._competition_key(match.league_name)
            if not comp_key:
                continue
            grouped[comp_key].append(match)

        datasets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        dataset_limit = max(1, int(getattr(self.settings, 'openfootball_dataset_limit', 12) or 12))
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for comp_key, match_group in list(grouped.items())[:dataset_limit]:
                loaded = None
                used_season = None
                path_candidates: list[tuple[str, str]] = []
                for season in self._season_candidates(match_group[0].commence_time.astimezone(UTC)):
                    path_candidates.extend([
                        (season, f'/{season}/{comp_key}.json'),
                        (season, f'/{season}/{comp_key.lower()}.json'),
                    ])
                seen_paths: set[str] = set()
                for season, path in path_candidates:
                    if path in seen_paths:
                        continue
                    seen_paths.add(path)
                    payload = await self._fetch_json(client, path, stats)
                    matches_payload = [row for row in ((payload or {}).get('matches') or []) if isinstance(row, dict)] if isinstance(payload, dict) else []
                    if matches_payload:
                        loaded = matches_payload
                        used_season = season
                        break
                if not loaded:
                    continue
                datasets[(comp_key, used_season or 'unknown')] = loaded
                stats['datasets_loaded'] += 1
                if len(preview['sample_datasets']) < 4:
                    preview['sample_datasets'].append({'competition_key': comp_key, 'season': used_season or 'unknown', 'matches': loaded[:2]})

        contexts: dict[str, MatchContext] = {}
        for (comp_key, season), rows in datasets.items():
            target_matches = [m for m in soccer_matches if self._competition_key(m.league_name) == comp_key]
            for match in target_matches:
                context, quality = self._build_context(match, rows)
                if context is None:
                    continue
                contexts[match.match_key] = context
                stats['contexts_built'] += 1
                if quality == 'exact':
                    stats['matched_exact'] += 1
                elif quality == 'loose':
                    stats['matched_loose'] += 1
                else:
                    stats['matched_fuzzy'] += 1
                if len(preview['sample_contexts']) < 8:
                    preview['sample_contexts'].append({'match_key': match.match_key, 'season': season, 'expected_home': context.expected_home, 'expected_away': context.expected_away})
        return contexts, stats, preview

    def supports_match(self, match: Match) -> bool:
        return str(getattr(match, 'sport_key', '') or '') == 'soccer' and bool(self._competition_key(match.league_name))

    async def _fetch_json(self, client: httpx.AsyncClient, path: str, stats: dict[str, Any]) -> Any | None:
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
            result[left.strip().lower()] = right.strip()
        return result

    def _competition_key(self, league_name: str) -> str | None:
        raw = str(league_name or '').strip().lower()
        norm = self._normalize_league_name(raw)
        for key in (raw, norm):
            if key in self.league_map:
                return self.league_map[key]
            if key in self.alias_map:
                return self.alias_map[key]
        for source in (raw, norm):
            for left, right in {**self.league_map, **self.alias_map}.items():
                if left in source or source in left:
                    return right
        return None

    @staticmethod
    def _normalize_league_name(name: str) -> str:
        text = ' '.join(str(name or '').lower().replace('_', ' ').replace('/', ' ').replace('.', ' ').split())
        for ch in ',:;()[]{}':
            text = text.replace(ch, ' ')
        text = ' '.join(text.split())
        return text

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

    @staticmethod
    def _season_candidates(dt: datetime) -> list[str]:
        year = dt.year
        if dt.month >= 7:
            return [f'{year}-{str(year + 1)[-2:]}', str(year)]
        return [f'{year - 1}-{str(year)[-2:]}', str(year), str(year - 1)]

    def _build_context(self, match: Match, rows: list[dict[str, Any]]) -> tuple[MatchContext | None, str | None]:
        match_dt = match.commence_time.astimezone(UTC)
        home_games = []
        away_games = []
        h2h_goals: list[float] = []
        best_quality: str | None = None
        for row in rows:
            try:
                row_dt = parse_datetime(f"{row.get('date')}T12:00:00+00:00")
            except Exception:
                continue
            if row_dt >= match_dt:
                continue
            t1 = str(row.get('team1') or '')
            t2 = str(row.get('team2') or '')
            if not t1 or not t2:
                continue
            score = row.get('score') or {}
            ft = score.get('ft') if isinstance(score, dict) else None
            if not isinstance(ft, list) or len(ft) < 2:
                continue
            try:
                goals1 = float(ft[0])
                goals2 = float(ft[1])
            except Exception:
                continue
            home_sim = self._team_match_score(match.home_team, t1)
            away_sim = self._team_match_score(match.away_team, t2)
            rev_home = self._team_match_score(match.home_team, t2)
            rev_away = self._team_match_score(match.away_team, t1)
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
        if not home_games or not away_games:
            return None, None
        if len(home_games) + len(away_games) < 4:
            return None, None
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
        home_rest = max((match_dt - home_recent[0][0]).days, 0)
        away_rest = max((match_dt - away_recent[0][0]).days, 0)
        expected_home = clamp(((home_gf + away_ga) / 2.0) + 0.14, 0.30, 3.40)
        expected_away = clamp(((away_gf + home_ga) / 2.0), 0.25, 3.20)
        delta = clamp((home_ppg - away_ppg) * 0.12 + (home_form - away_form) * 0.08, -0.18, 0.18)
        draw = clamp(0.25 - abs(delta) * 0.16, 0.16, 0.30)
        home = 0.38 + delta
        away = 1.0 - home - draw
        total = home + away + draw
        home /= total
        away /= total
        draw /= total
        support = min(len(home_recent), len(away_recent))
        confidence = clamp(53.0 + support * 1.8, 53.0, 64.0)
        details = {
            'home_form': round(home_form, 3),
            'away_form': round(away_form, 3),
            'home_ppg': round(home_ppg, 3),
            'away_ppg': round(away_ppg, 3),
            'home_rest_days': float(home_rest),
            'away_rest_days': float(away_rest),
            'openfootball_h2h_avg_goals': round(sum(h2h_goals) / len(h2h_goals), 3) if h2h_goals else None,
            'openfootball_home_form': round(home_form, 3),
            'openfootball_away_form': round(away_form, 3),
            'openfootball_home_ppg': round(home_ppg, 3),
            'openfootball_away_ppg': round(away_ppg, 3),
            'draw_probability': round(draw, 4),
        }
        return MatchContext(
            source='openfootball',
            payload={'recent_home': home_recent, 'recent_away': away_recent},
            expected_home=round(expected_home, 3),
            expected_away=round(expected_away, 3),
            home_win_probability=round(home, 4),
            away_win_probability=round(away, 4),
            confidence=float(round(confidence, 2)),
            details=details,
        ), best_quality
