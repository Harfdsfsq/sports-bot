from __future__ import annotations

"""Pre-publication price integrity guard for controlled fallback candidates.

The guard blocks real failure modes that were seen in HARIZON:
* a line value from Bzzoiro odds/comparison was used as a decimal price;
* a Bzzoiro over/under path was inverted into the opposite selection;
* the selected price is a clear outlier versus real same-side bookmaker prices;
* totals with quarter points (.25/.75, e.g. 4.75) are blocked from publication.

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
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

EXPORT_DIR = Path('.data/exports')
REPORT_PATH = EXPORT_DIR / 'latest-controlled-fallback-price-integrity-guard.json'
SNAPSHOT_PATHS = (
    EXPORT_DIR / 'latest-odds-api-io-offer-snapshot.json',
    Path('artifacts/run-bot/latest-odds-api-io-offer-snapshot.json'),
)
_SNAPSHOT_CACHE: list[dict[str, Any]] | None = None

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


def point_from_selection_text(row: dict[str, Any]) -> float | None:
    text = ' '.join(str(row.get(key) or '') for key in ('selection', 'selection_key', 'market_selection', 'label', 'name'))
    match = re.search(r'(?<!\d)(\d+(?:[.,]\d+)?)(?!\d)', text)
    if not match:
        return None
    return as_float(match.group(1), None)


def publication_point_value(row: dict[str, Any]) -> float | None:
    return point_value(row) if point_value(row) is not None else point_from_selection_text(row)


def is_allowed_totals_publication_point(row: dict[str, Any]) -> bool:
    if not env_bool('CONTROLLED_FALLBACK_BLOCK_QUARTER_TOTALS', True):
        return True
    if family_norm(row) not in {'totals', 'teamtotals'}:
        return True
    point = publication_point_value(row)
    if point is None:
        return True
    # Publication markets must be whole or half-goal totals only: 2.0, 2.5, 3.0, 3.5...
    # Asian/quarter totals such as 2.25/2.75/4.75 are intentionally excluded.
    return abs(point * 2.0 - round(point * 2.0)) <= 1e-6


def offer_blob(offer: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        'selection', 'selection_key', 'label', 'name', 'outcome', 'side',
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


def _snapshot_payload_rows(payload: Any) -> list[dict[str, Any]]:
    """Return raw odds-api.io offer rows from the saved provider snapshot.

    The provider snapshot has had a few shapes across runs: either a dict with an
    ``offers``/``rows`` key or a bare list.  This reader is intentionally broad
    but only returns dictionaries; no prices are fabricated here.
    """
    if isinstance(payload, list):
        return [dict(x) for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ('offers', 'rows', 'items', 'data'):
        value = payload.get(key)
        if isinstance(value, list):
            return [dict(x) for x in value if isinstance(x, dict)]
    # Some snapshots keep offers under account/source buckets.
    out: list[dict[str, Any]] = []
    for value in payload.values():
        if isinstance(value, list):
            out.extend(dict(x) for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            nested = _snapshot_payload_rows(value)
            if nested:
                out.extend(nested)
    return out


def odds_api_snapshot_offers() -> list[dict[str, Any]]:
    global _SNAPSHOT_CACHE
    if _SNAPSHOT_CACHE is not None:
        return _SNAPSHOT_CACHE
    rows: list[dict[str, Any]] = []
    for path in SNAPSHOT_PATHS:
        payload = load_json(path, None)
        if payload is None:
            continue
        rows = _snapshot_payload_rows(payload)
        if rows:
            break
    _SNAPSHOT_CACHE = rows
    return rows


def _ascii_norm(value: Any) -> str:
    text = norm(value)
    # Keep this lightweight and deterministic; club suffixes are removed because
    # inventory and odds-api snapshots often disagree on FC/SC/II/U19 suffixes.
    for token in (' fc ', ' sc ', ' cf ', ' fk ', ' ac ', ' cd ', ' club ', ' de ', ' la ', ' the '):
        text = f' {text} '.replace(token, ' ')
    return ' '.join(text.split())


def _date_token(value: Any) -> str:
    if value in (None, ''):
        return ''
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        try:
            raw = float(value)
            if raw > 10_000_000_000:
                raw /= 1000.0
            return datetime.fromtimestamp(raw, tz=timezone.utc).date().isoformat()
        except Exception:
            pass
    text = str(value).strip()
    m = re.search(r'(20\d{2}-\d{2}-\d{2})', text)
    if m:
        return m.group(1)
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()
    except Exception:
        return ''


def _row_date(row: dict[str, Any]) -> str:
    for key in ('kickoff_utc', 'commence_time', 'kickoff', 'start_time', 'event_date', 'date'):
        d = _date_token(row.get(key))
        if d:
            return d
    return _date_token(row.get('match_key'))


def _team_name(row: dict[str, Any], side: str) -> str:
    keys = ('home_team', 'home', 'home_name', 'homeTeam') if side == 'home' else ('away_team', 'away', 'away_name', 'awayTeam')
    for key in keys:
        value = row.get(key)
        if isinstance(value, dict):
            value = value.get('name') or value.get('team_name') or value.get('display_name')
        if value not in (None, ''):
            return str(value)
    return ''


def _match_aliases(row: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    raw_key = str(row.get('match_key') or row.get('canonical_match_id') or row.get('event_id') or '').strip().lower()
    if raw_key:
        aliases.add(raw_key)
    date = _row_date(row)
    home = _ascii_norm(_team_name(row, 'home'))
    away = _ascii_norm(_team_name(row, 'away'))
    if date and home and away:
        aliases.update({
            f'{date}|{home}|{away}',
            f'{date}|{away}|{home}',
            f'soccer|{home}|{away}|{date}',
            f'soccer|{away}|{home}|{date}',
        })
    if raw_key:
        parts = [p for p in raw_key.replace('_', '|').split('|') if p]
        maybe_date = next((p for p in parts if re.fullmatch(r'20\d{2}-\d{2}-\d{2}', p)), '')
        teams = [_ascii_norm(p) for p in parts if p not in {'soccer', maybe_date} and not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', p)]
        if maybe_date and len(teams) >= 2:
            aliases.add(f'{maybe_date}|{teams[0]}|{teams[1]}')
            aliases.add(f'{maybe_date}|{teams[1]}|{teams[0]}')
            aliases.add(f'soccer|{teams[0]}|{teams[1]}|{maybe_date}')
            aliases.add(f'soccer|{teams[1]}|{teams[0]}|{maybe_date}')
    return {a for a in aliases if a}


def _token_overlap(a: str, b: str) -> float:
    sa = set(_ascii_norm(a).split())
    sb = set(_ascii_norm(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def _snapshot_offer_matches_candidate(candidate: dict[str, Any], offer: dict[str, Any]) -> bool:
    cand_aliases = _match_aliases(candidate)
    offer_aliases = _match_aliases(offer)
    if cand_aliases and offer_aliases and cand_aliases.intersection(offer_aliases):
        return True
    # Fallback to date + team-token match for snapshots that do not preserve the
    # same match_key shape.  Require the same date to avoid cross-day contamination.
    cdate, odate = _row_date(candidate), _row_date(offer)
    if cdate and odate and cdate != odate:
        return False
    ch, ca = _team_name(candidate, 'home'), _team_name(candidate, 'away')
    oh, oa = _team_name(offer, 'home'), _team_name(offer, 'away')
    if not (ch and ca and oh and oa):
        return False
    direct = (_token_overlap(ch, oh) + _token_overlap(ca, oa)) / 2.0
    swapped = (_token_overlap(ch, oa) + _token_overlap(ca, oh)) / 2.0
    return max(direct, swapped) >= 0.62


def _offer_family(offer: dict[str, Any]) -> str:
    text = norm(' '.join(str(offer.get(k) or '') for k in ('family', 'market_family', 'market', 'market_key', 'market_name', 'path')))
    if 'total' in text or 'over' in text or 'under' in text or 'тотал' in text:
        return 'totals'
    if 'spread' in text or 'handicap' in text or 'фора' in text:
        return 'spreads'
    return str(offer.get('family') or offer.get('market_family') or '').strip().lower()


def external_snapshot_same_side_prices(row: dict[str, Any]) -> tuple[list[float], list[dict[str, Any]], dict[str, Any]]:
    target_side = selection_side(row)
    target_point = publication_point_value(row)
    target_family = family_norm(row)
    prices_by_book: dict[str, float] = {}
    matched_rows = 0
    side_matched_rows: list[dict[str, Any]] = []
    point_mismatch = 0
    side_mismatch = 0
    unknown_side = 0
    for offer in odds_api_snapshot_offers():
        if not _snapshot_offer_matches_candidate(row, offer):
            continue
        matched_rows += 1
        offer_family = _offer_family(offer)
        if target_family in {'totals', 'teamtotals'} and offer_family and offer_family not in {'totals', 'teamtotals'}:
            continue
        side = offer_side(offer) or str(offer.get('side') or '').strip().lower()
        if target_side and not side:
            unknown_side += 1
            continue
        if target_side and side != target_side:
            side_mismatch += 1
            continue
        offer_point = point_value(offer) if point_value(offer) is not None else point_from_selection_text(offer)
        if target_point is not None and offer_point is not None and abs(float(target_point) - float(offer_point)) > 1e-6:
            point_mismatch += 1
            continue
        price = as_float(offer.get('price') or offer.get('odds') or offer.get('decimal_odds'), None)
        if price is None or price <= 1.0 or price > 50.0:
            continue
        book = bookmaker_name(offer)
        if not is_real_bookmaker(book):
            continue
        side_matched_rows.append(offer)
        current = prices_by_book.get(book)
        # Keep the price closest to the selected price to avoid stale duplicate rows
        # from the same book pushing the median away from the market.
        selected = selected_odds(row)
        if current is None or abs(float(price) - selected) < abs(float(current) - selected):
            prices_by_book[book] = float(price)
    debug = {
        'external_snapshot_rows_total': len(odds_api_snapshot_offers()),
        'external_snapshot_match_rows': matched_rows,
        'external_snapshot_side_rows': len(side_matched_rows),
        'external_snapshot_skipped_unknown_side': unknown_side,
        'external_snapshot_skipped_opposite_side': side_mismatch,
        'external_snapshot_skipped_point_mismatch': point_mismatch,
    }
    return list(prices_by_book.values()), side_matched_rows, debug


def selected_vs_external_snapshot_outlier(row: dict[str, Any], report: dict[str, Any]) -> bool:
    if not env_bool('CONTROLLED_FALLBACK_EXTERNAL_SNAPSHOT_PRICE_GUARD_ENABLED', True):
        return False
    if family_norm(row) not in {'totals', 'teamtotals', 'spreads'}:
        return False
    selected = selected_odds(row)
    if selected <= 1.0:
        return False
    prices, offers, debug = external_snapshot_same_side_prices(row)
    report.update(debug)
    report['external_snapshot_same_side_real_book_prices'] = [round(x, 4) for x in prices[:20]]
    min_books = int(env_float('CONTROLLED_FALLBACK_EXTERNAL_SNAPSHOT_MIN_REAL_BOOKS', 2.0))
    if len(prices) < min_books:
        return False
    med = float(median(prices))
    if med <= 1.0:
        return False
    deviation_pct = abs(selected - med) / med * 100.0
    max_dev = env_float('CONTROLLED_FALLBACK_EXTERNAL_SNAPSHOT_MAX_DEVIATION_PCT', 18.0)
    hard_selected = env_float('CONTROLLED_FALLBACK_EXTERNAL_SNAPSHOT_HARD_SELECTED_ODDS', 2.35)
    hard_median = env_float('CONTROLLED_FALLBACK_EXTERNAL_SNAPSHOT_HARD_LOW_MEDIAN_ODDS', 1.75)
    implied_gap_pp = (1.0 / med - 1.0 / selected) * 100.0
    max_gap_pp = env_float('CONTROLLED_FALLBACK_EXTERNAL_SNAPSHOT_MAX_IMPLIED_GAP_PP', 12.0)
    report.update({
        'external_snapshot_price_guard_mode': 'odds_api_io_same_side_snapshot_median',
        'external_snapshot_median_same_side_price': round(med, 4),
        'external_snapshot_selected_price': round(selected, 4),
        'external_snapshot_selected_vs_median_deviation_pct': round(deviation_pct, 3),
        'external_snapshot_selected_vs_median_implied_gap_pp': round(implied_gap_pp, 3),
        'external_snapshot_max_deviation_pct': max_dev,
        'external_snapshot_max_implied_gap_pp': max_gap_pp,
    })
    if selected >= hard_selected and med <= hard_median:
        report['external_snapshot_hard_rule'] = f'selected>={hard_selected} and snapshot_median<={hard_median}'
        return True
    return deviation_pct > max_dev or implied_gap_pp > max_gap_pp


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
    if not is_allowed_totals_publication_point(row):
        details['selected_point'] = publication_point_value(row)
        details['allowed_total_points'] = 'whole_or_half_only'
        reasons.append('market_point:quarter_totals_not_allowed')
    if is_line_field_used_as_price(row):
        reasons.append('price_integrity:bzzoiro_line_value_used_as_price')
    if has_total_side_mismatch(row):
        reasons.append('price_integrity:bzzoiro_total_side_mismatch')
    if selected_vs_median_outlier(row, details):
        reasons.append('price_integrity:selected_price_vs_bookmaker_median_outlier')
    if selected_vs_external_snapshot_outlier(row, details):
        reasons.append('price_integrity:external_snapshot_bookmaker_median_outlier')
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
        'policy': 'identified_same_side_plus_external_odds_api_snapshot_v4_whole_or_half_totals_only',
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
