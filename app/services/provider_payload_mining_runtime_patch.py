from __future__ import annotations

import os
import re
from collections import defaultdict
from typing import Any, Iterable

from app.schemas import Match, MatchContext, Offer

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
        text = str(value).strip().replace(',', '.')
        if not text:
            return None
        value_f = float(text)
        return value_f
    except Exception:
        return None


def _valid_price(value: Any) -> float | None:
    price = _to_float(value)
    if price is None:
        return None
    if 1.01 <= price <= 35.0:
        return round(price, 4)
    return None


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
                # Keep a light preview but avoid exploding large payloads.
                out[full] = item
                for idx, child in enumerate(item[:6]):
                    if isinstance(child, dict):
                        out.update(_flatten(child, f'{full}.{idx}', depth + 1))
            else:
                out[full] = item
    return out


def _norm_key(key: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', str(key or '').strip().lower()).strip('_')


def _point_from_token(token: str) -> float | None:
    token = str(token or '').lower().replace('_', '').replace('-', '').replace('.', '')
    mapping = {'05': 0.5, '15': 1.5, '25': 2.5, '35': 3.5, '45': 4.5, '55': 5.5}
    if token in mapping:
        return mapping[token]
    try:
        if len(token) == 2 and token.endswith('5'):
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


def _best_value(flat: dict[str, Any], patterns: Iterable[str]) -> Any:
    norm_map = {_norm_key(key): value for key, value in flat.items()}
    for pattern in patterns:
        norm = _norm_key(pattern)
        if norm in norm_map:
            return norm_map[norm]
    for pattern in patterns:
        pattern_norm = _norm_key(pattern)
        for key, value in norm_map.items():
            if pattern_norm in key:
                return value
    return None


def _add_offer(offers: list[dict[str, Any]], *, source: str, bookmaker: str, family: str, selection: str, price: Any, point: float | None = None, team_side: str | None = None, key: str = '') -> None:
    price_f = _valid_price(price)
    if price_f is None:
        return
    ident = (source, bookmaker, family, selection, point, team_side, price_f)
    for existing in offers:
        if existing.get('_ident') == ident:
            return
    offers.append({
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


def _mine_odds_hints_from_payload(payload: Any, source: str, bookmaker: str) -> list[dict[str, Any]]:
    flat = _flatten(payload)
    offers: list[dict[str, Any]] = []

    # H2H / 1X2. Bzzoiro events commonly expose odds_home/odds_draw/odds_away.
    _add_offer(offers, source=source, bookmaker=bookmaker, family='h2h', selection='home', price=_best_value(flat, ('odds_home', 'home_odds', 'home_win_odds', 'odds_1')), key='h2h_home')
    _add_offer(offers, source=source, bookmaker=bookmaker, family='h2h', selection='draw', price=_best_value(flat, ('odds_draw', 'draw_odds', 'odds_x')), key='h2h_draw')
    _add_offer(offers, source=source, bookmaker=bookmaker, family='h2h', selection='away', price=_best_value(flat, ('odds_away', 'away_odds', 'away_win_odds', 'odds_2')), key='h2h_away')

    # Totals and BTTS by key scan. Supports odds_over_25, over_2_5_odds, total_over_2_5, etc.
    for raw_key, value in flat.items():
        key = _norm_key(raw_key)
        if 'odds' not in key and not key.endswith('_price'):
            continue
        if any(token in key for token in ('home', 'away', 'draw')):
            continue
        over_match = re.search(r'(?:odds_)?over_?(0?5|1?5|2?5|3?5|4?5|5?5)', key)
        under_match = re.search(r'(?:odds_)?under_?(0?5|1?5|2?5|3?5|4?5|5?5)', key)
        if over_match:
            point = _point_from_token(over_match.group(1))
            if point is not None:
                _add_offer(offers, source=source, bookmaker=bookmaker, family='totals', selection='Over', price=value, point=point, key=raw_key)
                continue
        if under_match:
            point = _point_from_token(under_match.group(1))
            if point is not None:
                _add_offer(offers, source=source, bookmaker=bookmaker, family='totals', selection='Under', price=value, point=point, key=raw_key)
                continue
        if 'btts' in key or 'both_teams' in key:
            if 'yes' in key:
                _add_offer(offers, source=source, bookmaker=bookmaker, family='btts', selection='Yes', price=value, key=raw_key)
            elif 'no' in key:
                _add_offer(offers, source=source, bookmaker=bookmaker, family='btts', selection='No', price=value, key=raw_key)

    for row in offers:
        row.pop('_ident', None)
    return offers


def _mine_metric_hints(payload: Any) -> dict[str, Any]:
    flat = _flatten(payload)
    metrics: dict[str, Any] = {}
    aliases = {
        'expected_home_goals': ('expected_home_goals', 'home_expected_goals', 'home_xg', 'xg_home', 'home_team_xg'),
        'expected_away_goals': ('expected_away_goals', 'away_expected_goals', 'away_xg', 'xg_away', 'away_team_xg'),
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
    }
    for name, keys in aliases.items():
        value = _best_value(flat, keys)
        if name.startswith('prob_'):
            value = _prob_pct(value)
        else:
            value = _to_float(value)
        if value is not None:
            metrics[name] = value
    for key in ('most_likely_score', 'favorite', 'model_version'):
        value = _best_value(flat, (key,))
        if value not in (None, ''):
            metrics[key] = value
    if metrics.get('expected_home_goals') is not None and metrics.get('expected_away_goals') is not None:
        metrics['expected_total_goals'] = round(float(metrics['expected_home_goals']) + float(metrics['expected_away_goals']), 4)
    return metrics


def _iter_contexts(value: Any) -> Iterable[MatchContext]:
    if isinstance(value, MatchContext):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_contexts(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_contexts(item)


def _enhance_context(context: MatchContext, provider_hint: str | None = None) -> MatchContext:
    source = provider_hint or str(getattr(context, 'source', '') or '').lower()
    if 'bzzoiro' in source:
        offer_source = 'bzzoiro'
        bookmaker = 'Bzzoiro'
    elif 'sstats' in source:
        offer_source = 'sstats'
        bookmaker = 'SStats'
    else:
        return context
    payloads: list[Any] = [getattr(context, 'payload', {}) or {}, getattr(context, 'details', {}) or {}]
    details = dict(getattr(context, 'details', {}) or {})
    odds_hints: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    for payload in payloads:
        odds_hints.extend(_mine_odds_hints_from_payload(payload, offer_source, bookmaker))
        metrics.update(_mine_metric_hints(payload))
    # Keep old details and add normalized fields used by diagnostics/model tooling.
    if odds_hints:
        details['provider_odds_hints'] = odds_hints
        details['provider_odds_hints_count'] = len(odds_hints)
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


def _offer_from_hint(hint: dict[str, Any]) -> Offer | None:
    try:
        family = str(hint.get('family') or '').strip()
        if family not in {'h2h', 'totals', 'btts', 'spreads', 'teamTotals', 'doubleChance', 'dnb'}:
            return None
        price = _valid_price(hint.get('price'))
        if price is None:
            return None
        return Offer(
            source=str(hint.get('source') or 'provider_payload'),
            bookmaker=str(hint.get('bookmaker') or hint.get('source') or 'ProviderPayload'),
            family=family,  # type: ignore[arg-type]
            selection=str(hint.get('selection') or ''),
            price=float(price),
            point=_to_float(hint.get('point')),
            team_side=str(hint.get('team_side') or '').strip().lower() or None,
            market_name=str(hint.get('market_name') or ''),
            market_key=str(hint.get('market_key') or family),
            metadata={'provider_payload_mined': True, 'raw_hint': dict(hint)},
        )
    except Exception:
        return None


def _patch_provider_contexts() -> None:
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider
        if not getattr(BzzoiroContextProvider, '_harizon_payload_mining_patch', False):
            original_bzz = BzzoiroContextProvider.fetch_context

            async def bzz_fetch_patched(self, matches):  # type: ignore[no-untyped-def]
                contexts, stats, preview = await original_bzz(self, matches)
                hints = 0
                metrics = 0
                for context in contexts.values():
                    _enhance_context(context, 'bzzoiro')
                    hints += int((context.details or {}).get('provider_odds_hints_count') or 0)
                    metrics += int((context.details or {}).get('provider_metric_hints_count') or 0)
                stats['provider_odds_hints'] = hints
                stats['provider_metric_hints'] = metrics
                preview['provider_payload_mining'] = {'odds_hints': hints, 'metric_hints': metrics}
                return contexts, stats, preview

            BzzoiroContextProvider.fetch_context = bzz_fetch_patched
            BzzoiroContextProvider._harizon_payload_mining_patch = True
    except Exception:
        pass

    try:
        from app.providers.sstats import SStatsContextProvider
        if not getattr(SStatsContextProvider, '_harizon_payload_mining_patch', False):
            original_sstats = SStatsContextProvider.fetch_context

            async def sstats_fetch_patched(self, matches):  # type: ignore[no-untyped-def]
                contexts, stats, preview = await original_sstats(self, matches)
                hints = 0
                metrics = 0
                for context in contexts.values():
                    _enhance_context(context, 'sstats')
                    hints += int((context.details or {}).get('provider_odds_hints_count') or 0)
                    metrics += int((context.details or {}).get('provider_metric_hints_count') or 0)
                stats['provider_odds_hints'] = hints
                stats['provider_metric_hints'] = metrics
                preview['provider_payload_mining'] = {'odds_hints': hints, 'metric_hints': metrics}
                return contexts, stats, preview

            SStatsContextProvider.fetch_context = sstats_fetch_patched
            SStatsContextProvider._harizon_payload_mining_patch = True
    except Exception:
        pass


def _patch_candidate_factory() -> None:
    if not _truthy(os.getenv('CONTEXT_ODDS_HINTS_AS_SECONDARY_OFFERS'), True):
        return
    try:
        from app.services.model import CandidateFactory
    except Exception:
        return
    if getattr(CandidateFactory, '_harizon_context_odds_hints_patch', False):
        return
    original = CandidateFactory.build_candidates

    def build_candidates_patched(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        merged: dict[str, list[Offer]] = {str(k): list(v or []) for k, v in dict(offers_by_match or {}).items()}
        added = 0
        allowed_sources = {item.strip().lower() for item in str(os.getenv('CONTEXT_ODDS_HINT_SOURCES') or 'bzzoiro,sstats').split(',') if item.strip()}
        try:
            self.target_books.add(self._norm_book('Bzzoiro'))
            self.target_books.add(self._norm_book('SStats'))
            self.consensus_books.add(self._norm_book('Bzzoiro'))
            self.consensus_books.add(self._norm_book('SStats'))
        except Exception:
            pass
        for match_key, value in dict(contexts_by_match or {}).items():
            for context in _iter_contexts(value):
                _enhance_context(context)
                for hint in (context.details or {}).get('provider_odds_hints') or []:
                    source = str(hint.get('source') or '').lower()
                    if source not in allowed_sources:
                        continue
                    offer = _offer_from_hint(hint)
                    if offer is None:
                        continue
                    merged.setdefault(str(match_key), []).append(offer)
                    added += 1
        result = original(self, matches, merged, contexts_by_match, market_signals_by_match=market_signals_by_match)
        try:
            candidates, rejections, debug = result
            debug = dict(debug or {})
            debug['provider_payload_mining'] = {
                'context_odds_hints_as_secondary_offers': True,
                'secondary_offers_added': added,
                'sources': sorted(allowed_sources),
            }
            return candidates, rejections, debug
        except Exception:
            return result

    CandidateFactory.build_candidates = build_candidates_patched
    CandidateFactory._harizon_context_odds_hints_patch = True


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    _patch_provider_contexts()
    _patch_candidate_factory()
    _INSTALLED = True
    return {'status': 'installed', 'patches': ['provider_context_payload_mining', 'candidate_factory_context_odds_hints']}
