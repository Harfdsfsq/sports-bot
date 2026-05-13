from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / '.data' / 'exports' / 'latest-model-input-market-sanity.json'
_INSTALLED = False

# CandidateFactory is currently configured for safe publication on totals/spreads.
# Keep only soccer lines that the model can price sensibly. This avoids generating
# fake value from exotic/high-total markets such as Under 4.5 @ 3.72.
DEFAULT_TOTAL_LINES = {1.5, 2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5}
DEFAULT_SPREAD_ABS_MAX = 2.5


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def _allowed_total_lines() -> set[float]:
    raw = os.getenv('MODEL_INPUT_ALLOWED_TOTAL_LINES', '')
    if raw.strip():
        vals = {_float(x) for x in raw.replace(';', ',').split(',')}
        return {float(x) for x in vals if x is not None}
    return set(DEFAULT_TOTAL_LINES)


def _family_allowed(family: str) -> bool:
    allowed = {x.strip() for x in os.getenv('MODEL_INPUT_ALLOWED_FAMILIES', 'totals,spreads').split(',') if x.strip()}
    return str(family or '') in allowed


def _is_good_offer(offer: Any, counters: Counter[str], total_lines: set[float]) -> bool:
    family = str(getattr(offer, 'family', '') or '')
    if not _family_allowed(family):
        counters[f'drop_family_{family or "empty"}'] += 1
        return False
    price = _float(getattr(offer, 'price', None), 0.0) or 0.0
    min_price = _float(os.getenv('MODEL_INPUT_MIN_PRICE', '1.50'), 1.50) or 1.50
    max_price = _float(os.getenv('MODEL_INPUT_MAX_PRICE', '3.49'), 3.49) or 3.49
    if price < min_price:
        counters['drop_price_below_min'] += 1
        return False
    if price > max_price:
        counters['drop_price_above_max'] += 1
        return False
    point = _float(getattr(offer, 'point', None), None)
    if family == 'totals':
        if point is None:
            counters['drop_total_no_point'] += 1
            return False
        rounded = round(float(point) * 4.0) / 4.0
        if rounded not in total_lines:
            counters['drop_total_unsupported_line'] += 1
            return False
        # Avoid low-line traps that previously produced wrong-looking Over 1.5 prices.
        if rounded == 1.5 and price > (_float(os.getenv('MODEL_INPUT_TOTAL_15_MAX_PRICE', '1.95'), 1.95) or 1.95):
            counters['drop_total_15_suspicious_price'] += 1
            return False
        return True
    if family == 'spreads':
        if point is None:
            counters['drop_spread_no_point'] += 1
            return False
        abs_max = _float(os.getenv('MODEL_INPUT_SPREAD_ABS_MAX', str(DEFAULT_SPREAD_ABS_MAX)), DEFAULT_SPREAD_ABS_MAX) or DEFAULT_SPREAD_ABS_MAX
        rounded = round(float(point) * 4.0) / 4.0
        if abs(rounded) > abs_max:
            counters['drop_spread_too_wide'] += 1
            return False
        return True
    return True


def _dedupe_best(offers: list[Any]) -> list[Any]:
    best: dict[tuple[Any, ...], Any] = {}
    for offer in offers:
        key = (
            str(getattr(offer, 'source', '') or '').lower(),
            str(getattr(offer, 'bookmaker', '') or '').lower(),
            str(getattr(offer, 'family', '') or ''),
            str(getattr(offer, 'selection', '') or '').lower(),
            _float(getattr(offer, 'point', None), None),
            str(getattr(offer, 'team_side', '') or '').lower(),
        )
        prev = best.get(key)
        if prev is None or (_float(getattr(offer, 'price', None), 0.0) or 0.0) > (_float(getattr(prev, 'price', None), 0.0) or 0.0):
            best[key] = offer
    return list(best.values())


def _patch_candidate_factory(report: dict[str, Any]) -> None:
    from app.services.model import CandidateFactory

    current = getattr(CandidateFactory, 'build_candidates', None)
    if not callable(current) or getattr(current, '_harizon_model_input_market_sanity', False):
        report['candidate_factory'] = 'already_wrapped_or_missing'
        return

    def build_candidates_with_market_sanity(self: Any, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        total_lines = _allowed_total_lines()
        counters: Counter[str] = Counter()
        filtered: dict[str, list[Any]] = {}
        before_matches = 0
        after_matches = 0
        before_offers = 0
        after_offers = 0
        source_combo_counter: Counter[str] = Counter()
        for match_key, offers in dict(offers_by_match or {}).items():
            bucket = list(offers or [])
            if bucket:
                before_matches += 1
                before_offers += len(bucket)
            kept = [offer for offer in bucket if _is_good_offer(offer, counters, total_lines)]
            kept = _dedupe_best(kept)
            if kept:
                filtered[match_key] = kept
                after_matches += 1
                after_offers += len(kept)
                combo = '+'.join(sorted({str(getattr(o, 'source', '') or '').lower() for o in kept if str(getattr(o, 'source', '') or '').strip()}))
                source_combo_counter[combo or 'unknown'] += 1
        candidates, rejections, debug = current(self, matches, filtered, contexts_by_match, market_signals_by_match=market_signals_by_match)
        debug = dict(debug or {})
        payload = {
            'created_at_utc': datetime.now(UTC).isoformat(),
            'before_matches': before_matches,
            'after_matches': after_matches,
            'before_offers': before_offers,
            'after_offers': after_offers,
            'drop_reasons': dict(counters),
            'allowed_total_lines': sorted(total_lines),
            'source_combinations_after': dict(source_combo_counter.most_common(20)),
            'candidates_after': len(candidates or []),
        }
        debug['model_input_market_sanity'] = payload
        _write(payload)
        return candidates, rejections, debug

    build_candidates_with_market_sanity._harizon_model_input_market_sanity = True  # type: ignore[attr-defined]
    CandidateFactory.build_candidates = build_candidates_with_market_sanity  # type: ignore[assignment]
    report['candidate_factory'] = 'wrapped'


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    _INSTALLED = True
    report: dict[str, Any] = {'created_at_utc': datetime.now(UTC).isoformat(), 'status': 'starting'}
    try:
        _patch_candidate_factory(report)
        report['status'] = 'installed'
    except Exception as exc:
        report['status'] = 'error'
        report['error'] = f'{type(exc).__name__}: {exc}'
    _write(report)
    return report
