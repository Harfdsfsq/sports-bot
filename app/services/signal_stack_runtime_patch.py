from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.schemas import Match, MatchContext, Offer
from app.utils import canonicalize_league_name, canonicalize_team_name, score_event_match

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / '.data' / 'exports'
SNAPSHOT_PATH = ROOT / '.data' / 'odds_movement_snapshots.jsonl'
LATEST_SNAPSHOT_REPORT = EXPORT_DIR / 'latest-odds-movement-snapshot-report.json'
LATEST_SIGNAL_STACK = EXPORT_DIR / 'latest-signal-stack-runtime.json'
LATEST_NEWS_SHORTLIST = EXPORT_DIR / 'latest-news-injury-shortlist.json'
LATEST_SECONDARY_MATCHING = EXPORT_DIR / 'latest-secondary-provider-matching.json'
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
        result = float(str(value).strip().replace(',', '.'))
        if math.isfinite(result):
            return result
    except Exception:
        return None
    return None


def _valid_price(value: Any) -> float | None:
    price = _to_float(value)
    if price is None:
        return None
    if 1.01 <= price <= 50.0:
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
    if 0.001 <= number <= 0.999:
        return round(number, 5)
    return None


def _add_offer_hint(hints: list[dict[str, Any]], *, source: str, bookmaker: str, family: str, selection: str, price: Any, point: float | None = None, team_side: str | None = None, key: str = '') -> None:
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


def _mine_bzzoiro_current_odds(payload: Any) -> list[dict[str, Any]]:
    payloads: list[Any] = []
    if isinstance(payload, dict):
        payloads.append(payload)
        event = payload.get('event')
        prediction = payload.get('prediction')
        if isinstance(event, dict):
            payloads.append(event)
        if isinstance(prediction, dict):
            payloads.append(prediction)
            nested_event = prediction.get('event')
            if isinstance(nested_event, dict):
                payloads.append(nested_event)
    else:
        payloads.append(payload)
    hints: list[dict[str, Any]] = []
    for item in payloads:
        flat = _flatten(item)
        _add_offer_hint(hints, source='bzzoiro', bookmaker='Bzzoiro', family='h2h', selection='home', price=_best_value(flat, ('odds_home', 'home_odds', 'odds_1')), key='h2h_home')
        _add_offer_hint(hints, source='bzzoiro', bookmaker='Bzzoiro', family='h2h', selection='draw', price=_best_value(flat, ('odds_draw', 'draw_odds', 'odds_x')), key='h2h_draw')
        _add_offer_hint(hints, source='bzzoiro', bookmaker='Bzzoiro', family='h2h', selection='away', price=_best_value(flat, ('odds_away', 'away_odds', 'odds_2')), key='h2h_away')
        for raw_key, value in flat.items():
            key = _norm_key(raw_key)
            if 'odds' not in key and not key.endswith('_price'):
                continue
            over = re.search(r'(?:odds_)?over_?(0?5|1?5|2?5|3?5|4?5|5?5)', key)
            under = re.search(r'(?:odds_)?under_?(0?5|1?5|2?5|3?5|4?5|5?5)', key)
            if over:
                point = _point_from_token(over.group(1))
                if point is not None:
                    _add_offer_hint(hints, source='bzzoiro', bookmaker='Bzzoiro', family='totals', selection='Over', price=value, point=point, key=raw_key)
                continue
            if under:
                point = _point_from_token(under.group(1))
                if point is not None:
                    _add_offer_hint(hints, source='bzzoiro', bookmaker='Bzzoiro', family='totals', selection='Under', price=value, point=point, key=raw_key)
                continue
            if 'btts' in key or 'both_teams' in key:
                if 'yes' in key:
                    _add_offer_hint(hints, source='bzzoiro', bookmaker='Bzzoiro', family='btts', selection='Yes', price=value, key=raw_key)
                elif 'no' in key:
                    _add_offer_hint(hints, source='bzzoiro', bookmaker='Bzzoiro', family='btts', selection='No', price=value, key=raw_key)
    for item in hints:
        item.pop('_ident', None)
    return hints


