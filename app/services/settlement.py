from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
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
        manual_overrides, manual_meta = self._load_manual_overrides()
        if not getattr(self.settings, 'settlement_enabled', True):
            return self._empty_probe_result(0, manual_meta)
        pending = [item for item in bets if self._eligible(item, now_utc)]
        has_sstats = bool(getattr(self.settings, 'sstats_api_key', None))
        has_football_data = bool(getattr(self.settings, 'football_data_api_key', None))
        has_manual = manual_meta['valid_count'] > 0
        if not pending or (not has_sstats and not has_football_data and not has_manual):
            return self._empty_probe_result(len(pending), manual_meta)
        start_date = (min(parse_datetime(item['commence_time']) for item in pending).astimezone(UTC) - timedelta(days=1)).date().isoformat()
        end_date = now_utc.date().isoformat()
        rows: list[dict[str, Any]] = []
        if has_sstats:
            rows.extend(await self._fetch_sstats_rows(start_date, end_date))
        if has_football_data:
            rows.extend(await self._fetch_football_data_rows(start_date, end_date))
        items: list[dict[str, Any]] = []
        reasons: defaultdict[str, int] = defaultdict(int)
        debug_bets: list[dict[str, Any]] = []
        manual_matches = 0
        for bet in pending:
            debug_entry: dict[str, Any] = {
                'fingerprint': str(bet.get('fingerprint') or ''),
                'home_team': str(bet.get('home_team') or ''),
                'away_team': str(bet.get('away_team') or ''),
                'league_name': str(bet.get('league_name') or ''),
                'commence_time': str(bet.get('commence_time') or ''),
                'family': str(bet.get('family') or ''),
                'selection': str(bet.get('selection') or ''),
                'selection_key': str(bet.get('selection_key') or ''),
                'point': bet.get('point'),
            }
            manual_row, manual_debug = self._find_manual_override_row(bet, manual_overrides)
            if manual_row is not None:
                row = manual_row
                debug_entry.update(manual_debug)
                manual_matches += 1
            else:
                row, match_debug = self._match_row_with_debug(bet, rows)
                debug_entry.update(match_debug)
            if row is None:
                debug_entry['reason'] = 'no_match'
                reasons['no_match'] += 1
                debug_bets.append(debug_entry)
                continue
            home_goals = self._extract_result(row, 'home')
            away_goals = self._extract_result(row, 'away')
            if home_goals is None or away_goals is None:
                debug_entry['reason'] = 'missing_score'
                reasons['missing_score'] += 1
                debug_bets.append(debug_entry)
                continue
            outcome, pnl = self._grade_bet(bet, float(home_goals), float(away_goals))
            if outcome is None:
                debug_entry['reason'] = 'grade_skipped'
                debug_entry['odds'] = float(bet.get('odds') or 0.0)
                debug_entry['stake_amount'] = float(bet.get('stake_amount') or 0.0)
                reasons['grade_skipped'] += 1
                debug_bets.append(debug_entry)
                continue
            debug_entry['reason'] = 'settled'
            debug_entry['settlement_outcome'] = outcome
            debug_entry['settlement_source'] = str(row.get('_settlement_source') or 'unknown')
            debug_entry['final_home_goals'] = float(home_goals)
            debug_entry['final_away_goals'] = float(away_goals)
            reasons['settled'] += 1
            debug_bets.append(debug_entry)
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
        rows_by_source = self._rows_by_source(rows)
        return {
            'checked': len(pending),
            'items': items,
            'rows_fetched': len(rows),
            'rows_by_source': rows_by_source,
            'manual_overrides_loaded': manual_meta['loaded_count'],
            'manual_overrides_valid': manual_meta['valid_count'],
            'manual_overrides_disabled': manual_meta['disabled_count'],
            'manual_overrides_invalid': manual_meta['invalid_count'],
            'manual_overrides_matched': manual_matches,
            'reasons': dict(reasons),
            'bets': debug_bets,
            'sample': [self._compact_debug_entry(item) for item in debug_bets[:8]],
        }

    def _empty_probe_result(self, checked: int, manual_meta: dict[str, int]) -> dict[str, Any]:
        return {
            'checked': checked,
            'items': [],
            'rows_fetched': 0,
            'rows_by_source': {},
            'manual_overrides_loaded': manual_meta['loaded_count'],
            'manual_overrides_valid': manual_meta['valid_count'],
            'manual_overrides_disabled': manual_meta['disabled_count'],
            'manual_overrides_invalid': manual_meta['invalid_count'],
            'manual_overrides_matched': 0,
            'reasons': {},
            'bets': [],
            'sample': [],
        }

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

    def _match_row_with_debug(self, bet: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        best_row = None
        best_key: tuple[int, float, int] | None = None
        match_start = parse_datetime(str(bet.get('commence_time')))
        top_candidates: list[tuple[tuple[int, float, int], dict[str, Any]]] = []
        rows_with_start = 0
        for row in rows:
            event_start = self._extract_start(row)
            if event_start is None:
                continue
            rows_with_start += 1
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
            source_name = str(row.get('_settlement_source') or '')
            source_priority = 3 if source_name == 'manual_override' else 2 if source_name == 'sstats' else 1
            key = (has_score, score, source_priority)
            candidate_debug = self._row_debug(row=row, score=score, has_score=bool(has_score))
            top_candidates.append((key, candidate_debug))
            top_candidates.sort(key=lambda item: item[0], reverse=True)
            if len(top_candidates) > 3:
                top_candidates = top_candidates[:3]
            if best_key is None or key > best_key:
                best_key = key
                best_row = row
        debug = {
            'rows_considered': len(rows),
            'rows_with_start': rows_with_start,
            'best_score': round(float(best_key[1]), 2) if best_key is not None else 0.0,
            'best_source': str(best_row.get('_settlement_source') or 'unknown') if best_row is not None else None,
            'best_status': self._row_status(best_row) if best_row is not None else None,
            'best_has_score': bool(best_key[0]) if best_key is not None else False,
            'best_event': self._row_event_label(best_row) if best_row is not None else None,
            'best_scoreline': self._row_scoreline(best_row) if best_row is not None else None,
            'top_candidates': [item for _, item in top_candidates],
        }
        if best_row is None or best_key is None or best_key[1] < 70.0:
            return None, debug
        return best_row, debug

    def _load_manual_overrides(self) -> tuple[list[dict[str, Any]], dict[str, int]]:
        path = Path(str(getattr(self.settings, 'manual_settlement_path', '.data/manual-settlements.json') or '.data/manual-settlements.json'))
        if not path.is_absolute():
            path = Path.cwd() / path
        meta: dict[str, int] = {
            'loaded_count': 0,
            'valid_count': 0,
            'disabled_count': 0,
            'invalid_count': 0,
        }
        if not path.exists():
            return [], meta
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            meta['invalid_count'] = 1
            return [], meta
        raw_items = payload.get('items') if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            meta['invalid_count'] = 1
            return [], meta
        overrides: list[dict[str, Any]] = []
        meta['loaded_count'] = len(raw_items)
        for item in raw_items:
            normalized = self._normalize_manual_override(item)
            status = str(normalized.get('_status') or 'invalid')
            if status == 'valid':
                overrides.append(normalized)
                meta['valid_count'] += 1
            elif status == 'disabled':
                meta['disabled_count'] += 1
            else:
                meta['invalid_count'] += 1
        return overrides, meta

    def _normalize_manual_override(self, item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {'_status': 'invalid'}
        if not bool(item.get('enabled', True)):
            return {'_status': 'disabled'}
        home_goals = self._coerce_goal(item.get('home_goals'))
        away_goals = self._coerce_goal(item.get('away_goals'))
        if home_goals is None or away_goals is None:
            return {'_status': 'invalid'}
        fingerprint = str(item.get('fingerprint') or '').strip()
        match_key = str(item.get('match_key') or '').strip()
        home_team = str(item.get('home_team') or '').strip()
        away_team = str(item.get('away_team') or '').strip()
        if not fingerprint and not match_key and (not home_team or not away_team):
            return {'_status': 'invalid'}
        return {
            '_status': 'valid',
            'fingerprint': fingerprint,
            'match_key': match_key,
            'sport_key': str(item.get('sport_key') or '').strip(),
            'league_name': str(item.get('league_name') or '').strip(),
            'home_team': home_team,
            'away_team': away_team,
            'commence_time': str(item.get('commence_time') or '').strip(),
            'home_goals': home_goals,
            'away_goals': away_goals,
            'note': str(item.get('note') or '').strip(),
        }

    def _find_manual_override_row(self, bet: dict[str, Any], overrides: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        for override in overrides:
            match_mode = self._manual_override_match_mode(bet, override)
            if match_mode is None:
                continue
            row = self._build_manual_override_row(bet, override, match_mode)
            debug = {
                'rows_considered': 0,
                'rows_with_start': 0,
                'best_score': 100.0,
                'best_source': 'manual_override',
                'best_status': self._row_status(row),
                'best_has_score': True,
                'best_event': self._row_event_label(row),
                'best_scoreline': self._row_scoreline(row),
                'matched_via': 'manual_override',
                'manual_override_match_mode': match_mode,
                'manual_override_note': override.get('note') or None,
                'top_candidates': [self._row_debug(row=row, score=100.0, has_score=True)],
            }
            return row, debug
        return None, {}

    def _manual_override_match_mode(self, bet: dict[str, Any], override: dict[str, Any]) -> str | None:
        fingerprint = str(override.get('fingerprint') or '')
        if fingerprint and fingerprint == str(bet.get('fingerprint') or ''):
            return 'fingerprint'
        match_key = str(override.get('match_key') or '')
        if match_key and match_key == str(bet.get('match_key') or ''):
            return 'match_key'
        home_team = str(override.get('home_team') or '')
        away_team = str(override.get('away_team') or '')
        if not home_team or not away_team:
            return None
        if self._normalize_name(home_team) != self._normalize_name(str(bet.get('home_team') or '')):
            return None
        if self._normalize_name(away_team) != self._normalize_name(str(bet.get('away_team') or '')):
            return None
        sport_key = str(override.get('sport_key') or '')
        if sport_key and self._normalize_name(sport_key) != self._normalize_name(str(bet.get('sport_key') or '')):
            return None
        league_name = str(override.get('league_name') or '')
        if league_name and self._normalize_name(league_name) != self._normalize_name(str(bet.get('league_name') or '')):
            return None
        commence_time = str(override.get('commence_time') or '')
        if commence_time:
            if self._safe_date(commence_time) != self._safe_date(str(bet.get('commence_time') or '')):
                return None
        return 'teams'

    def _build_manual_override_row(self, bet: dict[str, Any], override: dict[str, Any], match_mode: str) -> dict[str, Any]:
        return {
            '_settlement_source': 'manual_override',
            '_manual_override_match_mode': match_mode,
            '_manual_override_note': str(override.get('note') or ''),
            'utcDate': str(override.get('commence_time') or bet.get('commence_time') or ''),
            'status': 'FINISHED',
            'competition': {'name': str(override.get('league_name') or bet.get('league_name') or '')},
            'homeTeam': {'name': str(override.get('home_team') or bet.get('home_team') or '')},
            'awayTeam': {'name': str(override.get('away_team') or bet.get('away_team') or '')},
            'score': {
                'fullTime': {
                    'home': float(override.get('home_goals') or 0.0),
                    'away': float(override.get('away_goals') or 0.0),
                }
            },
        }

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
    def _rows_by_source(rows: list[dict[str, Any]]) -> dict[str, int]:
        counts: defaultdict[str, int] = defaultdict(int)
        for row in rows:
            counts[str(row.get('_settlement_source') or 'unknown')] += 1
        return dict(counts)

    def _row_debug(self, *, row: dict[str, Any], score: float, has_score: bool) -> dict[str, Any]:
        return {
            'source': str(row.get('_settlement_source') or 'unknown'),
            'score': round(float(score), 2),
            'has_score': bool(has_score),
            'status': self._row_status(row),
            'event': self._row_event_label(row),
            'event_start': self._extract_start(row).isoformat() if self._extract_start(row) is not None else None,
            'league': self._extract_league_name(row),
            'scoreline': self._row_scoreline(row),
        }

    @staticmethod
    def _compact_debug_entry(item: dict[str, Any]) -> dict[str, Any]:
        compact = {
            'home_team': item.get('home_team'),
            'away_team': item.get('away_team'),
            'family': item.get('family'),
            'selection': item.get('selection'),
            'point': item.get('point'),
            'reason': item.get('reason'),
            'best_score': item.get('best_score'),
            'best_source': item.get('best_source'),
            'best_status': item.get('best_status'),
            'best_event': item.get('best_event'),
            'best_scoreline': item.get('best_scoreline'),
        }
        top_candidates = item.get('top_candidates')
        if isinstance(top_candidates, list) and top_candidates:
            compact['top_candidates'] = top_candidates[:2]
        return compact

    def _row_event_label(self, row: dict[str, Any] | None) -> str | None:
        if not isinstance(row, dict):
            return None
        home = self._extract_team_name(row, 'home')
        away = self._extract_team_name(row, 'away')
        if not home and not away:
            return None
        return f'{home} vs {away}'

    def _row_scoreline(self, row: dict[str, Any] | None) -> str | None:
        if not isinstance(row, dict):
            return None
        home_goals = self._extract_result(row, 'home')
        away_goals = self._extract_result(row, 'away')
        if home_goals is None or away_goals is None:
            return None
        return f'{int(home_goals)}:{int(away_goals)}'

    @staticmethod
    def _row_status(row: dict[str, Any] | None) -> str | None:
        if not isinstance(row, dict):
            return None
        status = row.get('statusName')
        if status not in (None, ''):
            return str(status)
        raw = row.get('status')
        if raw not in (None, ''):
            return str(raw)
        return None

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

    @staticmethod
    def _coerce_goal(value: Any) -> float | None:
        if value in (None, ''):
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_name(value: str) -> str:
        return ' '.join(str(value or '').strip().casefold().split())

    @staticmethod
    def _safe_date(value: str) -> str | None:
        if value in (None, ''):
            return None
        try:
            return parse_datetime(str(value)).astimezone(UTC).date().isoformat()
        except Exception:
            return None
