from __future__ import annotations

"""Pre-publication price integrity guard for controlled fallback candidates.

The guard blocks real failure modes that were seen in HARIZON:
* a line value from Bzzoiro odds/comparison was used as a decimal price;
* a Bzzoiro over/under path was inverted into the opposite selection;
* the selected price is a clear outlier versus real same-side bookmaker prices.

Important: unknown-side prices are no longer mixed into the same-side median.
If an offer cannot be identified as over/under/spread home/away from its own
selection/path fields, it is not used for the median. This prevents false
rejections such as Over 3.5 where one real over price and one under price were
averaged into a bogus median.
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
SYNTHETIC_BOOK_TOKENS = (
    'bzzoiroconsensus',
    'bzzoiro-consensus',
    'sstatsconsensus',
    'consensus',
    'synthetic',
    'model',
)


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
    return re.sub(r'[^a-zа-яё0-9_.@/:+ -]+', ' ', str(value or '').strip().lower()).strip()


def family_norm(row: dict[str, Any]) -> str:
    return str(row.get('family') or row.get('market_family') or '').strip().lower()


def selected_odds(row: dict[str, Any]) -> float:
    for key in ('selected_odds', 'odds', 'price_used_for_ev', 'price'):
        value = as_float(row.get(key), None)
        if value is not None and value > 1.0:
            return float(value)
    return 0.0


def _text_side(text: str) -> str:
    text = norm(text)
    if not text:
        return ''
    # Check under first to avoid matching words that contain "over" by accident.
    if any(token in text for token in ('under', 'меньше', 'тотал меньше', ' тм', 'tm ', 'u/')):
        return 'under'
    if any(token in text for token in ('over', 'больше', 'тотал больше', ' тб', 'tb ', 'o/')):
        return 'over'
    if re.search(r'(^|[._/@:+ -])u(?:nder)?([._/@:+ -]|$)', text):
        return 'under'
    if re.search(r'(^|[._/@:+ -])o(?:ver)?([._/@:+ -]|$)', text):
        return 'over'
    return ''


def selection_side(row: dict[str, Any]) -> str:
    return _text_side(' '.join(str(row.get(key) or '') for key in ('selection', 'selection_key', 'market_selection', 'label', 'name')))


def point_value(row: dict[str, Any]) -> float | None:
    for key in ('point', 'line', 'handicap', 'hdp'):
        value = as_float(row.get(key), None)
        if value is not None:
            return round(float(value), 3)
    return None


def offer_blob(offer: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        'selection', 'selection_key', 'label', 'name', 'outcome',
        'market_name', 'market_key', 'market', 'path', 'field', 'source_path',
    ):
        value = offer.get(key)
        if value not in (None, ''):
            parts.append(str(value))
    meta = offer.get('metadata') if isinstance(offer.get('metadata'), dict) else {}
    raw = meta.get('raw_hint') if isinstance(meta.get('raw_hint'), dict) else None
    for src in (meta, raw or {}):
        for key in ('selection', 'selection_key', 'market_name', 'market_key', 'market', 'path', 'field', 'source_path'):
            value = src.get(key)
            if value not in (None, ''):
                parts.append(str(value))
    return norm(' '.join(parts))


def offer_side(offer: dict[str, Any]) -> str:
    direct = selection_side(offer)
    if direct:
        return direct
    blob = offer_blob(offer)
    under_path = any(token in blob for token in ('.under', '/under', 'under@', '_under', ':under', ' under'))
    over_path = any(token in blob for token in ('.over', '/over', 'over@', '_over', ':over', ' over'))
    if under_path and not over_path:
        return 'under'
    if over_path and not under_path:
        return 'over'
    return ''


def raw_offers(row: dict[str, Any]) -> list[dict[str, Any]]:
    rows = row.get('raw_bucket_offers')
    if not isinstance(rows, list):
        ss = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
        rows = ss.get('raw_bucket_offers') or ss.get('bucket_offers') or ss.get('offers')
    return [dict(x) for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []


def bookmaker_name(offer: dict[str, Any]) -> str:
    return norm(offer.get('bookmaker') or offer.get('book') or offer.get('sportsbook') or offer.get('provider'))


def is_real_bookmaker(book: str) -> bool:
    return bool(book) and not any(token in book for token in SYNTHETIC_BOOK_TOKENS)


def same_side_offer_prices(row: dict[str, Any]) -> tuple[list[float], list[float], list[dict[str, Any]], dict[str, Any]]:
    target_side = selection_side(row)
    target_point = point_value(row)
    prices_by_book: dict[str, float] = {}
    all_identified_prices: list[float] = []
    same_offers: list[dict[str, Any]] = []
    skipped_unknown_side = 0
    skipped_opposite_side = 0
    skipped_point_mismatch = 0

    for offer in raw_offers(row):
        price = as_float(offer.get('price') or offer.get('odds') or offer.get('decimal_odds'), None)
        if price is None or price <= 1.0 or price > 50.0:
            continue
        side = offer_side(offer)
        if target_side and not side:
            skipped_unknown_side += 1
            continue
        if target_side and side != target_side:
            skipped_opposite_side += 1
            continue
        offer_point = point_value(offer)
        if target_point is not None and offer_point is not None and abs(target_point - offer_point) > 1e-6:
            skipped_point_mismatch += 1
            continue
        same_offers.append(offer)
        all_identified_prices.append(float(price))
        book = bookmaker_name(offer)
        if is_real_bookmaker(book):
            # Keep the price closest to the selected side. If duplicates exist, the
            # latest bridge usually puts the selected bookmaker row first; using max
            # is safer for value integrity because it prevents too-low medians from
            # unrelated duplicate stale rows.
            prices_by_book[book] = max(float(price), prices_by_book.get(book, 0.0))

    debug = {
        'same_side_identified_offers_count': len(same_offers),
        'skipped_unknown_side': skipped_unknown_side,
        'skipped_opposite_side': skipped_opposite_side,
        'skipped_point_mismatch': skipped_point_mismatch,
    }
    return list(prices_by_book.values()), all_identified_prices, same_offers, debug


def is_line_field_used_as_price(row: dict[str, Any]) -> bool:
    selected = selected_odds(row)
    if selected <= 1.0:
        return False
    selected_book = ''
    ss = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
    selected_book = norm(row.get('bookmaker') or ss.get('selected_bookmaker') or ss.get('bookmaker'))
    for offer in raw_offers(row):
        price = as_float(offer.get('price') or offer.get('odds') or offer.get('decimal_odds'), None)
        if price is None or abs(float(price) - selected) > 0.025:
            continue
        book = bookmaker_name(offer)
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
        if price is not None and selected > 1.0 and abs(float(price) - selected) > 0.05:
            continue
        side = offer_side(offer)
        if side and side != target:
            return True
    return False


def selected_vs_median_outlier(row: dict[str, Any], report: dict[str, Any]) -> bool:
    selected = selected_odds(row)
    if selected <= 1.0:
        return False
    real_prices, identified_prices, same_offers, debug = same_side_offer_prices(row)
    min_books = int(env_float('CONTROLLED_FALLBACK_PRICE_INTEGRITY_MIN_REAL_BOOKS', 2.0))
    report.update(debug)
    report['same_side_real_book_prices'] = [round(x, 4) for x in real_prices[:20]]
    report['same_side_identified_prices'] = [round(x, 4) for x in identified_prices[:20]]
    report['same_side_offers_count'] = len(same_offers)

    if len(real_prices) < min_books:
        report['price_guard_mode'] = 'insufficient_identified_same_side_real_books'
        report['same_side_prices_count'] = len(real_prices)
        return False

    median_price = float(median(real_prices))
    if median_price <= 1.0:
        return False
    deviation_pct = abs(selected - median_price) / median_price * 100.0
    max_dev = env_float('CONTROLLED_FALLBACK_MAX_SELECTED_BOOK_MEDIAN_DEVIATION_PCT', 16.0)
    report.update({
        'price_guard_mode': 'identified_same_side_real_book_median',
        'selected_price': round(selected, 4),
        'median_same_side_price': round(median_price, 4),
        'selected_vs_median_deviation_pct': round(deviation_pct, 3),
        'max_deviation_pct': max_dev,
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
            ss = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
            report['rejected'].append({
                'source': source,
                'match_key': row.get('match_key'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'family': row.get('family'),
                'selection': row.get('selection'),
                'point': row.get('point'),
                'odds': selected_odds(row),
                'bookmaker': row.get('bookmaker') or ss.get('selected_bookmaker') or ss.get('bookmaker'),
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
        'policy': 'identified_same_side_only_for_median_v2',
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