def _mine_metrics(payload: Any) -> dict[str, Any]:
    flat = _flatten(payload)
    aliases = {
        'expected_home_goals': ('expected_home_goals', 'home_expected_goals', 'home_xg', 'xg_home', 'actual_home_xg'),
        'expected_away_goals': ('expected_away_goals', 'away_expected_goals', 'away_xg', 'xg_away', 'actual_away_xg'),
        'prob_home_win': ('prob_home_win', 'home_win_probability', 'home_probability', 'prob_1'),
        'prob_draw': ('prob_draw', 'draw_probability', 'prob_x'),
        'prob_away_win': ('prob_away_win', 'away_win_probability', 'away_probability', 'prob_2'),
        'prob_over_1_5': ('prob_over_15', 'prob_over_1_5', 'over15_probability', 'over_1_5_probability'),
        'prob_over_2_5': ('prob_over_25', 'prob_over_2_5', 'over25_probability', 'over_2_5_probability'),
        'prob_over_3_5': ('prob_over_35', 'prob_over_3_5', 'over35_probability', 'over_3_5_probability'),
        'prob_btts_yes': ('prob_btts_yes', 'btts_yes_probability', 'both_teams_to_score_yes_probability'),
        'provider_confidence': ('confidence', 'model_confidence', 'prediction_confidence'),
        'shots_for': ('shots_for', 'home_shots', 'shots_home', 'team_shots'),
        'shots_against': ('shots_against', 'away_shots', 'shots_away', 'opponent_shots'),
        'corners_for': ('corners_for', 'home_corners', 'corners_home'),
        'corners_against': ('corners_against', 'away_corners', 'corners_away'),
        'possession': ('possession', 'home_possession', 'possession_home'),
    }
    metrics: dict[str, Any] = {}
    for name, keys in aliases.items():
        raw = _best_value(flat, keys)
        value = _prob_pct(raw) if name.startswith('prob_') else _to_float(raw)
        if value is not None:
            metrics[name] = value
    if metrics.get('expected_home_goals') is not None and metrics.get('expected_away_goals') is not None:
        metrics['expected_total_goals'] = round(float(metrics['expected_home_goals']) + float(metrics['expected_away_goals']), 4)
    return metrics


def _iter_contexts(value: Any):
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
    price = _valid_price(hint.get('price'))
    if family not in {'h2h', 'totals', 'btts', 'spreads', 'teamTotals', 'doubleChance', 'dnb'} or price is None:
        return None
    selection = str(hint.get('selection') or '').strip()
    team_side = str(hint.get('team_side') or '').strip().lower() or None
    if match is not None and family == 'h2h':
        low = selection.lower()
        if low in {'home', '1'}:
            selection = match.home_team; team_side = 'home'
        elif low in {'away', '2'}:
            selection = match.away_team; team_side = 'away'
        elif low in {'draw', 'x'}:
            selection = 'draw'
    if not selection:
        return None
    return Offer(
        source=str(hint.get('source') or 'provider_payload'),
        bookmaker=str(hint.get('bookmaker') or 'ProviderPayload'),
        family=family,  # type: ignore[arg-type]
        selection=selection,
        price=float(price),
        point=_to_float(hint.get('point')),
        team_side=team_side,
        market_name=str(hint.get('market_name') or ''),
        market_key=str(hint.get('market_key') or family),
        metadata={'signal_stack_mined': True, 'raw_hint': dict(hint)},
    )


def _enhance_context(context: MatchContext) -> None:
    payload = getattr(context, 'payload', {}) or {}
    details = dict(getattr(context, 'details', {}) or {})
    source = str(getattr(context, 'source', '') or '').lower()
    metrics = _mine_metrics({'payload': payload, 'details': details})
    if 'bzzoiro' in source:
        hints = _mine_bzzoiro_current_odds({'payload': payload, 'details': details})
        details['provider_odds_hints'] = hints
        details['provider_odds_hints_count'] = len(hints)
        details['provider_odds_hints_policy'] = 'current_event_bzzoiro_only'
    elif 'sstats' in source:
        # SStats odds are generally historical rows. Keep them out of current lines.
        details['provider_odds_hints'] = []
        details['provider_odds_hints_count'] = 0
        details['sstats_historical_odds_not_used_as_lines'] = True
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


def _append_snapshots(matches: list[Match], offers_by_match: dict[str, list[Offer]]) -> dict[str, Any]:
    if not _truthy(os.getenv('ODDS_MOVEMENT_SNAPSHOTS_ENABLED'), True):
        return {'enabled': False}
    now = datetime.now(UTC).isoformat()
    match_by_key = {m.match_key: m for m in matches or []}
    rows: list[dict[str, Any]] = []
    for match_key, offers in dict(offers_by_match or {}).items():
        match = match_by_key.get(str(match_key))
        for offer in offers or []:
            try:
                rows.append({
                    'captured_at_utc': now,
                    'match_key': str(match_key),
                    'league_name': getattr(match, 'league_name', '') if match else '',
                    'home_team': getattr(match, 'home_team', '') if match else '',
                    'away_team': getattr(match, 'away_team', '') if match else '',
                    'commence_time': getattr(match, 'commence_time', '').isoformat() if match else '',
                    'source': offer.source,
                    'bookmaker': offer.bookmaker,
                    'family': offer.family,
                    'selection': offer.selection,
                    'point': offer.point,
                    'price': offer.price,
                    'market_key': offer.market_key,
                    'market_name': offer.market_name,
                })
            except Exception:
                continue
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with SNAPSHOT_PATH.open('a', encoding='utf-8') as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    report = {'enabled': True, 'snapshots_added': len(rows), 'path': str(SNAPSHOT_PATH.relative_to(ROOT))}
    LATEST_SNAPSHOT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    LATEST_SNAPSHOT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return report


