from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.schemas import Match, Offer
from app.utils import normalize_bookmaker_name, parse_datetime, score_event_match

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / '.data' / 'exports'
REPORT_PATH = EXPORT_DIR / 'latest-sstats-bzzoiro-odds-merge.json'
_INSTALLED = False


def truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def fnum(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        return float(str(value).replace(',', '.'))
    except Exception:
        return None


def norm(value: Any) -> str:
    return ''.join(ch for ch in str(value or '').lower() if ch.isalnum())


def write_report(payload: dict[str, Any]) -> None:
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('data', 'results', 'items', 'odds', 'bookmakers', 'markets'):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
            if isinstance(val, dict):
                nested = rows(val)
                if nested:
                    return nested
    return []


def artifact_ids() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = defaultdict(dict)
    files = ['latest-day-inventory-core-crosswalk.json', 'latest-progressive-coverage-plan.json', 'latest-sstats-crosswalk.json']

    def visit(obj: Any) -> None:
        if isinstance(obj, dict):
            mk = str(obj.get('match_key') or '').strip()
            if mk:
                ids = obj.get('ids') if isinstance(obj.get('ids'), dict) else obj.get('source_ids') if isinstance(obj.get('source_ids'), dict) else {}
                if isinstance(ids, dict):
                    for k, v in ids.items():
                        if str(v or '').strip():
                            out[mk][str(k).lower()] = str(v).strip()
                for provider in ('sstats', 'bzzoiro'):
                    for key in (provider, f'{provider}_id', f'{provider}_game_id', f'{provider}_event_id'):
                        if str(obj.get(key) or '').strip():
                            out[mk][provider] = str(obj.get(key)).strip()
            for val in obj.values():
                visit(val)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    for name in files:
        try:
            path = EXPORT_DIR / name
            if path.exists():
                visit(json.loads(path.read_text(encoding='utf-8')))
        except Exception:
            pass
    return dict(out)


def provider_id(match: Match, provider: str, amap: dict[str, dict[str, str]]) -> str | None:
    provider = provider.lower()
    meta = getattr(match, 'metadata', {}) or {}

    def scan(obj: Any) -> str | None:
        if isinstance(obj, dict):
            ids = obj.get('ids') if isinstance(obj.get('ids'), dict) else obj.get('source_ids') if isinstance(obj.get('source_ids'), dict) else None
            if isinstance(ids, dict) and str(ids.get(provider) or '').strip():
                return str(ids.get(provider)).strip()
            for key, val in obj.items():
                low = str(key).lower()
                if provider in low and ('id' in low) and str(val or '').strip() and not isinstance(val, (dict, list)):
                    return str(val).strip()
            for val in obj.values():
                found = scan(val)
                if found:
                    return found
        return None

    return scan(meta) or amap.get(match.match_key, {}).get(provider)


def source_count(offers: list[Offer]) -> int:
    return len({str(o.source or '').lower() for o in offers if str(o.source or '').strip()})


def book_count(offers: list[Offer]) -> int:
    return len({str(o.bookmaker or '').lower() for o in offers if str(o.bookmaker or '').strip()})


def selected_matches(matches: list[Match], base: dict[str, list[Offer]]) -> list[Match]:
    now = datetime.now(UTC)
    pool = [m for m in matches if getattr(m, 'sport_key', '') == 'soccer']
    pool.sort(key=lambda m: (source_count(base.get(m.match_key, [])) >= 2, abs((m.commence_time.astimezone(UTC) - now).total_seconds()), m.league_name.lower()))
    try:
        limit = int(float(os.getenv('CORE_ODDS_PATCH_MATCH_LIMIT', '160')))
    except Exception:
        limit = 160
    return pool[:max(1, limit)]


def family_market(market: Any, outcome: Any = '') -> str | None:
    text = f'{market} {outcome}'.lower()
    flat = norm(text)
    if '1x2' in flat or 'matchwinner' in flat or 'fulltimeresult' in flat:
        return 'h2h'
    if 'overunder' in flat or 'total' in flat or 'totals' in flat or 'тотал' in text or 'тб' in text or 'тм' in text:
        return 'totals'
    if 'handicap' in flat or 'spread' in flat or 'фора' in text:
        return 'spreads'
    if 'btts' in flat or 'bothteamstoscore' in flat:
        return 'btts'
    return None


def line_from(*vals: Any) -> float | None:
    for val in vals:
        m = re.search(r'(\d+(?:[\.,]\d+)?)', str(val or ''))
        if m:
            return fnum(m.group(1))
    return None


def selection_for(fam: str, outcome: Any, match: Match) -> tuple[str | None, str | None]:
    raw = str(outcome or '').strip()
    low = raw.lower()
    flat = norm(raw)
    if fam == 'h2h':
        if flat in {'home', '1', 'p1'} or low == 'п1':
            return match.home_team, 'home'
        if flat in {'away', '2', 'p2'} or low == 'п2':
            return match.away_team, 'away'
        if flat in {'draw', 'x'} or low in {'x', 'х', 'ничья'}:
            return 'Draw', None
    if fam == 'totals':
        if 'under' in low or flat.startswith('u') or 'тм' in low or 'меньше' in low:
            return 'Under', None
        if 'over' in low or flat.startswith('o') or 'тб' in low or 'больше' in low:
            return 'Over', None
    if fam == 'spreads':
        if flat in {'home', '1', 'p1'} or norm(raw) == norm(match.home_team):
            return match.home_team, 'home'
        if flat in {'away', '2', 'p2'} or norm(raw) == norm(match.away_team):
            return match.away_team, 'away'
    if fam == 'btts':
        if flat in {'yes', 'y'}:
            return 'Yes', None
        if flat in {'no', 'n'}:
            return 'No', None
    return None, None


def _line_price_sane(source: str, fam: str, sel: str, price: float, point: float | None) -> bool:
    """Reject clearly misparsed current-line prices before they poison consensus.

    SStats /Odds values are often historical/closing rows and sometimes nested
    fields from another market. They must not be used as live/current price
    confirmation unless explicitly enabled. Bzzoiro and odds-api.io still pass
    normal prices, while obviously impossible totals/spreads are blocked.
    """
    if source == 'sstats' and not truthy(os.getenv('SSTATS_CURRENT_ODDS_AS_LINE_SOURCE'), False):
        return False
    if fam in {'totals', 'spreads', 'btts'} and price > 6.0:
        return False
    if fam == 'totals' and point is not None and abs(float(point) - 2.5) <= 0.01 and price > 4.25:
        return False
    if fam == 'spreads' and price > 8.0:
        return False
    return True


def add_offer(out: list[Offer], seen: set[tuple[Any, ...]], source: str, book: Any, fam: str, sel: str, price: Any, match: Match, point: float | None, side: str | None, event_id: str | None) -> None:
    p = fnum(price)
    if p is None or p <= 1.0 or p > 100:
        return
    if not _line_price_sane(source, fam, sel, float(p), point):
        return
    b = normalize_bookmaker_name(str(book or '')) or source
    key = (source, b, fam, sel, point, side, round(p, 4))
    if key in seen:
        return
    seen.add(key)
    out.append(Offer(source=source, bookmaker=b, family=fam, selection=sel, price=float(p), point=point, team_side=side, market_name=fam, market_key=fam, source_event_id=event_id, metadata={'provider_source': source}))


def parse_bzzoiro_compact_odds(payload: Any, match: Match, source: str, event_id: str | None = None) -> list[Offer]:
    """Parse Bzzoiro v2 compact odds maps.

    Docs expose /events/{id}/odds/ as {"odds": {home_win, draw, over_25_goals, ...}}
    and /events/{id}/odds/comparison/ as market/bookmaker/outcome maps.  The
    previous generic walker missed compact keys like over_25_goals because the
    market/outcome is encoded in the key, not in a row field.
    """
    out: list[Offer] = []
    seen: set[tuple[Any, ...]] = set()

    def emit(book: Any, fam: str, sel: str, price: Any, point: float | None = None, side: str | None = None) -> None:
        add_offer(out, seen, source, book or source, fam, sel, price, match, point, side, event_id)

    def key_offer(key: str, value: Any, book: Any = None) -> None:
        low = str(key or '').lower()
        price = value.get('decimal_odds') if isinstance(value, dict) else value.get('price') if isinstance(value, dict) else value.get('odds') if isinstance(value, dict) else value
        if price in (None, ''):
            return
        if low in {'home_win', 'home', 'home_winner', 'homewin', 'home_win_odds'}:
            emit(book, 'h2h', match.home_team, price, None, 'home')
            return
        if low in {'away_win', 'away', 'away_winner', 'awaywin', 'away_win_odds'}:
            emit(book, 'h2h', match.away_team, price, None, 'away')
            return
        if low in {'draw', 'x', 'draw_odds'}:
            emit(book, 'h2h', 'Draw', price)
            return
        m = re.search(r'^(over|under)[_\- ]?(\d+)(?:[_\- ]?(\d+))?[_\- ]?(?:goals?)?$', low)
        if not m:
            m = re.search(r'^(over|under)[_\- ]?(\d)(\d)[_\- ]?(?:goals?)?$', low)
        if m:
            side = 'Over' if m.group(1) == 'over' else 'Under'
            if len(m.groups()) >= 3 and m.group(3):
                point = fnum(f'{m.group(2)}.{m.group(3)}')
            else:
                token = m.group(2)
                point = fnum(f'{token[0]}.{token[1:]}') if len(token) > 1 else fnum(token)
            emit(book, 'totals', side, price, point)
            return
        m = re.search(r'^(?:over|under)[_\- ]?(\d+(?:[\._]\d+))[_\- ]?(?:goals?)?$', low)
        if m:
            point = fnum(m.group(1).replace('_', '.'))
            emit(book, 'totals', 'Over' if low.startswith('over') else 'Under', price, point)
            return
        if low in {'btts_yes', 'both_teams_to_score_yes'}:
            emit(book, 'btts', 'Yes', price)
            return
        if low in {'btts_no', 'both_teams_to_score_no'}:
            emit(book, 'btts', 'No', price)
            return

    def walk(obj: Any, book: Any = None, market: Any = None) -> None:
        if isinstance(obj, list):
            for item in obj:
                walk(item, book, market)
            return
        if not isinstance(obj, dict):
            return
        # Compact /events/{id}/odds/ envelope.
        odds_map = obj.get('odds') if isinstance(obj.get('odds'), dict) else None
        if odds_map:
            for key, value in odds_map.items():
                key_offer(str(key), value, book)
        # /odds/best/ rows.
        if isinstance(obj.get('best_odds'), list):
            market_name = obj.get('market') or market
            for item in obj.get('best_odds') or []:
                if isinstance(item, dict):
                    outcome = item.get('outcome') or item.get('outcome_name')
                    price = item.get('decimal_odds') or item.get('price') or item.get('odds')
                    book_name = item.get('bookmaker_slug') or item.get('bookmaker_name') or book
                    fam = family_market(market_name, outcome)
                    if fam:
                        sel, side = selection_for(fam, outcome, match)
                        point = line_from(market_name, outcome) if fam == 'totals' else None
                        if sel:
                            add_offer(out, seen, source, book_name, fam, sel, price, match, point, side, event_id or str(obj.get('event_id') or ''))
        # Comparison maps: markets -> market -> bookmaker -> outcome.
        for key, value in obj.items():
            if key in {'odds', 'best_odds'}:
                continue
            if isinstance(value, dict):
                next_market = market
                next_book = book
                if family_market(key):
                    next_market = key
                elif market and norm(key) not in {'outcomes', 'selections', 'values', 'data', 'results'}:
                    # When under a market, a dict key is often a bookmaker slug.
                    next_book = key
                # Scalar children under market/bookmaker are often outcome -> price.
                if next_market and all(not isinstance(v, (dict, list)) for v in value.values()):
                    for outcome, price in value.items():
                        fam = family_market(next_market, outcome)
                        if not fam:
                            key_offer(str(outcome), price, next_book)
                            continue
                        sel, side = selection_for(fam, outcome, match)
                        point = line_from(next_market, outcome) if fam == 'totals' else None
                        if sel:
                            add_offer(out, seen, source, next_book, fam, sel, price, match, point, side, event_id)
                walk(value, next_book, next_market)
            elif market and key not in {'event_id', 'id'}:
                fam = family_market(market, key)
                if fam:
                    sel, side = selection_for(fam, key, match)
                    point = line_from(market, key) if fam == 'totals' else None
                    if sel:
                        add_offer(out, seen, source, book, fam, sel, value, match, point, side, event_id)
                else:
                    key_offer(str(key), value, book)
            else:
                key_offer(str(key), value, book)

    walk(payload)
    return out


def parse_any(payload: Any, match: Match, source: str, event_id: str | None = None) -> list[Offer]:
    out: list[Offer] = []
    seen: set[tuple[Any, ...]] = set()

    # First handle documented Bzzoiro v2 compact maps, then fall back to the generic walker.
    for offer in parse_bzzoiro_compact_odds(payload, match, source, event_id):
        sig = (offer.source, offer.bookmaker, offer.family, offer.selection, offer.point, offer.team_side, round(float(offer.price), 4))
        if sig not in seen:
            seen.add(sig)
            out.append(offer)

    def emit(book: Any, market: Any, outcome: Any, price: Any, point: Any = None) -> None:
        fam = family_market(market, outcome)
        if not fam:
            return
        pt = fnum(point) or (line_from(market, outcome) if fam == 'totals' else None)
        sel, side = selection_for(fam, outcome, match)
        if not sel:
            return
        add_offer(out, seen, source, book, fam, sel, price, match, pt, side, event_id)

    def walk(obj: Any, inh: dict[str, Any]) -> None:
        if isinstance(obj, list):
            for x in obj:
                walk(x, inh)
            return
        if not isinstance(obj, dict):
            return
        row = dict(inh)
        for k, dst in [('bookmakerName','book'),('bookmaker','book'),('bookmaker_name','book'),('bookmaker_slug','book'),('marketName','market'),('market','market'),('market_key','market'),('marketId','market'),('name','outcome'),('selection','outcome'),('outcome','outcome'),('label','outcome'),('line','point'),('point','point'),('handicap','point'),('option_value','point')]:
            if k in obj and not isinstance(obj.get(k), (dict, list)):
                row[dst] = obj.get(k)
        if isinstance(obj.get('market'), dict):
            m = obj['market']
            row['market'] = m.get('key') or m.get('name') or m.get('label') or row.get('market')
        if isinstance(obj.get('bookmaker'), dict):
            b = obj['bookmaker']
            row['book'] = b.get('slug') or b.get('name') or row.get('book')
        price = None
        for pk in ('value','price','odds','decimal','decimal_odds','best_decimal_odds'):
            if obj.get(pk) not in (None, '') and not isinstance(obj.get(pk), (dict, list)):
                price = obj.get(pk)
                break
        if price not in (None, '') and (row.get('market') or row.get('outcome')):
            emit(row.get('book'), row.get('market'), row.get('outcome'), price, row.get('point'))
        if isinstance(obj.get('odds'), list) and (obj.get('marketName') or obj.get('marketId') or obj.get('market')):
            for x in obj.get('odds') or []:
                if isinstance(x, dict):
                    emit(row.get('book'), obj.get('marketName') or obj.get('marketId') or row.get('market'), x.get('name') or x.get('outcome') or x.get('option_name'), x.get('value') or x.get('price') or x.get('odds') or x.get('decimal_odds'), x.get('point') or x.get('line') or x.get('option_value'))
        for key, child in obj.items():
            if not isinstance(child, (dict, list)):
                continue
            nxt = dict(row)
            if family_market(key):
                nxt.setdefault('market', key)
            elif nxt.get('market') and not nxt.get('outcome') and norm(key) not in {'bookmakers','books','odds','data','results'}:
                nxt.setdefault('outcome', key)
            elif isinstance(child, dict) and any(pk in child for pk in ('price','odds','decimal','decimal_odds','value','best_decimal_odds')) and norm(key) not in {'markets','outcomes'}:
                nxt.setdefault('book', key)
            walk(child, nxt)

    walk(payload, {})
    return out

def merge(base: dict[str, list[Offer]], extra: dict[str, list[Offer]]) -> int:
    added = 0
    for mk, offers in extra.items():
        bucket = base.setdefault(mk, [])
        seen = {(o.source, o.bookmaker, o.family, o.selection, o.point, o.team_side, round(float(o.price), 4)) for o in bucket}
        for o in offers:
            sig = (o.source, o.bookmaker, o.family, o.selection, o.point, o.team_side, round(float(o.price), 4))
            if sig in seen:
                continue
            seen.add(sig)
            bucket.append(o)
            added += 1
    return added


async def fetch_sstats(settings: Any, matches: list[Match], base: dict[str, list[Offer]], amap: dict[str, dict[str, str]]) -> tuple[dict[str, list[Offer]], dict[str, Any]]:
    key = os.getenv('SSTATS_API_KEY') or getattr(settings, 'sstats_api_key', None)
    stats = {
        'enabled': bool(key) and truthy(os.getenv('SSTATS_CURRENT_ODDS_AS_LINE_SOURCE'), False),
        'requests': 0,
        'response_errors': 0,
        'matched_ids': 0,
        'offers_parsed': 0,
        'skipped_no_id': 0,
        'skip_reason': None,
    }
    if not key:
        stats['skip_reason'] = 'missing_key'
        return {}, stats
    if not truthy(os.getenv('SSTATS_CURRENT_ODDS_AS_LINE_SOURCE'), False):
        stats['skip_reason'] = 'sstats_kept_as_context_not_current_line_source'
        return {}, stats
    base_url = str(os.getenv('SSTATS_BASE_URL') or getattr(settings, 'sstats_base_url', 'https://api.sstats.net')).rstrip('/')
    out: dict[str, list[Offer]] = {}
    limit = int(float(os.getenv('SSTATS_ODDS_RESCUE_LIMIT_PER_RUN', '120')))
    count = 0
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        for match in selected_matches(matches, base):
            if source_count(base.get(match.match_key, [])) >= 2:
                continue
            gid = provider_id(match, 'sstats', amap)
            if not gid:
                stats['skipped_no_id'] += 1
                continue
            if count >= limit:
                break
            count += 1
            stats['matched_ids'] += 1
            stats['requests'] += 1
            try:
                resp = await client.get(f'{base_url}/Odds/{gid}', params={'apikey': key})
                if resp.status_code >= 400:
                    stats['response_errors'] += 1
                    continue
                payload = resp.json()
            except Exception:
                stats['response_errors'] += 1
                continue
            offers = parse_any(payload.get('data') if isinstance(payload, dict) and 'data' in payload else payload, match, 'sstats', str(gid))
            if offers:
                out[match.match_key] = offers
                stats['offers_parsed'] += len(offers)
    return out, stats


async def fetch_bzzoiro(settings: Any, matches: list[Match], base: dict[str, list[Offer]], amap: dict[str, dict[str, str]]) -> tuple[dict[str, list[Offer]], dict[str, Any]]:
    key = os.getenv('BZZOIRO_API_KEY') or getattr(settings, 'bzzoiro_api_key', None)
    stats = {'enabled': bool(key), 'requests': 0, 'response_errors': 0, 'events_fetched': 0, 'events_matched': 0, 'event_odds_requests': 0, 'event_comparison_requests': 0, 'offers_parsed': 0, 'offers_from_compact_odds': 0, 'offers_from_comparison': 0}
    if not key:
        return {}, stats
    api = (os.getenv('BZZOIRO_BASE_URL') or 'https://sports.bzzoiro.com/api/v2').rstrip('/')
    headers = {'Authorization': f'Token {key}'}
    target = selected_matches(matches, base)
    if not target:
        return {}, stats
    d1 = min(m.commence_time.astimezone(UTC).date() for m in target).isoformat()
    d2 = (max(m.commence_time.astimezone(UTC).date() for m in target) + timedelta(days=1)).isoformat()
    out: dict[str, list[Offer]] = {}
    async with httpx.AsyncClient(timeout=14, follow_redirects=True) as client:
        events: list[dict[str, Any]] = []
        offset = 0
        while offset < 600:
            stats['requests'] += 1
            try:
                resp = await client.get(f'{api}/events/', headers=headers, params={'date_from': d1, 'date_to': d2, 'status': 'notstarted', 'limit': 200, 'offset': offset})
                if resp.status_code >= 400:
                    stats['response_errors'] += 1
                    break
                batch = rows(resp.json())
            except Exception:
                stats['response_errors'] += 1
                break
            if not batch:
                break
            events.extend(batch)
            if len(batch) < 200:
                break
            offset += 200
        stats['events_fetched'] = len(events)
        for match in target:
            if source_count(base.get(match.match_key, [])) >= 2:
                continue
            eid = provider_id(match, 'bzzoiro', amap)
            event = next((e for e in events if str(e.get('id') or '') == str(eid)), None) if eid else None
            if event is None:
                best = (0.0, None)
                for row in events:
                    try:
                        st = parse_datetime(row.get('event_date') or row.get('date') or row.get('start_time'))
                    except Exception:
                        continue
                    home = str(row.get('home_team') or row.get('home') or '')
                    away = str(row.get('away_team') or row.get('away') or '')
                    league = str(row.get('league_name') or row.get('league') or '')
                    score, _ = score_event_match('soccer', match.home_team, match.away_team, match.commence_time, match.league_name, home, away, st, league, exact_tolerance_hours=6, fuzzy_tolerance_hours=24)
                    if score > best[0]:
                        best = (score, row)
                if best[0] >= 62:
                    event = best[1]
            if not event:
                continue
            event_id = str(event.get('id') or '').strip()
            if not event_id:
                continue
            stats['events_matched'] += 1
            collected: list[Offer] = []
            for path, bucket in ((f'/events/{event_id}/odds/', 'offers_from_compact_odds'), (f'/events/{event_id}/odds/comparison/', 'offers_from_comparison')):
                stats['requests'] += 1
                if path.endswith('/odds/'):
                    stats['event_odds_requests'] += 1
                else:
                    stats['event_comparison_requests'] += 1
                try:
                    resp = await client.get(f'{api}{path}', headers=headers)
                    if resp.status_code == 404:
                        continue
                    if resp.status_code >= 400:
                        stats['response_errors'] += 1
                        continue
                    payload = resp.json()
                except Exception:
                    stats['response_errors'] += 1
                    continue
                offers = parse_any(payload, match, 'bzzoiro', event_id)
                if offers:
                    stats[bucket] += len(offers)
                    collected.extend(offers)
            if collected:
                out[match.match_key] = collected
                stats['offers_parsed'] += len(collected)
    return out, stats


async def fetch_offers_wrapped(self: Any, matches: list[Match]):
    original = getattr(self.__class__, '_harizon_original_fetch_offers_sstats_bzzoiro', None)
    base, stats, preview = await original(self, matches)
    base = {k: list(v) for k, v in dict(base or {}).items()}
    stats = dict(stats or {})
    preview = dict(preview or {})
    amap = artifact_ids()
    report: dict[str, Any] = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'before_matches': len([1 for v in base.values() if v]),
        'before_2plus_sources': sum(1 for v in base.values() if source_count(v) >= 2),
        'sstats_current_odds_as_line_source': truthy(os.getenv('SSTATS_CURRENT_ODDS_AS_LINE_SOURCE'), False),
    }
    for name, func in (('sstats', fetch_sstats), ('bzzoiro', fetch_bzzoiro)):
        try:
            extra, sub = await func(getattr(self, 'settings', None), matches, base, amap)
            sub['offers_added_to_pool'] = merge(base, extra)
            report[name] = sub
        except Exception as exc:
            report[name] = {'error': f'{type(exc).__name__}: {exc}'}
    report['after_matches'] = len([1 for v in base.values() if v])
    report['after_2plus_sources'] = sum(1 for v in base.values() if source_count(v) >= 2)
    report['after_2plus_books'] = sum(1 for v in base.values() if book_count(v) >= 2)
    stats['sstats_bzzoiro_odds_merge'] = report
    stats['offers_parsed'] = sum(len(v) for v in base.values())
    stats['matches_with_2plus_sources_after_merge'] = report['after_2plus_sources']
    stats['matches_with_2plus_books_after_merge'] = report['after_2plus_books']
    preview['sstats_bzzoiro_odds_merge'] = report
    write_report(report)
    return base, stats, preview


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    _INSTALLED = True
    os.environ.setdefault('CORE_ODDS_PATCH_MATCH_LIMIT', '160')
    os.environ.setdefault('SSTATS_CURRENT_ODDS_AS_LINE_SOURCE', 'false')
    os.environ.setdefault('SSTATS_ODDS_RESCUE_LIMIT_PER_RUN', '120')
    try:
        from app.providers.odds_api_io import OddsApiIoProvider
        if not getattr(OddsApiIoProvider.fetch_offers, '_harizon_sstats_bzzoiro_merge', False):
            OddsApiIoProvider._harizon_original_fetch_offers_sstats_bzzoiro = OddsApiIoProvider.fetch_offers
            fetch_offers_wrapped._harizon_sstats_bzzoiro_merge = True  # type: ignore[attr-defined]
            OddsApiIoProvider.fetch_offers = fetch_offers_wrapped  # type: ignore[assignment]
    except Exception as exc:
        result = {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}
        write_report(result)
        return result
    result = {
        'status': 'installed',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'sstats_current_odds_as_line_source': truthy(os.getenv('SSTATS_CURRENT_ODDS_AS_LINE_SOURCE'), False),
        'sstats_policy': 'context_only_by_default',
    }
    write_report(result)
    return result
