from __future__ import annotations

import os
import re
from datetime import timezone
from typing import Any, Iterable

from app.schemas import Match, MatchContext, Offer
from app.utils import parse_datetime

UTC = timezone.utc
_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        return float(str(value).strip().replace(',', '.'))
    except Exception:
        return None


def _valid_price(value: Any) -> float | None:
    price = _to_float(value)
    if price is None:
        return None
    if 1.01 <= price <= 35.0:
        return round(price, 4)
    return None


def _norm_key(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')


def _flatten(value: Any, prefix: str = '', depth: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if depth > 5:
        return out
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key or '').strip()
            if not key_text:
                continue
            full = f'{prefix}.{key_text}' if prefix else key_text
            if isinstance(item, dict):
                out.update(_flatten(item, full, depth + 1))
            elif isinstance(item, list):
                out[full] = item
                for idx, child in enumerate(item[:8]):
                    if isinstance(child, dict):
                        out.update(_flatten(child, f'{full}.{idx}', depth + 1))
            else:
                out[full] = item
    return out


def _best_value(flat: dict[str, Any], patterns: Iterable[str]) -> Any:
    norm_map = {_norm_key(key): value for key, value in flat.items()}
    for pattern in patterns:
        key = _norm_key(pattern)
        if key in norm_map:
            return norm_map[key]
    for pattern in patterns:
        needle = _norm_key(pattern)
        for key, value in norm_map.items():
            if needle in key:
                return value
    return None


def _point_from_token(token: str) -> float | None:
    token = str(token or '').lower().replace('_', '').replace('-', '').replace('.', '')
    mapping = {'05': 0.5, '15': 1.5, '25': 2.5, '35': 3.5, '45': 4.5, '55': 5.5}
    if token in mapping:
        return mapping[token]
    if len(token) == 2 and token.endswith('5'):
        try:
            return float(token[0] + '.5')
        except Exception:
            return None
    return None


def _prob_pct(value: Any) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    if number > 1.0:
        number /= 100.0
    if 0.01 <= number <= 0.99:
        return round(number, 5)
    return None


def _add_hint(hints: list[dict[str, Any]], *, source: str, bookmaker: str, family: str, selection: str, price: Any, point: float | None = None, team_side: str | None = None, key: str = '') -> None:
    price_f = _valid_price(price)
    if price_f is None:
        return
    ident = (source, bookmaker, family, selection, point, team_side, price_f)
    if any(item.get('_ident') == ident for item in hints):
        return
    hints.append({
        '_ident': ident,
        'source': source,
        'bookmaker': bookmaker,
        'family': family,
        'selection': selection,
        'price': price_f,
        'point': point,
        'team_side': team_side,
        'market_name': key,
        'market_key': family,
    })


def _event_like_payloads(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return [payload]
    out: list[Any] = []
    event = payload.get('event')
    if isinstance(event, dict):
        out.append(event)
    prediction = payload.get('prediction')
    if isinstance(prediction, dict):
        pred_event = prediction.get('event')
        if isinstance(pred_event, dict):
            out.append(pred_event)
        out.append(prediction)
    out.append(payload)
    return out


def _mine_odds_hints(payload: Any, *, source: str, bookmaker: str) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    payloads = _event_like_payloads(payload) if source == 'bzzoiro' else [payload]
    for item in payloads:
        flat = _flatten(item)
        _add_hint(hints, source=source, bookmaker=bookmaker, family='h2h', selection='home', price=_best_value(flat, ('odds_home', 'home_odds', 'home_win_odds', 'odds_1')), key='h2h_home')
        _add_hint(hints, source=source, bookmaker=bookmaker, family='h2h', selection='draw', price=_best_value(flat, ('odds_draw', 'draw_odds', 'odds_x')), key='h2h_draw')
        _add_hint(hints, source=source, bookmaker=bookmaker, family='h2h', selection='away', price=_best_value(flat, ('odds_away', 'away_odds', 'away_win_odds', 'odds_2')), key='h2h_away')
        for raw_key, value in flat.items():
            key = _norm_key(raw_key)
            if 'odds' not in key and not key.endswith('_price'):
                continue
            if any(token in key for token in ('home_win', 'away_win', 'odds_home', 'odds_away', 'draw')):
                continue
            over = re.search(r'(?:odds_)?over_?(0?5|1?5|2?5|3?5|4?5|5?5)', key)
            under = re.search(r'(?:odds_)?under_?(0?5|1?5|2?5|3?5|4?5|5?5)', key)
            if over:
                point = _point_from_token(over.group(1))
                if point is not None:
                    _add_hint(hints, source=source, bookmaker=bookmaker, family='totals', selection='Over', price=value, point=point, key=raw_key)
                continue
            if under:
                point = _point_from_token(under.group(1))
                if point is not None:
                    _add_hint(hints, source=source, bookmaker=bookmaker, family='totals', selection='Under', price=value, point=point, key=raw_key)
                continue
            if 'btts' in key or 'both_teams' in key:
                if 'yes' in key:
                    _add_hint(hints, source=source, bookmaker=bookmaker, family='btts', selection='Yes', price=value, key=raw_key)
                elif 'no' in key:
                    _add_hint(hints, source=source, bookmaker=bookmaker, family='btts', selection='No', price=value, key=raw_key)
    for item in hints:
        item.pop('_ident', None)
    return hints


def _mine_metrics(payload: Any) -> dict[str, Any]:
    flat = _flatten(payload)
    aliases = {
        'expected_home_goals': ('expected_home_goals', 'home_expected_goals', 'home_xg', 'xg_home', 'actual_home_xg', 'home_xg_live'),
        'expected_away_goals': ('expected_away_goals', 'away_expected_goals', 'away_xg', 'xg_away', 'actual_away_xg', 'away_xg_live'),
        'prob_home_win': ('prob_home_win', 'home_win_probability', 'home_probability', 'prob_1'),
        'prob_draw': ('prob_draw', 'draw_probability', 'prob_x'),
        'prob_away_win': ('prob_away_win', 'away_win_probability', 'away_probability', 'prob_2'),
        'prob_over_1_5': ('prob_over_15', 'prob_over_1_5', 'over15_probability', 'over_1_5_probability'),
        'prob_over_2_5': ('prob_over_25', 'prob_over_2_5', 'over25_probability', 'over_2_5_probability'),
        'prob_over_3_5': ('prob_over_35', 'prob_over_3_5', 'over35_probability', 'over_3_5_probability'),
        'prob_btts_yes': ('prob_btts_yes', 'btts_yes_probability', 'both_teams_to_score_yes_probability'),
        'provider_confidence': ('confidence', 'model_confidence', 'prediction_confidence'),
        'home_shots': ('home_shots', 'shots_home', 'home_total_shots'),
        'away_shots': ('away_shots', 'shots_away', 'away_total_shots'),
        'home_corners': ('home_corners', 'corners_home'),
        'away_corners': ('away_corners', 'corners_away'),
        'home_possession': ('home_possession', 'possession_home'),
        'away_possession': ('away_possession', 'possession_away'),
        'travel_distance_km': ('travel_distance_km',),
        'temperature_c': ('temperature_c',),
        'wind_speed': ('wind_speed',),
    }
    metrics: dict[str, Any] = {}
    for name, keys in aliases.items():
        raw = _best_value(flat, keys)
        value = _prob_pct(raw) if name.startswith('prob_') else _to_float(raw)
        if value is not None:
            metrics[name] = value
    for key in ('most_likely_score', 'favorite', 'model_version', 'pitch_condition', 'is_local_derby', 'is_neutral_ground'):
        raw = _best_value(flat, (key,))
        if raw not in (None, ''):
            metrics[key] = raw
    if metrics.get('expected_home_goals') is not None and metrics.get('expected_away_goals') is not None:
        metrics['expected_total_goals'] = round(float(metrics['expected_home_goals']) + float(metrics['expected_away_goals']), 4)
    return metrics


def _is_sstats_current_event(payload: Any, match: Match | None) -> bool:
    if match is None or not isinstance(payload, dict):
        return False
    details = payload.get('details') if isinstance(payload.get('details'), dict) else {}
    mode = str(payload.get('sstats_mode') or details.get('sstats_mode') or '').lower()
    if 'form' in mode or 'team_form' in mode or 'historical' in mode:
        return False
    status_text = ' '.join(str(payload.get(k) or '') for k in ('status', 'statusName', 'period')).lower()
    if any(term in status_text for term in ('finished', 'ft', 'aet', 'after penalties')):
        return False
    date_raw = payload.get('date') or payload.get('event_date') or payload.get('start') or payload.get('kickoff')
    if not date_raw:
        return False
    try:
        event_dt = parse_datetime(str(date_raw))
        if event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=UTC)
        hours = abs((event_dt.astimezone(UTC) - match.commence_time.astimezone(UTC)).total_seconds()) / 3600.0
        return hours <= 6.0
    except Exception:
        return False


def _enhance_context(context: MatchContext, provider_hint: str | None = None, match: Match | None = None) -> MatchContext:
    source_text = (provider_hint or str(getattr(context, 'source', '') or '')).lower()
    if 'bzzoiro' in source_text:
        source = 'bzzoiro'; bookmaker = 'Bzzoiro'; allow_odds = True
    elif 'sstats' in source_text:
        source = 'sstats'; bookmaker = 'SStats'; allow_odds = _is_sstats_current_event(getattr(context, 'payload', {}) or {}, match)
    else:
        return context
    payloads = [getattr(context, 'payload', {}) or {}, getattr(context, 'details', {}) or {}]
    details = dict(getattr(context, 'details', {}) or {})
    odds_hints: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    for payload in payloads:
        metrics.update(_mine_metrics(payload))
        if allow_odds:
            odds_hints.extend(_mine_odds_hints(payload, source=source, bookmaker=bookmaker))
    if odds_hints:
        details['provider_odds_hints'] = odds_hints
        details['provider_odds_hints_count'] = len(odds_hints)
        details['provider_odds_hints_current_event_only'] = True
    else:
        details.pop('provider_odds_hints', None)
        details['provider_odds_hints_count'] = 0
    if metrics:
        details['provider_metric_hints'] = metrics
        details['provider_metric_hints_count'] = len(metrics)
        if context.expected_home is None and metrics.get('expected_home_goals') is not None:
            context.expected_home = float(metrics['expected_home_goals'])
        if context.expected_away is None and metrics.get('expected_away_goals') is not None:
            context.expected_away = float(metrics['expected_away_goals'])
        if context.home_win_probability is None and metrics.get('prob_home_win') is not None:
            context.home_win_probability = float(metrics['prob_home_win'])
        if context.away_win_probability is None and metrics.get('prob_away_win') is not None:
            context.away_win_probability = float(metrics['prob_away_win'])
    context.details = details
    return context


def _iter_contexts(value: Any) -> Iterable[MatchContext]:
    if isinstance(value, MatchContext):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_contexts(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_contexts(item)


def _offer_from_hint(hint: dict[str, Any], match: Match | None) -> Offer | None:
    family = str(hint.get('family') or '').strip()
    if family not in {'h2h', 'totals', 'btts', 'spreads', 'teamTotals', 'doubleChance', 'dnb'}:
        return None
    price = _valid_price(hint.get('price'))
    if price is None:
        return None
    selection = str(hint.get('selection') or '').strip()
    team_side = str(hint.get('team_side') or '').strip().lower() or None
    if match is not None and family == 'h2h':
        low = selection.lower()
        if low in {'home', '1'}:
            selection = match.home_team
            team_side = 'home'
        elif low in {'away', '2'}:
            selection = match.away_team
            team_side = 'away'
        elif low in {'draw', 'x'}:
            selection = 'draw'
    if not selection:
        return None
    return Offer(
        source=str(hint.get('source') or 'provider_payload'),
        bookmaker=str(hint.get('bookmaker') or hint.get('source') or 'ProviderPayload'),
        family=family,  # type: ignore[arg-type]
        selection=selection,
        price=float(price),
        point=_to_float(hint.get('point')),
        team_side=team_side,
        market_name=str(hint.get('market_name') or ''),
        market_key=str(hint.get('market_key') or family),
        metadata={'provider_payload_mined': True, 'provider_payload_mining_v2': True, 'raw_hint': dict(hint)},
    )


def _patch_provider_contexts() -> None:
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider
        if not getattr(BzzoiroContextProvider, '_harizon_payload_mining_v2_patch', False):
            original = BzzoiroContextProvider.fetch_context
            async def wrapped(self, matches):  # type: ignore[no-untyped-def]
                contexts, stats, preview = await original(self, matches)
                odds = metrics = 0
                for context in contexts.values():
                    _enhance_context(context, 'bzzoiro')
                    odds += int((context.details or {}).get('provider_odds_hints_count') or 0)
                    metrics += int((context.details or {}).get('provider_metric_hints_count') or 0)
                stats['provider_odds_hints'] = odds
                stats['provider_metric_hints'] = metrics
                stats['provider_payload_mining_v2'] = True
                preview['provider_payload_mining_v2'] = {'odds_hints': odds, 'metric_hints': metrics, 'odds_policy': 'bzzoiro_current_event_odds_allowed'}
                return contexts, stats, preview
            BzzoiroContextProvider.fetch_context = wrapped
            BzzoiroContextProvider._harizon_payload_mining_v2_patch = True
    except Exception:
        pass
    try:
        from app.providers.sstats import SStatsContextProvider
        if not getattr(SStatsContextProvider, '_harizon_payload_mining_v2_patch', False):
            original = SStatsContextProvider.fetch_context
            async def wrapped(self, matches):  # type: ignore[no-untyped-def]
                contexts, stats, preview = await original(self, matches)
                metrics = 0
                for context in contexts.values():
                    _enhance_context(context, 'sstats', None)
                    metrics += int((context.details or {}).get('provider_metric_hints_count') or 0)
                stats['provider_odds_hints'] = 0
                stats['provider_metric_hints'] = metrics
                stats['provider_payload_mining_v2'] = True
                stats['sstats_historical_odds_not_used_as_lines'] = True
                preview['provider_payload_mining_v2'] = {'odds_hints': 0, 'metric_hints': metrics, 'odds_policy': 'historical_sstats_odds_blocked'}
                return contexts, stats, preview
            SStatsContextProvider.fetch_context = wrapped
            SStatsContextProvider._harizon_payload_mining_v2_patch = True
    except Exception:
        pass


def _patch_candidate_factory() -> None:
    if not _truthy(os.getenv('CONTEXT_ODDS_HINTS_AS_SECONDARY_OFFERS'), True):
        return
    try:
        from app.services.model import CandidateFactory
    except Exception:
        return
    if getattr(CandidateFactory, '_harizon_context_odds_hints_v2_patch', False):
        return
    original = CandidateFactory.build_candidates
    def patched(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        by_key = {m.match_key: m for m in matches or []}
        merged: dict[str, list[Offer]] = {str(k): list(v or []) for k, v in dict(offers_by_match or {}).items()}
        added = 0
        by_source: dict[str, int] = {}
        try:
            for book in ('Bzzoiro', 'SStats'):
                self.target_books.add(self._norm_book(book))
                self.consensus_books.add(self._norm_book(book))
        except Exception:
            pass
        for match_key, value in dict(contexts_by_match or {}).items():
            match = by_key.get(str(match_key))
            for context in _iter_contexts(value):
                _enhance_context(context, None, match)
                for hint in (context.details or {}).get('provider_odds_hints') or []:
                    source = str(hint.get('source') or '').lower()
                    # Bzzoiro event odds are current-match odds. SStats odds are accepted only if explicitly marked current-event by _enhance_context.
                    if source not in {'bzzoiro', 'sstats'}:
                        continue
                    offer = _offer_from_hint(hint, match)
                    if offer is None:
                        continue
                    merged.setdefault(str(match_key), []).append(offer)
                    added += 1
                    by_source[source] = by_source.get(source, 0) + 1
        candidates, rejections, debug = original(self, matches, merged, contexts_by_match, market_signals_by_match=market_signals_by_match)
        debug = dict(debug or {})
        debug['provider_payload_mining_v2'] = {
            'context_odds_hints_as_secondary_offers': True,
            'secondary_offers_added': added,
            'secondary_offers_by_source': by_source,
            'sstats_historical_odds_blocked': True,
        }
        return candidates, rejections, debug
    CandidateFactory.build_candidates = patched
    CandidateFactory._harizon_context_odds_hints_v2_patch = True


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    os.environ['CONTEXT_ENRICHMENT_REQUIRES_OFFERS'] = 'false'
    os.environ.setdefault('CONTEXT_ODDS_HINTS_AS_SECONDARY_OFFERS', 'true')
    _patch_provider_contexts()
    _patch_candidate_factory()
    _INSTALLED = True
    return {'status': 'installed', 'patch': 'provider_payload_mining_runtime_patch_v2'}
