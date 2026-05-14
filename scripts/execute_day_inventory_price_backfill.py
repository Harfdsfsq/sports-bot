from __future__ import annotations

"""Execute the low-quota odds-api.io price backfill plan.

This script is intentionally narrow and auditable.  It reads
latest-day-inventory-price-backfill-plan.json, calls odds-api.io /odds/multi in
at most one batch per configured account, and merges returned bookmaker prices
back into the top-300 day inventory as price confirmations.

It does not generate predictions.  It only improves stored line evidence so the
next model/publication pass can see which matches have 2+ independent price
confirmations and 2+ context confirmations.
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

UTC = timezone.utc
ROOT = Path('.').resolve()
DAY_INV_DIR = ROOT / '.data' / 'day_inventory'
EXPORT_DIR = ROOT / '.data' / 'exports'
PLAN_JSON = EXPORT_DIR / 'latest-day-inventory-price-backfill-plan.json'
OUT_JSON = EXPORT_DIR / 'latest-day-inventory-price-backfill-execution.json'
OUT_TXT = EXPORT_DIR / 'latest-day-inventory-price-backfill-execution.txt'
SUMMARY = EXPORT_DIR / 'latest-day-inventory-summary.json'


def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name) or '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def secret(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name) or '').strip()
        if value:
            return value
    return ''


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def target_date(now: datetime) -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    return explicit or now.astimezone(app_tz()).date().isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        return float(str(value).replace(',', '.'))
    except Exception:
        return None


def norm(value: Any) -> str:
    text = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    aliases = {
        'oddsapiio': 'odds_api_io',
        'odds_api': 'odds_api_io',
        'bet365': 'bet365',
        'unibet': 'unibet',
        'betfair': 'betfair_exchange',
        'betfairexchange': 'betfair_exchange',
        'sbobet': 'sbobet',
    }
    return aliases.get(text, text)


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r'[,|;/]+', value) if v.strip()]
    return []


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ('data', 'results', 'response', 'events', 'odds', 'items'):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = rows(value)
            if nested:
                return nested
    # Some endpoints return a single event object.
    if any(k in payload for k in ('id', 'eventId', 'event_id', 'bookmakers', 'markets')):
        return [payload]
    return []


def text_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ('name', 'title', 'key', 'label', 'display_name'):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        return ''
    return str(value or '').strip()


def event_id(row: dict[str, Any]) -> str:
    for key in ('id', 'eventId', 'event_id', 'source_event_id', 'match_id'):
        value = row.get(key)
        if value not in (None, ''):
            return str(value).strip()
    return ''


def market_family(raw: Any) -> str:
    text = norm(text_value(raw))
    if text in {'totals', 'total', 'over_under', 'over_under_goals'} or 'over_under' in text or 'total' in text:
        return 'totals'
    if text in {'spreads', 'spread', 'handicap', 'asian_handicap'} or 'spread' in text or 'handicap' in text:
        return 'spreads'
    if text in {'h2h', 'match_winner', 'winner', '1x2', 'full_time_result'}:
        return 'h2h'
    if 'btts' in text or 'both_teams' in text:
        return 'btts'
    return text or 'unknown'


def iter_bookmakers(event: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ('bookmakers', 'books', 'sportsbooks'):
        value = event.get(key)
        if isinstance(value, list):
            candidates.extend(x for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            for name, payload in value.items():
                if isinstance(payload, dict):
                    item = dict(payload)
                    item.setdefault('name', name)
                    candidates.append(item)
    if not candidates:
        # Flat rows still become one synthetic bookmaker payload.
        book = text_value(event.get('bookmaker') or event.get('book') or event.get('sportsbook'))
        if book:
            candidates.append({'name': book, 'markets': [event]})
    return candidates


def iter_markets(book: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ('markets', 'odds', 'outcomes', 'prices'):
        value = book.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    out.append(item)
        elif isinstance(value, dict):
            for name, payload in value.items():
                if isinstance(payload, dict):
                    item = dict(payload)
                    item.setdefault('key', name)
                    out.append(item)
                elif isinstance(payload, (int, float, str)):
                    out.append({'key': name, 'price': payload})
    return out or [book]


def iter_outcomes(market: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ('outcomes', 'selections', 'odds', 'prices'):
        value = market.get(key)
        if isinstance(value, list):
            out.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            for name, payload in value.items():
                if isinstance(payload, dict):
                    item = dict(payload)
                    item.setdefault('name', name)
                    out.append(item)
                elif isinstance(payload, (int, float, str)):
                    out.append({'name': name, 'price': payload})
    if not out and any(k in market for k in ('price', 'odds', 'value', 'decimal')):
        out.append(market)
    return out


def outcome_price(outcome: dict[str, Any]) -> float | None:
    for key in ('price', 'odds', 'decimal', 'value', 'decimal_odds'):
        price = as_float(outcome.get(key))
        if price and price > 1.0:
            return price
    return None


def outcome_point(outcome: dict[str, Any], market: dict[str, Any]) -> str:
    for key in ('point', 'line', 'total', 'handicap', 'value'):
        value = outcome.get(key)
        if value not in (None, '') and key != 'value':
            return str(value).strip()
    for key in ('point', 'line', 'total', 'handicap'):
        value = market.get(key)
        if value not in (None, ''):
            return str(value).strip()
    name = text_value(outcome.get('name') or outcome.get('selection'))
    m = re.search(r'([+-]?\d+(?:\.\d+)?)', name)
    return m.group(1) if m else ''


def extract_price_evidence(payload: Any, account: str) -> dict[str, dict[str, Any]]:
    by_event: dict[str, dict[str, Any]] = {}
    for event in rows(payload):
        eid = event_id(event)
        if not eid:
            continue
        ev = by_event.setdefault(eid, {'price_confirmations': set(), 'books': set(), 'odds_sources': set(), 'samples': []})
        for book in iter_bookmakers(event):
            book_name = text_value(book.get('name') or book.get('title') or book.get('key') or book.get('bookmaker'))
            book_key = norm(book_name) or 'unknown_book'
            if book_key:
                ev['books'].add(book_key)
            for market in iter_markets(book):
                family = market_family(market.get('key') or market.get('market_key') or market.get('market') or market.get('name') or market.get('type'))
                for outcome in iter_outcomes(market):
                    price = outcome_price(outcome)
                    if not price:
                        continue
                    selection = norm(outcome.get('name') or outcome.get('selection') or outcome.get('label')) or 'selection'
                    point = outcome_point(outcome, market)
                    # Count only public-safe market families as strong line confirmations.
                    public_family = family if family in {'totals', 'spreads'} else family
                    token = f'odds_api_io:{account}:{book_key}:{public_family}:{selection}:{point}'
                    ev['price_confirmations'].add(token)
                    ev['odds_sources'].add(account)
                    samples = ev['samples']
                    if isinstance(samples, list) and len(samples) < 10:
                        samples.append({'account': account, 'bookmaker': book_key, 'family': public_family, 'selection': selection, 'point': point, 'price': price})
    return by_event


async def fetch_odds_multi(client: httpx.AsyncClient, key: str, event_ids: list[str], bookmakers: str, account: str) -> tuple[Any, dict[str, Any]]:
    if not key or not event_ids:
        return None, {'account': account, 'status': 'skipped', 'reason': 'missing_key_or_event_ids', 'rows': 0}
    params = {'apiKey': key, 'eventIds': ','.join(event_ids), 'bookmakers': bookmakers}
    started = datetime.now(UTC)
    try:
        resp = await client.get('https://api.odds-api.io/v3/odds/multi', params=params)
        try:
            payload = resp.json()
        except Exception:
            payload = None
        return payload, {
            'account': account,
            'status': resp.status_code,
            'ok': resp.status_code == 200,
            'event_ids_requested': len(event_ids),
            'bookmakers': bookmakers,
            'rows': len(rows(payload)),
            'body_preview': resp.text[:500],
            'duration_ms': round((datetime.now(UTC) - started).total_seconds() * 1000, 1),
        }
    except Exception as exc:
        return None, {'account': account, 'status': 'request_error', 'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'event_ids_requested': len(event_ids), 'bookmakers': bookmakers}


def source_ids(row: dict[str, Any]) -> dict[str, str]:
    raw = row.get('source_ids') if isinstance(row.get('source_ids'), dict) else {}
    out = {norm(k): str(v).strip() for k, v in raw.items() if str(v).strip()}
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    for key in ('odds_api_io_event_id', 'odds_api_io_id'):
        if md.get(key) and 'odds_api_io' not in out:
            out['odds_api_io'] = str(md[key]).strip()
    return out


def merge_inventory(evidence: dict[str, dict[str, Any]], report: dict[str, Any]) -> None:
    now = datetime.now(UTC).isoformat()
    date_local = target_date(datetime.now(UTC))
    inv_path = DAY_INV_DIR / f'{date_local}.json'
    inv = load_json(inv_path, {})
    if not isinstance(inv, dict):
        return
    matches = [row for row in inv.get('matches', []) if isinstance(row, dict)]
    min_price = max(2, as_int(os.getenv('PUBLISH_MIN_ODDS_SOURCES') or os.getenv('CONTROLLED_FALLBACK_MIN_ODDS_SOURCES'), 2))
    min_context = max(2, as_int(os.getenv('PUBLISH_MIN_CONTEXT_SOURCES') or os.getenv('MIN_CONTEXT_SOURCES_PUBLISH'), 2))
    updated = 0
    newly_price_ready = 0
    newly_publish_ready = 0
    for row in matches:
        eid = source_ids(row).get('odds_api_io')
        if not eid or eid not in evidence:
            continue
        ev = evidence[eid]
        price_tokens = set(list_from_any(row.get('price_confirmations'))) | set(ev.get('price_confirmations') or set())
        books = set(list_from_any(row.get('books'))) | set(ev.get('books') or set())
        odds_sources = set(list_from_any(row.get('odds_sources'))) | {'odds_api_io'} | set(ev.get('odds_sources') or set())
        row['price_confirmations'] = sorted(price_tokens)
        row['books'] = sorted(books)
        row['odds_sources'] = sorted(odds_sources)
        row['line_sources'] = sorted(set(list_from_any(row.get('line_sources'))) | odds_sources)
        md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        before_price_ready = as_int(md.get('price_confirmation_sources_count')) >= min_price
        price_count = max(as_int(md.get('price_confirmation_sources_count')), len(price_tokens), len(books), len(odds_sources))
        md['price_confirmation_sources_count'] = price_count
        md['price_sources_count'] = price_count
        md['books_count'] = max(as_int(md.get('books_count')), len(books))
        md['odds_sources_count'] = max(as_int(md.get('odds_sources_count')), len(odds_sources))
        md['independent_odds_sources_count'] = max(as_int(md.get('independent_odds_sources_count')), len(odds_sources))
        md['odds_api_io_backfill_updated_utc'] = now
        if ev.get('samples'):
            md['odds_api_io_backfill_samples'] = ev.get('samples')[:10]
        row['metadata'] = md
        cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        context_count = max(as_int(md.get('context_sources_count')), as_int(md.get('confirmation_sources_count')), len(row.get('context_confirmations') or []), len(row.get('context_sources') or []))
        cov['odds'] = True
        cov['odds_2plus_sources'] = price_count >= min_price
        cov['ready_for_model'] = bool(cov.get('ready_for_model')) or (price_count > 0 and context_count > 0)
        before_publish = bool(cov.get('ready_for_publish'))
        cov['ready_for_publish'] = bool(cov.get('ready_for_publish')) or (price_count >= min_price and context_count >= min_context)
        row['coverage'] = cov
        refresh = row.get('refresh') if isinstance(row.get('refresh'), dict) else {}
        refresh['last_odds_refresh_utc'] = now
        row['refresh'] = refresh
        row['price_backfill'] = {
            'updated_at_utc': now,
            'needed': price_count < min_price,
            'executed': True,
            'price_confirmations': price_count,
            'context_confirmations': context_count,
            'source': 'odds_api_io',
        }
        row['coverage_gaps'] = {
            'price_confirmations': price_count,
            'context_confirmations': context_count,
            'need_price_confirmations': max(0, min_price - price_count),
            'need_context_confirmations': max(0, min_context - context_count),
            'has_odds': price_count > 0,
            'has_context': context_count > 0,
        }
        updated += 1
        newly_price_ready += int((not before_price_ready) and price_count >= min_price)
        newly_publish_ready += int((not before_publish) and bool(cov.get('ready_for_publish')))
    # recompute core counts
    counts = dict(inv.get('counts') or {})
    price2 = context2 = odds_any = context_any = ready_model = ready_publish = 0
    for row in matches:
        md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        pc = max(as_int(md.get('price_confirmation_sources_count')), len(row.get('price_confirmations') or []), len(row.get('books') or []))
        cc = max(as_int(md.get('context_sources_count')), as_int(md.get('confirmation_sources_count')), len(row.get('context_confirmations') or []), len(row.get('context_sources') or []))
        odds_any += int(bool(cov.get('odds')) or pc > 0)
        context_any += int(bool(cov.get('context')) or cc > 0)
        price2 += int(pc >= min_price)
        context2 += int(cc >= min_context)
        ready_model += int(bool(cov.get('ready_for_model')))
        ready_publish += int(bool(cov.get('ready_for_publish')))
    counts.update({
        'matches_with_odds': odds_any,
        'matches_with_context': context_any,
        'matches_with_2plus_price_confirmations': price2,
        'matches_with_2plus_odds_sources': price2,
        'matches_with_2plus_context_sources': context2,
        'matches_ready_for_model': ready_model,
        'matches_ready_for_publish': ready_publish,
        'matches_missing_price_2plus': max(0, len(matches) - price2),
        'matches_missing_context_2plus': max(0, len(matches) - context2),
        'price_backfill_execution_updated_utc': now,
    })
    inv['counts'] = counts
    inv['updated_at_utc'] = now
    src = inv.setdefault('sources', {})
    if isinstance(src, dict):
        src['price_backfill_execution'] = {
            'updated_at_utc': now,
            'matches_updated': updated,
            'newly_price_ready': newly_price_ready,
            'newly_publish_ready': newly_publish_ready,
            'requests_used': report.get('requests_used'),
        }
    for path in [inv_path, DAY_INV_DIR / 'latest.json', DAY_INV_DIR / 'current.json', DAY_INV_DIR / 'today.json']:
        write_json(path, inv)
    summary = load_json(SUMMARY, {})
    if isinstance(summary, dict):
        summary['counts'] = counts
        summary['sources'] = dict(inv.get('sources') or {})
        summary['updated_at_utc'] = now
        write_json(SUMMARY, summary)
    report.update({'matches_updated': updated, 'newly_price_ready': newly_price_ready, 'newly_publish_ready': newly_publish_ready, 'counts_after': counts})


def render(report: dict[str, Any]) -> str:
    lines = [
        '💸 Day inventory price backfill execution',
        f"• status: {report.get('status')}",
        f"• requests_used: {report.get('requests_used')}",
        f"• event_ids_requested: {report.get('event_ids_requested')}",
        f"• event_ids_with_prices: {report.get('event_ids_with_prices')}",
        f"• matches_updated: {report.get('matches_updated')}",
        f"• newly_price_ready: {report.get('newly_price_ready')}",
        f"• newly_publish_ready: {report.get('newly_publish_ready')}",
    ]
    counts = report.get('counts_after') or {}
    if isinstance(counts, dict):
        lines.append(f"• matches_with_2plus_price_confirmations: {counts.get('matches_with_2plus_price_confirmations')}")
        lines.append(f"• matches_ready_for_publish: {counts.get('matches_ready_for_publish')}")
    return '\n'.join(lines) + '\n'


async def main_async() -> int:
    now = datetime.now(UTC)
    if not env_bool('PRICE_BACKFILL_EXECUTE_ENABLED', False):
        report = {'status': 'skipped', 'reason': 'PRICE_BACKFILL_EXECUTE_ENABLED is not true', 'updated_at_utc': now.isoformat(), 'requests_used': 0}
        write_json(OUT_JSON, report)
        OUT_TXT.write_text(render(report), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    plan = load_json(PLAN_JSON, {})
    ids = [str(x).strip() for x in (plan.get('odds_api_io_event_ids') or []) if str(x).strip()]
    # Preserve order but remove duplicates.
    seen: set[str] = set()
    ids = [x for x in ids if not (x in seen or seen.add(x))]
    ids = ids[: max(1, as_int(os.getenv('PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT'), 60))]
    key1 = secret('ODDS_API_IO_KEY')
    key2 = secret('ODDS_API_IO_KEY_2', 'ODDS_API_IO_KEY2')
    timeout = httpx.Timeout(float(os.getenv('PRICE_BACKFILL_TIMEOUT_SECONDS', '16')), connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        tasks = [
            fetch_odds_multi(client, key1, ids, os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT1') or 'Bet365,Unibet', 'odds_api_io_account1'),
            fetch_odds_multi(client, key2, ids, os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT2') or 'Betfair Exchange,Sbobet', 'odds_api_io_account2'),
        ]
        results = await asyncio.gather(*tasks)
    evidence: dict[str, dict[str, Any]] = {}
    attempts = []
    requests_used = 0
    for payload, attempt in results:
        attempts.append(attempt)
        if attempt.get('ok'):
            requests_used += 1
        account = str(attempt.get('account') or 'odds_api_io')
        by_event = extract_price_evidence(payload, account)
        for eid, ev in by_event.items():
            dst = evidence.setdefault(eid, {'price_confirmations': set(), 'books': set(), 'odds_sources': set(), 'samples': []})
            for key in ('price_confirmations', 'books', 'odds_sources'):
                dst[key].update(ev.get(key) or set())
            for sample in ev.get('samples') or []:
                if len(dst['samples']) < 10:
                    dst['samples'].append(sample)
    report: dict[str, Any] = {
        'status': 'ok',
        'updated_at_utc': now.isoformat(),
        'plan_path': str(PLAN_JSON),
        'event_ids_requested': len(ids),
        'event_ids_with_prices': len(evidence),
        'requests_used': requests_used,
        'attempts': attempts,
    }
    merge_inventory(evidence, report)
    # Convert sets for JSON safety.
    write_json(OUT_JSON, report)
    OUT_TXT.write_text(render(report), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except Exception as exc:
        report = {'status': 'error', 'updated_at_utc': datetime.now(UTC).isoformat(), 'error': f'{type(exc).__name__}: {exc}'}
        write_json(OUT_JSON, report)
        OUT_TXT.write_text(render(report), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
