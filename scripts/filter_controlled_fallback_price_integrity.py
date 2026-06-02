from __future__ import annotations

"""Pre-publication price integrity guard for controlled fallback candidates.

This script runs immediately before scripts/publish_controlled_fallback.py.  It
removes candidates whose selected price is not a real same-side bookmaker price.
The concrete failure it blocks:

* Bzzoiro v2 comparison path `...over@2.5.line` was mined as price=2.5;
* the hint was also mapped to selection=Under;
* fallback selected Under 2.5 @2.50-2.66 while real Under offers were ~1.4-1.8.

The guard does not lower thresholds and does not create prices. It only filters
bad candidate rows from fresh artifacts and writes a diagnostic report.
"""

import json
import math
import os
import re
from pathlib import Path
from statistics import median
from typing import Any

EXPORT_DIR = Path('.data/exports')
REPORT_PATH = EXPORT_DIR / 'latest-controlled-fallback-price-integrity-guard.json'

TRUTHY = {'1', 'true', 'yes', 'on', 'y'}
SYNTHETIC_BOOK_TOKENS = ('bzzoiroconsensus', 'bzzoiro-consensus', 'sstatsconsensus', 'consensus')


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in TRUTHY


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    try:
        return float(raw)
    except Exception:
        return default


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ''):
            return default
        number = float(str(value).strip().replace(',', '.'))
        if math.isfinite(number):
            return number
    except Exception:
        pass
    return default


def norm(value: Any) -> str:
    return re.sub(r'[^a-zа-яё0-9_.@/ -]+', ' ', str(value or '').strip().lower()).strip()


def family_norm(row: dict[str, Any]) -> str:
    return str(row.get('family') or row.get('market_family') or '').strip().lower()


def selected_odds(row: dict[str, Any]) -> float:
    for key in ('selected_odds', 'odds', 'price_used_for_ev', 'price'):
        value = as_float(row.get(key), None)
        if value is not None and value > 1.0:
            return float(value)
    return 0.0


def selection_side(row: dict[str, Any]) -> str:
    text = str(row.get('selection') or row.get('selection_key') or '').strip().lower()
    if any(token in text for token in ('under', 'меньше', 'тм')):
        return 'under'
    if any(token in text for token in ('over', 'больше', 'тб')):
        return 'over'
    return ''


def point_value(row: dict[str, Any]) -> float | None:
    for key in ('point', 'line', 'handicap'):
        value = as_float(row.get(key), None)
        if value is not None:
            return round(float(value), 3)
    return None


