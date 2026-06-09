from __future__ import annotations

"""Execute low-quota price backfill for the persisted top-300 inventory.

This executor keeps odds-api.io spend tiny: by default one /odds/multi batch per
account, max 10 eventIds per batch.  It also uses matched Bzzoiro event ids from
the planner to fetch `/events/{id}/odds/` for a small secondary line backfill.
Bzzoiro does not consume the odds-api.io quota and helps cover matches that have
context but no odds-api.io event id.
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.publication_thresholds import publish_min_context_sources, publish_min_odds_sources
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
LIVE_ODDS_SOURCES = {'odds_api_io', 'bzzoiro', 'sportlogic'}


def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name) or '').strip().lower()
    return default if not raw else raw in {'1', 'true', 'yes', 'on', 'force'}


def env_int(name: str, default: int) -> int:
    try:
        raw = str(os.getenv(name) or '').strip()
        return int(float(raw)) if raw else default
    except Exception:
        return default


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


def target_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    return explicit or datetime.now(UTC).astimezone(app_tz()).date().isoformat()


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
        'oddsapiio': 'odds_api_io', 'odds_api': 'odds_api_io',
        'bet365': 'bet365', 'unibet': 'unibet', 'betfair': 'betfair_exchange',
        'betfairexchange': 'betfair_exchange', 'betfair_exchange': 'betfair_exchange',
        'sbobet': 'sbobet', 'ml': 'h2h', 'moneyline': 'h2h', 'match_winner': 'h2h',
        '1x2': 'h2h', 'winner': 'h2h', 'spread': 'spreads', 'handicap': 'spreads',
        'asian_handicap': 'spreads', 'over_under': 'totals', 'over_under_goals': 'totals',
        'total': 'totals', 'ou': 'totals', 'both_teams_to_score': 'btts',
    }
    return aliases.get(text, text)


def unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


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
    for key in ('data', 'results', 'response', 'events', 'odds', 'items', 'markets', 'bookmakers'):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = rows(value)
            if nested:
                return nested
    if any(k in payload for k in ('id', 'eventId', 'event_id', 'bookmakers', 'markets', 'market', 'price', 'odds')):
        return [payload]
    return []


def text_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ('name', 'title', 'key', 'label', 'display_name', 'bookmaker'):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        return ''
    return str(value or '').strip()


def event_id(row: dict[str, Any], default: str = '') -> str:
    for key in ('id', 'eventId', 'event_id', 'source_event_id', 'match_id'):
        value = row.get(key)
        if value not in (None, ''):
            return str(value).strip()
    return default


def market_family(raw: Any) -> str:
    text = norm(text_value(raw))
    if text in {'totals', 'spreads', 'h2h', 'btts'}:
        return text
    if 'total' in text or 'over_under' in text:
        return 'totals'
    if 'spread' in text or 'handicap' in text:
        return 'spreads'
    if 'btts' in text or 'both_teams' in text:
        return 'btts'
    return text or 'unknown'


def iter_bookmakers(event: dict[str, Any], default_book: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ('bookmakers', 'books', 'sportsbooks'):
        value = event.get(key)
        if isinstance(value, list):
            out.extend(x for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            for name, payload in value.items():
                if isinstance(payload, dict):
                    item = dict(payload); item.setdefault('name', name); out.append(item)
                elif isinstance(payload, list):
                    out.append({'name': name, 'markets': [x for x in payload if isinstance(x, dict)]})
                elif payload not in (None, ''):
                    out.append({'name': name, 'markets': [{'name': name, 'price': payload}]})
    if not out:
        book = text_value(event.get('bookmaker') or event.get('book') or event.get('sportsbook')) or default_book
        out.append({'name': book, 'markets': [event]})
    return out


def iter_markets(book: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ('markets', 'odds', 'outcomes', 'prices'):
        value = book.get(key)
        if isinstance(value, list):
            out.extend(x for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            for name, payload in value.items():
                if isinstance(payload, dict):
                    item = dict(payload); item.setdefault('name', name); out.append(item)
                elif isinstance(payload, list):
                    out.append({'name': name, 'odds': [x for x in payload if isinstance(x, dict)]})
                elif isinstance(payload, (int, float, str)):
                    out.append({'name': name, 'price': payload})
    return out or [book]


def _flatten_price_dict(value: dict[str, Any], market: dict[str, Any]) -> list[dict[str, Any]]:
    point = value.get('point') or value.get('line') or value.get('total') or value.get('hdp') or value.get('handicap') or market.get('point') or market.get('line') or market.get('total') or market.get('hdp') or market.get('handicap')
    out: list[dict[str, Any]] = []
    for key in ('home', 'draw', 'away', 'over', 'under', 'yes', 'no', '1', 'x', '2'):
        price = value.get(key)
        p = as_float(price)
        if p and p > 1.0:
            out.append({'name': key, 'price': price, 'point': point})
    if not out:
        for key in ('price', 'odds', 'decimal', 'value', 'decimal_odds'):
            p = as_float(value.get(key))
            if p and p > 1.0:
                out.append({'name': value.get('name') or value.get('selection') or value.get('outcome') or key, 'price': value.get(key), 'point': point})
                break
    return out


def iter_outcomes(market: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ('outcomes', 'selections', 'odds', 'prices'):
        value = market.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    out.extend(_flatten_price_dict(item, market) or [item])
        elif isinstance(value, dict):
            flat = _flatten_price_dict(value, market)
            if flat:
                out.extend(flat)
            else:
                for name, payload in value.items():
                    if isinstance(payload, dict):
                        item = dict(payload); item.setdefault('name', name); out.extend(_flatten_price_dict(item, market) or [item])
                    elif isinstance(payload, (int, float, str)) and as_float(payload) and as_float(payload) > 1.0:
                        out.append({'name': name, 'price': payload})
    if not out:
        out.extend(_flatten_price_dict(market, market))
    return out


def outcome_price(outcome: dict[str, Any]) -> float | None:
    for key in ('price', 'odds', 'decimal', 'value', 'decimal_odds'):
        price = as_float(outcome.get(key))
        if price and price > 1.0:
            return price
    return None


def outcome_point(outcome: dict[str, Any], market: dict[str, Any]) -> str:
    for key in ('point', 'line', 'total', 'handicap', 'hdp'):
        value = outcome.get(key)
        if value not in (None, ''):
            return str(value).strip()
    for key in ('point', 'line', 'total', 'handicap', 'hdp'):
        value = market.get(key)
        if value not in (None, ''):
            return str(value).strip()
    name = text_value(outcome.get('name') or outcome.get('selection'))
    found = re.search(r'([+-]?\d+(?:\.\d+)?)', name)
    return found.group(1) if found else ''


def extract_price_evidence(payload: Any, *, provider: str, account: str, default_event_id: str = '') -> dict[str, dict[str, Any]]:
    by_event: dict[str, dict[str, Any]] = {}
    for event in rows(payload):
        eid = event_id(event, default_event_id)
        if not eid:
            continue
        ev = by_event.setdefault(eid, {'price_confirmations': set(), 'books': set(), 'odds_sources': set(), 'samples': []})
        for book in iter_bookmakers(event, provider):
            book_name = text_value(book.get('name') or book.get('title') or book.get('key') or book.get('bookmaker')) or provider
            book_key = norm(book_name) or provider
            for market in iter_markets(book):
                family = market_family(market.get('key') or market.get('market_key') or market.get('market') or market.get('name') or market.get('type'))
                for outcome in iter_outcomes(market):
                    price = outcome_price(outcome)
                    if not price:
                        continue
                    selection = norm(outcome.get('name') or outcome.get('selection') or outcome.get('label') or outcome.get('outcome')) or 'selection'
                    point = outcome_point(outcome, market)
                    token = f'{provider}:{account}:{book_key}:{family}:{selection}:{point}'
                    ev['price_confirmations'].add(token)
                    ev['books'].add(book_key)
                    ev['odds_sources'].add(provider)
                    if len(ev['samples']) < 12:
                        ev['samples'].append({'provider': provider, 'account': account, 'bookmaker': book_key, 'family': family, 'selection': selection, 'point': point, 'price': price})
    return by_event


def merge_ev(dst: dict[str, dict[str, Any]], src: dict[str, dict[str, Any]]) -> None:
    for eid, ev in src.items():
        target = dst.setdefault(eid, {'price_confirmations': set(), 'books': set(), 'odds_sources': set(), 'samples': []})
        for key in ('price_confirmations', 'books', 'odds_sources'):
            target[key].update(ev.get(key) or set())
        for sample in ev.get('samples') or []:
            if len(target['samples']) < 12:
                target['samples'].append(sample)


def evidence_preview(evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for eid, ev in list(evidence.items())[:5]:
        out[eid] = {
            'price_confirmations': sorted(ev.get('price_confirmations') or [])[:20],
            'books': sorted(ev.get('books') or []),
            'odds_sources': sorted(ev.get('odds_sources') or []),
            'samples': list(ev.get('samples') or [])[:5],
        }
    return out


def chunk_event_ids(ids: list[str]) -> list[list[str]]:
    per_req = max(1, min(10, env_int('PRICE_BACKFILL_ODDS_API_IO_MAX_EVENT_IDS_PER_REQUEST', 10)))
    batches = max(1, env_int('PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT', 1))
    trimmed = ids[: per_req * batches]
    return [trimmed[i:i + per_req] for i in range(0, len(trimmed), per_req) if trimmed[i:i + per_req]]


async def fetch_odds_multi(client: httpx.AsyncClient, key: str, ids: list[str], bookmakers: str, account: str, idx: int) -> tuple[Any, dict[str, Any]]:
    if not key or not ids:
        return None, {'provider': 'odds_api_io', 'account': account, 'batch_index': idx, 'status': 'skipped', 'reason': 'missing_key_or_ids', 'rows': 0}
    started = datetime.now(UTC)
    try:
        resp = await client.get('https://api.odds-api.io/v3/odds/multi', params={'apiKey': key, 'eventIds': ','.join(ids[:10]), 'bookmakers': bookmakers})
        try:
            payload = resp.json()
        except Exception:
            payload = None
        return payload, {'provider': 'odds_api_io', 'account': account, 'batch_index': idx, 'status': resp.status_code, 'ok': resp.status_code == 200, 'event_ids_requested': len(ids[:10]), 'rows': len(rows(payload)), 'bookmakers': bookmakers, 'body_preview': resp.text[:500], 'duration_ms': round((datetime.now(UTC) - started).total_seconds() * 1000, 1)}
    except Exception as exc:
        return None, {'provider': 'odds_api_io', 'account': account, 'batch_index': idx, 'status': 'request_error', 'ok': False, 'event_ids_requested': len(ids[:10]), 'error': f'{type(exc).__name__}: {exc}'}


async def fetch_bzzoiro_odds(client: httpx.AsyncClient, key: str, event_id: str, idx: int) -> tuple[Any, dict[str, Any]]:
    if not key or not event_id:
        return None, {'provider': 'bzzoiro', 'event_id': event_id, 'batch_index': idx, 'status': 'skipped', 'reason': 'missing_key_or_event_id', 'rows': 0}
    base = (os.getenv('BZZOIRO_BASE_URL') or 'https://sports.bzzoiro.com/api/v2').rstrip('/')
    started = datetime.now(UTC)
    try:
        resp = await client.get(f'{base}/events/{event_id}/odds/', headers={'Authorization': f'Token {key}'})
        try:
            payload = resp.json()
        except Exception:
            payload = None
        return payload, {'provider': 'bzzoiro', 'event_id': event_id, 'batch_index': idx, 'status': resp.status_code, 'ok': resp.status_code == 200, 'rows': len(rows(payload)), 'body_preview': resp.text[:500], 'duration_ms': round((datetime.now(UTC) - started).total_seconds() * 1000, 1)}
    except Exception as exc:
        return None, {'provider': 'bzzoiro', 'event_id': event_id, 'batch_index': idx, 'status': 'request_error', 'ok': False, 'error': f'{type(exc).__name__}: {exc}'}


def source_ids(row: dict[str, Any]) -> dict[str, str]:
    raw = row.get('source_ids') if isinstance(row.get('source_ids'), dict) else {}
    out = {norm(k): str(v).strip() for k, v in raw.items() if str(v).strip()}
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    for src in ('odds_api_io', 'bzzoiro', 'sstats', 'sportlogic'):
        for key in (f'{src}_event_id', f'{src}_id', f'{src}_match_id'):
            if md.get(key) and src not in out:
                out[src] = str(md[key]).strip()
    return out


def merge_inventory(evidence: dict[str, dict[str, dict[str, Any]]], report: dict[str, Any]) -> None:
    now = datetime.now(UTC).isoformat()
    inv_path = DAY_INV_DIR / f'{target_date()}.json'
    inv = load_json(inv_path, {})
    if not isinstance(inv, dict):
        return
    matches = [row for row in inv.get('matches', []) if isinstance(row, dict)]
    min_price = publish_min_odds_sources()
    min_context = publish_min_context_sources()
    updated = newly_price_ready = newly_publish_ready = 0
    provider_updated = {'odds_api_io': 0, 'bzzoiro': 0}
    for row in matches:
        ids = source_ids(row)
        row_evidence: list[tuple[str, dict[str, Any]]] = []
        for provider in ('odds_api_io', 'bzzoiro'):
            eid = ids.get(provider)
            if eid and eid in evidence.get(provider, {}):
                row_evidence.append((provider, evidence[provider][eid]))
        if not row_evidence:
            continue
        md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        before_price_ready = as_int(md.get('price_confirmation_sources_count')) >= min_price
        cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        before_publish = bool(cov.get('ready_for_publish'))
        price_tokens = set(list_from_any(row.get('price_confirmations')))
        books = set(list_from_any(row.get('books')))
        odds_sources = set(list_from_any(row.get('odds_sources')))
        for provider, ev in row_evidence:
            price_tokens |= set(ev.get('price_confirmations') or set())
            books |= set(ev.get('books') or set())
            odds_sources |= set(ev.get('odds_sources') or set()) | {provider}
            provider_updated[provider] = provider_updated.get(provider, 0) + 1
            if ev.get('samples'):
                md[f'{provider}_backfill_samples'] = list(ev.get('samples') or [])[:12]
                md[f'{provider}_backfill_updated_utc'] = now
        row['price_confirmations'] = sorted(price_tokens)
        row['books'] = sorted(books)
        row['odds_sources'] = sorted(odds_sources)
        row['line_sources'] = sorted(set(list_from_any(row.get('line_sources'))) | odds_sources)
        odds_source_count = len({norm(x) for x in odds_sources if norm(x) in LIVE_ODDS_SOURCES})
        price_count = max(as_int(md.get('price_confirmation_sources_count')), len(price_tokens), len(books))
        context_count = max(as_int(md.get('context_sources_count')), as_int(md.get('confirmation_sources_count')), len(row.get('context_confirmations') or []), len(row.get('context_sources') or []))
        md.update({'price_confirmation_sources_count': price_count, 'price_sources_count': price_count, 'books_count': max(as_int(md.get('books_count')), len(books)), 'odds_sources_count': odds_source_count, 'independent_odds_sources_count': odds_source_count})
        row['metadata'] = md
        cov['odds'] = True
        cov['odds_2plus_sources'] = odds_source_count >= min_price
        cov['ready_for_model'] = bool(cov.get('ready_for_model')) or (price_count > 0 and context_count > 0)
        cov['ready_for_publish'] = price_count >= min_price and odds_source_count >= min_price and context_count >= min_context
        row['coverage'] = cov
        refresh = row.get('refresh') if isinstance(row.get('refresh'), dict) else {}
        refresh['last_odds_refresh_utc'] = now
        row['refresh'] = refresh
        row['price_backfill'] = {'updated_at_utc': now, 'needed': price_count < min_price or odds_source_count < min_price, 'executed': True, 'price_confirmations': price_count, 'odds_sources': odds_source_count, 'context_confirmations': context_count, 'source': 'odds_api_io+bzzoiro'}
        row['coverage_gaps'] = {'price_confirmations': price_count, 'odds_sources': odds_source_count, 'context_confirmations': context_count, 'need_price_confirmations': max(0, min_price - price_count), 'need_odds_sources': max(0, min_price - odds_source_count), 'need_context_confirmations': max(0, min_context - context_count), 'has_odds': price_count > 0, 'has_context': context_count > 0}
        updated += 1
        newly_price_ready += int((not before_price_ready) and price_count >= min_price)
        newly_publish_ready += int((not before_publish) and bool(cov.get('ready_for_publish')))
    counts = dict(inv.get('counts') or {})
    price2 = odds_source2 = context2 = odds_any = context_any = ready_model = ready_publish = 0
    for row in matches:
        md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        pc = max(as_int(md.get('price_confirmation_sources_count')), len(row.get('price_confirmations') or []), len(row.get('books') or []))
        oc = len({norm(x) for x in list_from_any(row.get('odds_sources')) + list_from_any(row.get('line_sources')) if norm(x) in LIVE_ODDS_SOURCES})
        cc = max(as_int(md.get('context_sources_count')), as_int(md.get('confirmation_sources_count')), len(row.get('context_confirmations') or []), len(row.get('context_sources') or []))
        odds_any += int(bool(cov.get('odds')) or pc > 0)
        context_any += int(bool(cov.get('context')) or cc > 0)
        price2 += int(pc >= min_price)
        odds_source2 += int(oc >= min_price)
        context2 += int(cc >= min_context)
        ready_model += int(bool(cov.get('ready_for_model')))
        ready_publish += int(pc >= min_price and oc >= min_price and cc >= min_context)
    counts.update({'matches_with_odds': odds_any, 'matches_with_context': context_any, 'matches_with_2plus_price_confirmations': price2, 'matches_with_2plus_odds_sources': odds_source2, 'matches_with_2plus_context_sources': context2, 'matches_ready_for_model': ready_model, 'matches_ready_for_publish': ready_publish, 'matches_missing_price_2plus': max(0, len(matches) - price2), 'matches_missing_odds_source_2plus': max(0, len(matches) - odds_source2), 'matches_missing_context_2plus': max(0, len(matches) - context2), 'price_backfill_execution_updated_utc': now})
    inv['counts'] = counts
    inv['updated_at_utc'] = now
    src = inv.setdefault('sources', {})
    if isinstance(src, dict):
        src['price_backfill_execution'] = {**{k: v for k, v in report.items() if k not in {'evidence_preview', 'attempts'}}, 'updated_at_utc': now, 'matches_updated': updated, 'provider_matches_updated': provider_updated, 'newly_price_ready': newly_price_ready, 'newly_publish_ready': newly_publish_ready}
    for path in [inv_path, DAY_INV_DIR / 'latest.json', DAY_INV_DIR / 'current.json', DAY_INV_DIR / 'today.json']:
        write_json(path, inv)
    summary = load_json(SUMMARY, {})
    if isinstance(summary, dict):
        summary['counts'] = counts
        summary['sources'] = dict(inv.get('sources') or {})
        summary['updated_at_utc'] = now
        write_json(SUMMARY, summary)
    report.update({'matches_updated': updated, 'provider_matches_updated': provider_updated, 'newly_price_ready': newly_price_ready, 'newly_publish_ready': newly_publish_ready, 'counts_after': counts})


def render(report: dict[str, Any]) -> str:
    counts = report.get('counts_after') if isinstance(report.get('counts_after'), dict) else {}
    return '\n'.join([
        '💸 Day inventory price backfill execution',
        f"• status: {report.get('status')}",
        f"• odds_api_requests_used: {report.get('odds_api_requests_used')}",
        f"• bzzoiro_requests_used: {report.get('bzzoiro_requests_used')}",
        f"• event_ids_requested: {report.get('event_ids_requested')}",
        f"• bzzoiro_event_ids_requested: {report.get('bzzoiro_event_ids_requested')}",
        f"• event_ids_with_price_tokens: {report.get('event_ids_with_price_tokens')}",
        f"• bzzoiro_event_ids_with_price_tokens: {report.get('bzzoiro_event_ids_with_price_tokens')}",
        f"• price_tokens_added: {report.get('price_tokens_added')}",
        f"• books_found: {report.get('books_found')}",
        f"• matches_updated: {report.get('matches_updated')}",
        f"• newly_price_ready: {report.get('newly_price_ready')}",
        f"• newly_publish_ready: {report.get('newly_publish_ready')}",
        f"• matches_with_2plus_price_confirmations: {counts.get('matches_with_2plus_price_confirmations')}",
        f"• matches_ready_for_publish: {counts.get('matches_ready_for_publish')}",
    ]) + '\n'


async def main_async() -> int:
    now = datetime.now(UTC)
    if not env_bool('PRICE_BACKFILL_EXECUTE_ENABLED', False):
        report = {'status': 'skipped', 'reason': 'PRICE_BACKFILL_EXECUTE_ENABLED is not true', 'updated_at_utc': now.isoformat(), 'requests_used': 0}
        write_json(OUT_JSON, report); OUT_TXT.write_text(render(report), encoding='utf-8'); print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)); return 0
    plan = load_json(PLAN_JSON, {})
    odds_ids = unique([str(x).strip() for x in (plan.get('odds_api_io_event_ids_selected') or plan.get('odds_api_io_event_ids') or []) if str(x).strip()])
    batches = chunk_event_ids(odds_ids)
    requested_odds_ids = [x for batch in batches for x in batch]
    bzz_targets = plan.get('bzzoiro_targets') if isinstance(plan.get('bzzoiro_targets'), list) else []
    bzz_ids = unique([str(((x.get('source_ids') or {}).get('bzzoiro') if isinstance(x, dict) else '') or '').strip() for x in bzz_targets])
    bzz_limit = max(0, env_int('BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT', 10))
    bzz_ids = bzz_ids[:bzz_limit]
    timeout = httpx.Timeout(float(os.getenv('PRICE_BACKFILL_TIMEOUT_SECONDS', '16')), connect=5.0)
    attempts: list[dict[str, Any]] = []
    evidence = {'odds_api_io': {}, 'bzzoiro': {}}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        tasks = []
        for idx, batch in enumerate(batches):
            tasks.append(fetch_odds_multi(client, secret('ODDS_API_IO_KEY'), batch, os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT1') or 'Bet365,Unibet', 'odds_api_io_account1', idx))
            tasks.append(fetch_odds_multi(client, secret('ODDS_API_IO_KEY_2', 'ODDS_API_IO_KEY2'), batch, os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT2') or 'Betfair Exchange,Sbobet', 'odds_api_io_account2', idx))
        if env_bool('BZZOIRO_PRICE_BACKFILL_ENABLED', True):
            bzz_key = secret('BZZOIRO_API_KEY')
            for idx, eid in enumerate(bzz_ids):
                tasks.append(fetch_bzzoiro_odds(client, bzz_key, eid, idx))
        results = await asyncio.gather(*tasks) if tasks else []
    odds_req = bzz_req = 0
    for payload, attempt in results:
        attempts.append(attempt)
        provider = str(attempt.get('provider') or '')
        if attempt.get('ok') and provider == 'odds_api_io':
            odds_req += 1
        if attempt.get('ok') and provider == 'bzzoiro':
            bzz_req += 1
        if provider == 'odds_api_io':
            merge_ev(evidence['odds_api_io'], extract_price_evidence(payload, provider='odds_api_io', account=str(attempt.get('account') or 'odds_api_io')))
        elif provider == 'bzzoiro':
            merge_ev(evidence['bzzoiro'], extract_price_evidence(payload, provider='bzzoiro', account='bzzoiro_v2', default_event_id=str(attempt.get('event_id') or '')))
    all_ev = {**evidence['odds_api_io'], **{f'bzzoiro:{k}': v for k, v in evidence['bzzoiro'].items()}}
    price_tokens_added = sum(len(ev.get('price_confirmations') or set()) for ev in evidence['odds_api_io'].values()) + sum(len(ev.get('price_confirmations') or set()) for ev in evidence['bzzoiro'].values())
    books_found = sum(len(ev.get('books') or set()) for ev in evidence['odds_api_io'].values()) + sum(len(ev.get('books') or set()) for ev in evidence['bzzoiro'].values())
    report: dict[str, Any] = {
        'status': 'ok', 'updated_at_utc': now.isoformat(), 'plan_path': str(PLAN_JSON),
        'event_ids_planned': len(odds_ids), 'event_ids_requested': len(requested_odds_ids),
        'bzzoiro_event_ids_requested': len(bzz_ids), 'event_ids_with_prices': len(evidence['odds_api_io']),
        'event_ids_with_price_tokens': sum(1 for ev in evidence['odds_api_io'].values() if ev.get('price_confirmations')),
        'bzzoiro_event_ids_with_price_tokens': sum(1 for ev in evidence['bzzoiro'].values() if ev.get('price_confirmations')),
        'price_tokens_added': price_tokens_added, 'books_found': books_found,
        'odds_api_requests_used': odds_req, 'bzzoiro_requests_used': bzz_req, 'requests_used': odds_req + bzz_req,
        'batches_per_account': len(batches), 'max_event_ids_per_request': max((len(x) for x in batches), default=0),
        'attempts': attempts, 'evidence_preview': evidence_preview(all_ev),
    }
    merge_inventory(evidence, report)
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
