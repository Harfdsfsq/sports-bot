from __future__ import annotations

from collections import Counter
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import clamp, canonicalize_league_name, canonicalize_team_name, parse_datetime, score_event_match

ESPN_SOCCER_LEAGUE_ALIASES = {
    'epl': 'eng.1',
    'english premier league': 'eng.1',
    'premier league': 'eng.1',
    'england premier league': 'eng.1',
    'english league championship': 'eng.2',
    'championship': 'eng.2',
    'england championship': 'eng.2',
    'league one': 'eng.3',
    'england league one': 'eng.3',
    'league two': 'eng.4',
    'england league two': 'eng.4',
    'laliga': 'esp.1',
    'la liga': 'esp.1',
    'spanish laliga': 'esp.1',
    'la liga 2': 'esp.2',
    'spanish la liga 2': 'esp.2',
    'serie a': 'ita.1',
    'italian serie a': 'ita.1',
    'serie b': 'ita.2',
    'italian serie b': 'ita.2',
    'bundesliga': 'ger.1',
    'german bundesliga': 'ger.1',
    'bundesliga 2': 'ger.2',
    'german bundesliga 2': 'ger.2',
    'ligue 1': 'fra.1',
    'french ligue 1': 'fra.1',
    'ligue 2': 'fra.2',
    'french ligue 2': 'fra.2',
    'eredivisie': 'ned.1',
    'netherlands eredivisie': 'ned.1',
    'jupiler pro league': 'bel.1',
    'belgian pro league': 'bel.1',
    'primeira liga': 'por.1',
    'portuguese primeira liga': 'por.1',
    'scottish premiership': 'sco.1',
    'scotland premiership': 'sco.1',
    'mls': 'usa.1',
    'major league soccer': 'usa.1',
    'uefa champions league': 'uefa.champions',
    'champions league': 'uefa.champions',
    'uefa europa league': 'uefa.europa',
    'europa league': 'uefa.europa',
    'uefa europa conference league': 'uefa.europa.conf',
    'conference league': 'uefa.europa.conf',
    'english national league': 'eng.5',
    'national league': 'eng.5',
    'netherlands eerste divisie': 'ned.2',
    'eerste divisie': 'ned.2',
    'netherlands tweede divisie': 'ned.3',
    'scottish championship': 'sco.2',
    'scottish league one': 'sco.3',
    'scottish league two': 'sco.4',
    'norwegian eliteserien': 'nor.1',
    'eliteserien': 'nor.1',
    'danish superliga': 'den.1',
    'superliga': 'den.1',
    'swedish allsvenskan': 'swe.1',
    'allsvenskan': 'swe.1',
    'turkish super lig': 'tur.1',
    'super lig': 'tur.1',
    'austrian bundesliga': 'aut.1',
}


class EspnContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.site_base_url = (settings.espn_base_site_url or 'https://site.api.espn.com/apis/site/v2').rstrip('/')
        self.core_base_url = (settings.espn_base_core_url or 'https://sports.core.api.espn.com/v2').rstrip('/')
        self.timeout = float(settings.espn_timeout_seconds or 20.0)
        self.allowed_leagues = [item.strip() for item in (settings.espn_soccer_leagues or []) if item and item.strip()]

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            'enabled': bool(self.settings.enable_espn_context),
            'scoreboard_requests': 0,
            'probability_requests': 0,
            'summary_requests': 0,
            'response_errors': 0,
            'events_fetched': 0,
            'events_matched': 0,
            'contexts_built': 0,
            'matched_exact': 0,
            'matched_loose': 0,
            'matched_fuzzy': 0,
            'league_slugs_used': [],
            'injury_counts_seen': 0,
            'soft_failures': 0,
            'partial_contexts_built': 0,
            'last_body_preview': None,
            'http_statuses': [],
        }
        preview: dict[str, Any] = {
            'sample_events': [],
            'sample_probabilities': [],
            'matched_examples': [],
        }
        if not self.settings.enable_espn_context:
            return {}, stats, preview

        soccer_matches = [match for match in matches if match.sport_key == 'soccer']
        if not soccer_matches:
            return {}, stats, preview

        prioritized = self._prioritize_matches(soccer_matches)
        max_matches = max(1, int(self.settings.espn_max_matches or 120))
        if len(prioritized) > max_matches:
            prioritized = prioritized[:max_matches]
            stats['candidate_matches_limited_to'] = max_matches

        slugs = self._candidate_slugs(prioritized)
        stats['league_slugs_used'] = slugs
        if not slugs:
            return {}, stats, preview

        events_by_slug: dict[str, list[dict[str, Any]]] = {slug: [] for slug in slugs}
        now = datetime.now(UTC)
        days = max(1, int(self.settings.run_days_ahead or 4))

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for slug in slugs:
                for offset in range(days + 1):
                    target_day = (now + timedelta(days=offset)).strftime('%Y%m%d')
                    url = f'{self.site_base_url}/sports/soccer/{slug}/scoreboard'
                    stats['scoreboard_requests'] += 1
                    try:
                        response = await client.get(url, params={'dates': target_day})
                    except Exception as exc:
                        stats['response_errors'] += 1
                        stats['last_body_preview'] = f'scoreboard request failed: {exc}'
                        continue
                    stats['http_statuses'].append(response.status_code)
                    stats['last_body_preview'] = response.text[:1200]
                    if response.status_code != 200:
                        stats['response_errors'] += 1
                        continue
                    payload = self._safe_json(response)
                    events = self._extract_events(payload)
                    if events and not preview['sample_events']:
                        preview['sample_events'] = events[:3]
                    stats['events_fetched'] += len(events)
                    events_by_slug[slug].extend(events)

            contexts: dict[str, MatchContext] = {}
            all_events = [(slug, event) for slug, items in events_by_slug.items() for event in items]
            for match in prioritized:
                hinted_slug = self._league_slug(match.league_name)
                search_pool = [(slug, event) for slug, event in all_events if slug == hinted_slug] if hinted_slug else []
                if not search_pool:
                    search_pool = all_events
                event, slug, quality = self._match_event(match, search_pool)
                if event is None or slug is None:
                    continue
                stats['events_matched'] += 1
                if quality == 'exact':
                    stats['matched_exact'] += 1
                elif quality == 'loose':
                    stats['matched_loose'] += 1
                elif quality == 'fuzzy':
                    stats['matched_fuzzy'] += 1

                if len(preview['matched_examples']) < 8:
                    preview['matched_examples'].append(
                        {
                            'match_key': match.match_key,
                            'league_slug': slug,
                            'event_id': event.get('id'),
                            'home': self._event_home(event),
                            'away': self._event_away(event),
                            'quality': quality,
                        }
                    )

                context = await self._build_context(client, match, slug, event, stats, preview)
                if context is None:
                    continue
                contexts[match.match_key] = context
                stats['contexts_built'] += 1
                injuries = (context.details or {}).get('espn_home_injuries', 0) + (context.details or {}).get('espn_away_injuries', 0)
                stats['injury_counts_seen'] += int(injuries or 0)

        return contexts, stats, preview

    def _prioritize_matches(self, matches: list[Match]) -> list[Match]:
        counts = Counter(match.league_name for match in matches)
        return sorted(
            matches,
            key=lambda item: (
                0 if self._league_slug(item.league_name) else 1,
                -counts[item.league_name],
                item.commence_time,
                item.league_name,
                item.home_team,
            ),
        )

    def _candidate_slugs(self, matches: list[Match]) -> list[str]:
        slug_scores: dict[str, float] = {}
        for idx, match in enumerate(matches):
            slug = self._league_slug(match.league_name)
            if not slug:
                continue
            tier_bonus = 2.5 if getattr(match, 'tier', 'mid') == 'top' else 1.0 if getattr(match, 'tier', 'mid') == 'mid' else 0.0
            competition_bonus = 2.5 if slug.startswith('uefa.') else 0.0
            recency_bonus = max(0.0, 3.0 - idx * 0.08)
            slug_scores[slug] = max(slug_scores.get(slug, 0.0), tier_bonus + competition_bonus + recency_bonus)
        discovered = [slug for slug, _ in sorted(slug_scores.items(), key=lambda item: item[1], reverse=True)]
        if getattr(self.settings, 'espn_query_all_allowed_when_unmapped', True):
            for slug in self.allowed_leagues:
                if slug not in discovered:
                    discovered.append(slug)
        elif self.allowed_leagues:
            discovered = [slug for slug in discovered if slug in self.allowed_leagues]
        limit = max(1, int(getattr(self.settings, 'espn_slugs_per_run_limit', 30) or 30))
        return discovered[:limit]

    def _league_slug(self, league_name: str) -> str | None:
        key = canonicalize_league_name(league_name)
        candidates = [key]
        compact = key.replace(' - ', ' ').replace('-', ' ')
        if compact not in candidates:
            candidates.append(compact)
        simplified = compact.replace('first division', 'league one').replace('second division', 'league two').replace('1st division', 'league one').replace('2nd division', 'league two')
        if simplified not in candidates:
            candidates.append(simplified)
        for candidate in candidates:
            if candidate in ESPN_SOCCER_LEAGUE_ALIASES:
                return ESPN_SOCCER_LEAGUE_ALIASES[candidate]
        for candidate in candidates:
            for alias, slug in ESPN_SOCCER_LEAGUE_ALIASES.items():
                if alias in candidate or candidate in alias:
                    return slug
        return None

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any | None:
        try:
            return response.json()
        except Exception:
            return None

    @staticmethod
    def _extract_events(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        events = payload.get('events')
        if not isinstance(events, list):
            return []
        return [event for event in events if isinstance(event, dict)]

    def _match_event(self, match: Match, pool: list[tuple[str, dict[str, Any]]]) -> tuple[dict[str, Any] | None, str | None, str | None]:
        best_event: dict[str, Any] | None = None
        best_slug: str | None = None
        best_quality: str | None = None
        best_score = 0.0
        for slug, event in pool:
            start = self._event_start(event)
            if start is None:
                continue
            score, quality = score_event_match(
                sport='soccer',
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home=self._event_home(event),
                event_away=self._event_away(event),
                event_start=start,
                event_league=slug,
                exact_tolerance_hours=float(getattr(self.settings, 'match_start_tolerance_hours', 12) or 12),
                fuzzy_tolerance_hours=float(getattr(self.settings, 'fallback_match_start_tolerance_hours', 8) or 8),
            )
            if score > best_score:
                best_score = score
                best_quality = quality
                best_event = event
                best_slug = slug
        threshold = float(getattr(self.settings, 'espn_event_match_threshold', 44.0) or 44.0)
        return (best_event, best_slug, best_quality) if best_score >= threshold else (None, None, None)

    async def _build_context(
        self,
        client: httpx.AsyncClient,
        match: Match,
        slug: str,
        event: dict[str, Any],
        stats: dict[str, Any],
        preview: dict[str, Any],
    ) -> MatchContext | None:
        event_id = str(event.get('id') or '')
        competition = self._event_competition(event)
        competition_id = str(competition.get('id') or event_id)
        home_team = self._event_team(event, 'home')
        away_team = self._event_team(event, 'away')

        home_prob: float | None = None
        away_prob: float | None = None
        draw_prob: float | None = None
        summary_payload: dict[str, Any] | None = None

        probability_url = f'{self.core_base_url}/sports/soccer/leagues/{slug}/events/{event_id}/competitions/{competition_id}/probabilities'
        stats['probability_requests'] += 1
        try:
            response = await client.get(probability_url)
        except Exception as exc:
            stats['response_errors'] += 1
            stats['last_body_preview'] = f'probabilities request failed: {exc}'
            response = None
        if response is not None:
            stats['http_statuses'].append(response.status_code)
            stats['last_body_preview'] = response.text[:1200]
            if response.status_code == 200:
                payload = self._safe_json(response)
                item = self._probability_item(payload)
                if item is not None:
                    home_prob = self._to_float(item.get('homeWinPercentage'))
                    away_prob = self._to_float(item.get('awayWinPercentage'))
                    draw_prob = self._to_float(item.get('tiePercentage'))
                    if len(preview['sample_probabilities']) < 3:
                        preview['sample_probabilities'].append(item)
            else:
                soft_statuses = {str(item).strip() for item in (getattr(self.settings, 'espn_soft_fail_statuses', []) or [])}
                if str(response.status_code) in soft_statuses:
                    stats['soft_failures'] += 1
                else:
                    stats['response_errors'] += 1

        summary_url = f'{self.site_base_url}/sports/soccer/{slug}/summary'
        stats['summary_requests'] += 1
        try:
            response = await client.get(summary_url, params={'event': event_id})
        except Exception as exc:
            stats['response_errors'] += 1
            stats['last_body_preview'] = f'summary request failed: {exc}'
            response = None
        if response is not None:
            stats['http_statuses'].append(response.status_code)
            stats['last_body_preview'] = response.text[:1200]
            if response.status_code == 200:
                payload = self._safe_json(response)
                if isinstance(payload, dict):
                    summary_payload = payload
                    predictor = self._extract_predictor(payload)
                    if predictor is not None and (home_prob is None or away_prob is None):
                        maybe_home = self._to_float((predictor.get('homeTeam') or {}).get('gameProjection'))
                        maybe_away = self._to_float((predictor.get('awayTeam') or {}).get('gameProjection'))
                        if maybe_home is not None:
                            home_prob = maybe_home / 100.0 if maybe_home > 1.0 else maybe_home
                        if maybe_away is not None:
                            away_prob = maybe_away / 100.0 if maybe_away > 1.0 else maybe_away
            else:
                soft_statuses = {str(item).strip() for item in (getattr(self.settings, 'espn_soft_fail_statuses', []) or [])}
                if str(response.status_code) in soft_statuses:
                    stats['soft_failures'] += 1
                else:
                    stats['response_errors'] += 1

        home_form = self._form_score(home_team)
        away_form = self._form_score(away_team)
        if home_form is None or away_form is None:
            comp_home_form, comp_away_form = self._competitor_form_scores(event)
            home_form = home_form if home_form is not None else comp_home_form
            away_form = away_form if away_form is not None else comp_away_form
        if summary_payload is not None and (home_form is None or away_form is None):
            summary_home_form, summary_away_form = self._summary_form_scores(summary_payload, match.home_team, match.away_team)
            home_form = home_form if home_form is not None else summary_home_form
            away_form = away_form if away_form is not None else summary_away_form
        partial_context = False
        if home_prob is None or away_prob is None:
            if home_form is not None and away_form is not None:
                delta = clamp((home_form - away_form) * 0.18 + 0.08, -0.35, 0.35)
                draw_prob = clamp(0.24 - abs(delta) * 0.18, 0.14, 0.30)
                home_prob = 0.38 + delta
                away_prob = 1.0 - home_prob - draw_prob
            elif getattr(self.settings, 'espn_allow_partial_context', True):
                partial_context = True
                if home_form is not None or away_form is not None:
                    base_home = 0.39 + ((home_form or 0.5) - (away_form or 0.5)) * 0.16
                    draw_prob = 0.25 if draw_prob is None else draw_prob
                    home_prob = base_home if home_prob is None else home_prob
                    away_prob = 1.0 - home_prob - draw_prob if away_prob is None else away_prob
                else:
                    draw_prob = 0.26 if draw_prob is None else draw_prob
                    home_prob = 0.39 if home_prob is None else home_prob
                    away_prob = 1.0 - home_prob - draw_prob if away_prob is None else away_prob
            else:
                return None

        probs = self._normalize_probs(home_prob, away_prob, draw_prob)
        expected_home, expected_away = self._expected_goals_from_probs(
            home_prob=probs['home'],
            away_prob=probs['away'],
            draw_prob=probs['draw'],
            home_form=home_form,
            away_form=away_form,
        )

        home_injuries = away_injuries = 0.0
        if summary_payload and getattr(self.settings, 'espn_enable_injuries', True):
            home_injuries, away_injuries = self._extract_injury_counts(summary_payload, match.home_team, match.away_team)
            penalty = float(getattr(self.settings, 'espn_news_absence_penalty_per_point', 0.05) or 0.05)
            expected_home = clamp(expected_home - min(float(home_injuries), 5.0) * penalty, 0.25, 3.4)
            expected_away = clamp(expected_away - min(float(away_injuries), 5.0) * penalty, 0.25, 3.2)

        confidence = 57.0
        if summary_payload is not None:
            confidence += 4.0
        if probs['home'] is not None and probs['away'] is not None:
            confidence += 6.0
        if home_form is not None and away_form is not None:
            confidence += 2.0
        if home_injuries or away_injuries:
            confidence += 1.0
        if partial_context:
            confidence = float(getattr(self.settings, 'espn_form_only_context_confidence', 53.0) or 53.0) if summary_payload is None else confidence - 7.0
            stats['partial_contexts_built'] += 1
        confidence = clamp(confidence, 50.0, 74.0)

        return MatchContext(
            source='espn',
            payload={'event': event, 'probabilities': {'home': probs['home'], 'away': probs['away'], 'draw': probs['draw']}},
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=probs['home'],
            away_win_probability=probs['away'],
            confidence=confidence,
            details={
                'espn_draw_probability': probs['draw'],
                'espn_league_slug': slug,
                'espn_event_id': event_id,
                'espn_competition_id': competition_id,
                'espn_home_form': home_form,
                'espn_away_form': away_form,
                'espn_home_injuries': home_injuries,
                'espn_away_injuries': away_injuries,
                'home_injuries': home_injuries,
                'away_injuries': away_injuries,
                'home_absences': home_injuries,
                'away_absences': away_injuries,
                'espn_partial_context': partial_context,
            },
        )

    @staticmethod
    def _probability_item(payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            items = payload.get('items')
            if isinstance(items, list) and items and isinstance(items[0], dict):
                return items[0]
            if all(key in payload for key in ('homeWinPercentage', 'awayWinPercentage')):
                return payload
        return None

    @staticmethod
    def _extract_predictor(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        predictor = payload.get('predictor')
        return predictor if isinstance(predictor, dict) else None

    @staticmethod
    def _team_aliases(team_name: str) -> set[str]:
        key = canonicalize_team_name(team_name)
        if not key:
            return set()
        aliases = {key}
        padded = f" {key} "
        replacements = {
            ' football club ': ' ',
            ' futbol club ': ' ',
            ' soccer club ': ' ',
            ' association football club ': ' ',
            ' athletic club ': ' ',
            ' united ': ' utd ',
            ' utd ': ' united ',
            ' saint ': ' st ',
            ' st ': ' saint ',
            ' club ': ' ',
            ' fc ': ' ',
            ' cf ': ' ',
            ' sc ': ' ',
            ' ac ': ' ',
            ' afc ': ' ',
            ' u23 ': ' ',
            ' u21 ': ' ',
            ' u20 ': ' ',
            ' u19 ': ' ',
        }
        aliases.add(' '.join(padded.split()))
        for src, dst in replacements.items():
            aliases.add(' '.join(padded.replace(src, dst).split()))
            aliases.add(' '.join(padded.replace(src, ' ').split()))
        return {item for item in aliases if item}

    @classmethod
    def _match_team_bucket(cls, team_text: str, home_team: str, away_team: str) -> str | None:
        candidate = canonicalize_team_name(team_text)
        if not candidate:
            return None
        home_aliases = cls._team_aliases(home_team)
        away_aliases = cls._team_aliases(away_team)
        if candidate in home_aliases:
            return 'home'
        if candidate in away_aliases:
            return 'away'
        for alias in home_aliases:
            if alias and (alias in candidate or candidate in alias):
                return 'home'
        for alias in away_aliases:
            if alias and (alias in candidate or candidate in alias):
                return 'away'
        return None

    def _absence_weight(self, text: str) -> float:
        lower = str(text or '').lower()
        if any(token in lower for token in ('suspend', 'red card', 'banned')):
            return float(getattr(self.settings, 'espn_injury_suspension_weight', 0.85) or 0.85)
        if any(token in lower for token in ('out', 'ruled out', 'inactive', 'missing')):
            return float(getattr(self.settings, 'espn_injury_out_weight', 1.0) or 1.0)
        if any(token in lower for token in ('doubt', 'doubtful')):
            return float(getattr(self.settings, 'espn_injury_doubtful_weight', 0.45) or 0.45)
        if any(token in lower for token in ('question', 'day-to-day', 'fitness test')):
            return float(getattr(self.settings, 'espn_injury_questionable_weight', 0.35) or 0.35)
        return 0.0

    @staticmethod
    def _extract_injury_counts(payload: dict[str, Any], home_team: str, away_team: str) -> tuple[int, int]:
        home_key = canonicalize_team_name(home_team)
        away_key = canonicalize_team_name(away_team)
        home_count = 0
        away_count = 0

        def walk(node: Any) -> list[dict[str, Any]]:
            found: list[dict[str, Any]] = []
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == 'injuries' and isinstance(value, list):
                        found.extend(item for item in value if isinstance(item, dict))
                    else:
                        found.extend(walk(value))
            elif isinstance(node, list):
                for item in node:
                    found.extend(walk(item))
            return found

        for item in walk(payload):
            team_text = ' '.join(
                str(item.get(key) or '')
                for key in ('team', 'teamName', 'displayName', 'shortDisplayName', 'abbreviation')
            ).strip()
            team_key = canonicalize_team_name(team_text)
            if not team_key:
                raw_team = item.get('team')
                if isinstance(raw_team, dict):
                    team_key = canonicalize_team_name(
                        str(raw_team.get('displayName') or raw_team.get('shortDisplayName') or raw_team.get('name') or '')
                    )
            if team_key == home_key:
                home_count += 1
            elif team_key == away_key:
                away_count += 1
        return home_count, away_count

    @staticmethod
    def _event_start(event: dict[str, Any]) -> datetime | None:
        try:
            return parse_datetime(event.get('date'))
        except Exception:
            return None

    @staticmethod
    def _event_competition(event: dict[str, Any]) -> dict[str, Any]:
        competitions = event.get('competitions')
        if isinstance(competitions, list):
            for item in competitions:
                if isinstance(item, dict):
                    return item
        return {}

    def _event_team(self, event: dict[str, Any], side: str) -> dict[str, Any]:
        competitors = (self._event_competition(event).get('competitors') or [])
        side = side.lower()
        for competitor in competitors:
            if not isinstance(competitor, dict):
                continue
            if str(competitor.get('homeAway') or '').lower() == side:
                team = competitor.get('team') or {}
                return team if isinstance(team, dict) else {}
        return {}

    def _event_home(self, event: dict[str, Any]) -> str:
        team = self._event_team(event, 'home')
        return str(team.get('displayName') or team.get('shortDisplayName') or team.get('name') or '')

    def _event_away(self, event: dict[str, Any]) -> str:
        team = self._event_team(event, 'away')
        return str(team.get('displayName') or team.get('shortDisplayName') or team.get('name') or '')

    def _competitor_form_scores(self, event: dict[str, Any]) -> tuple[float | None, float | None]:
        competition = self._event_competition(event)
        competitors = competition.get('competitors') if isinstance(competition, dict) else []
        home_form = away_form = None
        if isinstance(competitors, list):
            for competitor in competitors:
                if not isinstance(competitor, dict):
                    continue
                score = self._form_score(competitor)
                side = str(competitor.get('homeAway') or '').lower()
                if side == 'home' and home_form is None:
                    home_form = score
                elif side == 'away' and away_form is None:
                    away_form = score
        return home_form, away_form

    def _summary_form_scores(self, payload: dict[str, Any], home_team: str, away_team: str) -> tuple[float | None, float | None]:
        boxscore = payload.get('boxscore') if isinstance(payload, dict) else None
        form_rows = boxscore.get('form') if isinstance(boxscore, dict) else None
        if not isinstance(form_rows, list):
            return None, None
        home_key = canonicalize_team_name(home_team)
        away_key = canonicalize_team_name(away_team)
        home_form = away_form = None
        for row in form_rows:
            if not isinstance(row, dict):
                continue
            team = row.get('team') if isinstance(row.get('team'), dict) else {}
            team_name = str(team.get('displayName') or team.get('shortDisplayName') or team.get('name') or '')
            team_key = canonicalize_team_name(team_name)
            score = self._form_score(row)
            if score is None:
                score = self._form_score(team)
            if team_key == home_key and home_form is None:
                home_form = score
            elif team_key == away_key and away_form is None:
                away_form = score
        return home_form, away_form

    @staticmethod
    def _form_score(team: dict[str, Any]) -> float | None:
        candidates: list[str] = []
        for key in ('form', 'recentForm', 'formDisplayValue'):
            value = team.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value)
        records = team.get('records')
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                for key in ('summary', 'displayValue', 'value'):
                    value = record.get(key)
                    if isinstance(value, str) and value.strip():
                        candidates.append(value)
        for text in candidates:
            score = EspnContextProvider._parse_form_text(text)
            if score is not None:
                return score
        return None

    @staticmethod
    def _parse_form_text(value: str) -> float | None:
        raw = ''.join(ch for ch in str(value or '').upper() if ch.isalnum() or ch == '-')
        if not raw:
            return None
        letters = [ch for ch in raw if ch in {'W', 'D', 'L'}]
        if letters:
            total = 0.0
            for ch in letters[-5:]:
                if ch == 'W':
                    total += 1.0
                elif ch == 'D':
                    total += 0.5
            return total / max(1, min(len(letters), 5))
        parts = raw.split('-')
        if len(parts) >= 3:
            try:
                wins, draws, losses = (float(parts[0]), float(parts[1]), float(parts[2]))
                played = max(1.0, wins + draws + losses)
                return (wins + 0.5 * draws) / played
            except Exception:
                return None
        return None

    @staticmethod
    def _normalize_probs(home: float | None, away: float | None, draw: float | None) -> dict[str, float]:
        home_value = clamp(float(home or 0.0), 0.05, 0.90)
        away_value = clamp(float(away or 0.0), 0.05, 0.90)
        draw_value = clamp(float(draw or max(0.10, 1.0 - home_value - away_value)), 0.06, 0.35)
        total = home_value + away_value + draw_value
        if total <= 0:
            return {'home': 0.40, 'away': 0.32, 'draw': 0.28}
        return {
            'home': home_value / total,
            'away': away_value / total,
            'draw': draw_value / total,
        }

    @staticmethod
    def _expected_goals_from_probs(
        *,
        home_prob: float,
        away_prob: float,
        draw_prob: float,
        home_form: float | None,
        away_form: float | None,
    ) -> tuple[float, float]:
        total_goals = 2.45
        if home_form is not None and away_form is not None:
            total_goals += (home_form + away_form - 1.0) * 0.35
        total_goals = clamp(total_goals, 1.8, 3.4)
        diff = clamp((home_prob - away_prob) * 1.35 + (0.5 - draw_prob) * 0.18, -1.35, 1.35)
        expected_home = clamp((total_goals / 2.0) + diff / 2.0 + 0.08, 0.35, 3.3)
        expected_away = clamp(total_goals - expected_home, 0.25, 3.1)
        return expected_home, expected_away

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value in (None, ''):
                return None
            return float(value)
        except Exception:
            return None