def _secondary_matching_audit(matches: list[Match], offers_by_match: dict[str, list[Offer]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    combo: dict[str, int] = {}
    for key, offers in dict(offers_by_match or {}).items():
        sources = sorted({str(o.source) for o in offers or [] if str(o.source or '').strip()})
        for source in sources:
            counts[source] = counts.get(source, 0) + 1
        if sources:
            joined = '+'.join(sources)
            combo[joined] = combo.get(joined, 0) + 1
    payload = {
        'matches_seen': len(matches or []),
        'matches_with_offers': len([k for k, v in dict(offers_by_match or {}).items() if v]),
        'offer_sources_match_counts': dict(sorted(counts.items(), key=lambda i: (-i[1], i[0]))),
        'offer_source_combinations': dict(sorted(combo.items(), key=lambda i: (-i[1], i[0]))[:30]),
    }
    LATEST_SECONDARY_MATCHING.parent.mkdir(parents=True, exist_ok=True)
    LATEST_SECONDARY_MATCHING.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return payload


def _write_news_shortlist(candidates: list[Any]) -> dict[str, Any]:
    limit = max(0, int(float(os.getenv('NEWS_INJURY_SHORTLIST_LIMIT') or 8)))
    rows = []
    for cand in sorted(candidates or [], key=lambda c: float(getattr(c, 'publication_score', 0.0) or 0.0), reverse=True)[:limit]:
        rows.append({
            'match_key': getattr(cand, 'match_key', ''),
            'league_name': getattr(cand, 'league_name', ''),
            'home_team': getattr(cand, 'home_team', ''),
            'away_team': getattr(cand, 'away_team', ''),
            'family': getattr(cand, 'family', ''),
            'selection': getattr(cand, 'selection', ''),
            'edge_pct': getattr(cand, 'edge_pct', None),
            'ev_pct': getattr(cand, 'ev_pct', None),
            'confidence': getattr(cand, 'confidence', None),
            'query': f"{getattr(cand, 'home_team', '')} {getattr(cand, 'away_team', '')} injury team news lineup football",
        })
    payload = {'enabled': True, 'limit': limit, 'items': rows}
    LATEST_NEWS_SHORTLIST.parent.mkdir(parents=True, exist_ok=True)
    LATEST_NEWS_SHORTLIST.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return payload


def _patch_candidate_factory() -> None:
    try:
        from app.services.model import CandidateFactory
    except Exception:
        return
    if getattr(CandidateFactory, '_harizon_signal_stack_patch', False):
        return
    original = CandidateFactory.build_candidates

    def patched(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        match_by_key = {m.match_key: m for m in matches or []}
        merged: dict[str, list[Offer]] = {str(k): list(v or []) for k, v in dict(offers_by_match or {}).items()}
        added_offers = 0
        metric_contexts = 0
        try:
            for book in ('Bzzoiro',):
                self.target_books.add(self._norm_book(book))
                self.consensus_books.add(self._norm_book(book))
        except Exception:
            pass
        for match_key, value in dict(contexts_by_match or {}).items():
            match = match_by_key.get(str(match_key))
            for context in _iter_contexts(value):
                _enhance_context(context)
                if (context.details or {}).get('provider_metric_hints_count'):
                    metric_contexts += 1
                for hint in (context.details or {}).get('provider_odds_hints') or []:
                    offer = _offer_from_hint(hint, match)
                    if offer is None:
                        continue
                    merged.setdefault(str(match_key), []).append(offer)
                    added_offers += 1
        snapshot_report = _append_snapshots(list(matches or []), merged)
        matching_report = _secondary_matching_audit(list(matches or []), merged)
        candidates, rejections, debug = original(self, matches, merged, contexts_by_match, market_signals_by_match=market_signals_by_match)
        news_shortlist = _write_news_shortlist(candidates)
        debug = dict(debug or {})
        debug['signal_stack_runtime_patch'] = {
            'enabled': True,
            'bzzoiro_secondary_offers_added': added_offers,
            'metric_contexts_enhanced': metric_contexts,
            'odds_snapshot_report': snapshot_report,
            'secondary_matching': matching_report,
            'news_shortlist_count': len(news_shortlist.get('items') or []),
            'guards_relaxed': False,
        }
        payload = debug['signal_stack_runtime_patch']
        LATEST_SIGNAL_STACK.parent.mkdir(parents=True, exist_ok=True)
        LATEST_SIGNAL_STACK.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        return candidates, rejections, debug

    CandidateFactory.build_candidates = patched
    CandidateFactory._harizon_signal_stack_patch = True


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    os.environ.setdefault('ODDS_MOVEMENT_SNAPSHOTS_ENABLED', 'true')
    os.environ.setdefault('NEWS_INJURY_SHORTLIST_ENABLED', 'true')
    os.environ.setdefault('NEWS_INJURY_SHORTLIST_LIMIT', '8')
    _patch_candidate_factory()
    _INSTALLED = True
    return {'status': 'installed', 'patch': 'signal_stack_runtime_patch'}