def offer_blob(offer: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ('market_name', 'market_key', 'market', 'path', 'field', 'source_path', 'selection_key'):
        value = offer.get(key)
        if value not in (None, ''):
            parts.append(str(value))
    meta = offer.get('metadata') if isinstance(offer.get('metadata'), dict) else {}
    raw = meta.get('raw_hint') if isinstance(meta.get('raw_hint'), dict) else None
    for src in (meta, raw or {}):
        for key in ('market_name', 'market_key', 'market', 'path', 'field', 'source_path'):
            value = src.get(key)
            if value not in (None, ''):
                parts.append(str(value))
    return norm(' '.join(parts))


def raw_offers(row: dict[str, Any]) -> list[dict[str, Any]]:
    rows = row.get('raw_bucket_offers')
    if not isinstance(rows, list):
        ss = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
        rows = ss.get('raw_bucket_offers') or ss.get('bucket_offers') or ss.get('offers')
    return [dict(x) for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []


def same_side_offer_prices(row: dict[str, Any]) -> tuple[list[float], list[float], list[dict[str, Any]]]:
    target_side = selection_side(row)
    target_point = point_value(row)
    bookmaker_prices: list[float] = []
    all_prices: list[float] = []
    same_offers: list[dict[str, Any]] = []
    for offer in raw_offers(row):
        price = as_float(offer.get('price') or offer.get('odds') or offer.get('decimal_odds'), None)
        if price is None or price <= 1.0 or price > 50.0:
            continue
        offer_side = selection_side(offer)
        offer_point = point_value(offer)
        if target_side and offer_side and offer_side != target_side:
            continue
        if target_point is not None and offer_point is not None and abs(target_point - offer_point) > 1e-6:
            continue
        same_offers.append(offer)
        all_prices.append(float(price))
        book = norm(offer.get('bookmaker') or offer.get('book') or offer.get('sportsbook'))
        if book and not any(token in book for token in SYNTHETIC_BOOK_TOKENS):
            bookmaker_prices.append(float(price))
    return bookmaker_prices, all_prices, same_offers


def is_line_field_used_as_price(row: dict[str, Any]) -> bool:
    selected = selected_odds(row)
    if selected <= 1.0:
        return False
    selected_book = norm(row.get('bookmaker') or (row.get('source_summary') or {}).get('selected_bookmaker') if isinstance(row.get('source_summary'), dict) else '')
    for offer in raw_offers(row):
        price = as_float(offer.get('price') or offer.get('odds') or offer.get('decimal_odds'), None)
        if price is None or abs(float(price) - selected) > 0.025:
            continue
        book = norm(offer.get('bookmaker') or offer.get('book') or offer.get('sportsbook'))
        if selected_book and book and selected_book != book:
            continue
        blob = offer_blob(offer)
        if any(token in blob for token in ('.line', '/line', '_line', '@1.5.line', '@2.5.line', '@3.5.line')):
            return True
    return False


def has_total_side_mismatch(row: dict[str, Any]) -> bool:
    if family_norm(row) not in {'totals', 'teamtotals'}:
        return False
    target = selection_side(row)
    if target not in {'over', 'under'}:
        return False
    selected = selected_odds(row)
    for offer in raw_offers(row):
        price = as_float(offer.get('price') or offer.get('odds') or offer.get('decimal_odds'), None)
        # Prioritize the selected offer, but also block if any same-side Bzzoiro hint was obviously inverted.
        if price is not None and selected > 1.0 and abs(float(price) - selected) > 0.05:
            continue
        blob = offer_blob(offer)
        over_path = any(token in blob for token in ('.over', '/over', 'over@', '_over'))
        under_path = any(token in blob for token in ('.under', '/under', 'under@', '_under'))
        if target == 'under' and over_path:
            return True
        if target == 'over' and under_path:
            return True
    return False


def selected_vs_median_outlier(row: dict[str, Any], report: dict[str, Any]) -> bool:
    selected = selected_odds(row)
    if selected <= 1.0:
        return False
    real_prices, all_prices, same_offers = same_side_offer_prices(row)
    min_books = int(env_float('CONTROLLED_FALLBACK_PRICE_INTEGRITY_MIN_REAL_BOOKS', 2.0))
    prices = real_prices if len(real_prices) >= min_books else all_prices
    if len(prices) < max(2, min_books):
        report['price_guard_mode'] = 'insufficient_same_side_prices'
        report['same_side_prices_count'] = len(prices)
        return False
    median_price = float(median(prices))
    if median_price <= 1.0:
        return False
    deviation_pct = abs(selected - median_price) / median_price * 100.0
    max_dev = env_float('CONTROLLED_FALLBACK_MAX_SELECTED_BOOK_MEDIAN_DEVIATION_PCT', 16.0)
    report.update({
        'price_guard_mode': 'same_side_real_book_median',
        'selected_price': round(selected, 4),
        'median_same_side_price': round(median_price, 4),
        'selected_vs_median_deviation_pct': round(deviation_pct, 3),
        'max_deviation_pct': max_dev,
        'same_side_real_book_prices': [round(x, 4) for x in real_prices[:20]],
        'same_side_all_prices': [round(x, 4) for x in all_prices[:20]],
        'same_side_offers_count': len(same_offers),
    })
    return deviation_pct > max_dev


def selected_vs_market_probability_outlier(row: dict[str, Any], report: dict[str, Any]) -> bool:
    selected = selected_odds(row)
    market_prob = as_float(row.get('market_probability'), None)
    if selected <= 1.0 or market_prob is None or market_prob <= 0.0:
        return False
    implied = 1.0 / selected
    gap_pp = (float(market_prob) - implied) * 100.0
    max_gap = env_float('CONTROLLED_FALLBACK_MAX_SELECTED_VS_MARKET_GAP_PP', 10.0)
    report.update({
        'selected_implied_probability': round(implied, 6),
        'market_probability': round(float(market_prob), 6),
        'selected_vs_market_gap_pp': round(gap_pp, 3),
        'max_selected_vs_market_gap_pp': max_gap,
    })
    # Only block when selected is materially higher than market consensus. Small positive
    # gaps are normal value candidates; huge gaps usually mean wrong side/wrong field.
    return gap_pp > max_gap


def candidate_reject_reasons(row: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    details: dict[str, Any] = {}
    reasons: list[str] = []
    if not env_bool('CONTROLLED_FALLBACK_PRICE_INTEGRITY_GUARD_ENABLED', True):
        return reasons, details
    if family_norm(row) not in {'totals', 'teamtotals', 'spreads'}:
        return reasons, details
    if is_line_field_used_as_price(row):
        reasons.append('price_integrity:bzzoiro_line_value_used_as_price')
    if has_total_side_mismatch(row):
        reasons.append('price_integrity:bzzoiro_total_side_mismatch')
    if selected_vs_median_outlier(row, details):
        reasons.append('price_integrity:selected_price_vs_bookmaker_median_outlier')
    if selected_vs_market_probability_outlier(row, details):
        reasons.append('price_integrity:selected_price_vs_market_probability_outlier')
    return reasons, details


def filter_rows(rows: list[Any], source: str, report: dict[str, Any]) -> list[Any]:
    kept: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        reasons, details = candidate_reject_reasons(row)
        if reasons:
            report['rejected'].append({
                'source': source,
                'match_key': row.get('match_key'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'family': row.get('family'),
                'selection': row.get('selection'),
                'point': row.get('point'),
                'odds': selected_odds(row),
                'bookmaker': row.get('bookmaker') or (row.get('source_summary') or {}).get('selected_bookmaker') if isinstance(row.get('source_summary'), dict) else row.get('bookmaker'),
                'reasons': reasons,
                'details': details,
            })
            continue
        kept.append(row)
    report['sources'][source] = {'input': len(rows), 'kept': len(kept), 'removed': len(rows) - len(kept)}
    return kept


def filter_payload_file(path: Path, source: str, report: dict[str, Any]) -> None:
    payload = load_json(path, None)
    if payload is None:
        return
    changed = False
    if isinstance(payload, dict):
        for key in ('candidates', 'rows', 'items'):
            if isinstance(payload.get(key), list):
                original = payload[key]
                payload[key] = filter_rows(original, f'{source}.{key}', report)
                changed = changed or len(payload[key]) != len(original)
        if changed:
            payload['price_integrity_guard_applied'] = True
            payload['price_integrity_guard_removed'] = sum(v.get('removed', 0) for k, v in report['sources'].items() if k.startswith(source))
            write_json(path, payload)
    elif isinstance(payload, list):
        filtered = filter_rows(payload, source, report)
        if len(filtered) != len(payload):
            write_json(path, filtered)


def filter_debug_file(path: Path, report: dict[str, Any]) -> None:
    payload = load_json(path, None)
    if not isinstance(payload, dict):
        return
    changed = False
    for key in ('candidates_before_quality', 'candidates_after_quality'):
        if isinstance(payload.get(key), list):
            original = payload[key]
            payload[key] = filter_rows(original, f'debug.{key}', report)
            changed = changed or len(payload[key]) != len(original)
    if changed:
        payload['price_integrity_guard_applied'] = True
        write_json(path, payload)


def main() -> int:
    report: dict[str, Any] = {
        'enabled': env_bool('CONTROLLED_FALLBACK_PRICE_INTEGRITY_GUARD_ENABLED', True),
        'sources': {},
        'rejected': [],
    }
    if not report['enabled']:
        write_json(REPORT_PATH, report)
        return 0
    filter_payload_file(Path('.data/exports/latest-rescue-candidates.json'), 'latest_rescue_candidates', report)
    filter_payload_file(Path('artifacts/run-bot/latest-rescue-candidates.json'), 'artifact_rescue_candidates', report)
    filter_payload_file(Path('.data/exports/latest-picks.json'), 'latest_picks', report)
    filter_debug_file(Path('.logs/debug-last-run.json'), report)
    report['removed_total'] = len(report['rejected'])
    write_json(REPORT_PATH, report)
    if report['rejected']:
        print(f"price integrity guard removed {len(report['rejected'])} controlled fallback candidates")
    else:
        print('price integrity guard: no suspicious candidates')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
