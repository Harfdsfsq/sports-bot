from __future__ import annotations

"""Fetch Bzzoiro odds details for matched A-tier targets.

Runs after bzzoiro_targeted_odds_confirmation has matched target rows to
Bzzoiro event ids. It requests event odds/detail endpoints only for matched
items and persists parsed offers for downstream 2-source promotion diagnostics.
No publication side effects.
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

EXPORT = Path('.data/exports')
INP = EXPORT / 'latest-bzzoiro-targeted-odds-confirmation.json'
OUT = EXPORT / 'latest-bzzoiro-targeted-odds-detail.json'
ODDS = EXPORT / 'latest-bzzoiro-odds.json'
RAW = EXPORT / 'latest-bzzoiro-odds-raw.json'


def _load(path: Path, default: Any = None) -> Any:
    try: return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception: return {} if default is None else default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)+'\n', encoding='utf-8')


def _num(v: Any) -> float | None:
    try:
        if v in (None, ''): return None
        x = float(str(v).replace(',', '.'))
        return x if x > 1.0 else None
    except Exception: return None


def _event_id(row: dict[str, Any]) -> str:
    for key in ('bzzoiro_event_id','event_id','id','api_id','source_event_id'):
        v = row.get(key)
        if v not in (None, ''): return str(v)
    raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
    for key in ('id','api_id','event_id'):
        v = raw.get(key)
        if v not in (None, ''): return str(v)
    return ''


def _offers_from_payload(payload: Any, *, event_id: str, match_key: str = '') -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    def add(family: str, selection: str, price: Any, point: float | None = None, bookmaker: str = 'bzzoiro-detail') -> None:
        p = _num(price)
        if p is not None:
            offers.append({'source':'bzzoiro','bookmaker':bookmaker,'family':family,'selection':selection,'price':p,'point':point,'source_event_id':event_id,'match_key':match_key})
    def walk(obj: Any, market_hint: str = '', outcome_hint: str = '', book_hint: str = '') -> None:
        if isinstance(obj, dict):
            odds = obj.get('odds') if isinstance(obj.get('odds'), dict) else obj
            add('h2h','Home', odds.get('home_win') or odds.get('home') or odds.get('odds_home') or odds.get('home_odds'))
            add('h2h','Draw', odds.get('draw') or odds.get('odds_draw') or odds.get('draw_odds'))
            add('h2h','Away', odds.get('away_win') or odds.get('away') or odds.get('odds_away') or odds.get('away_odds'))
            for point, suffixes in ((1.5,('15','1_5')), (2.5,('25','2_5')), (3.5,('35','3_5'))):
                for suffix in suffixes:
                    add('totals','Over', odds.get(f'over_{suffix}_goals') or odds.get(f'odds_over_{suffix}'), point)
                    add('totals','Under', odds.get(f'under_{suffix}_goals') or odds.get(f'odds_under_{suffix}'), point)
            market = str(obj.get('market') or obj.get('market_key') or obj.get('market_name') or market_hint or '')
            outcome = str(obj.get('outcome') or obj.get('selection') or obj.get('name') or outcome_hint or '')
            book = str(obj.get('bookmaker') or obj.get('bookmaker_name') or obj.get('bookmaker_slug') or book_hint or 'bzzoiro-detail')
            price = obj.get('price') or obj.get('decimal') or obj.get('decimal_odds') or obj.get('odds')
            point = _num(obj.get('line') or obj.get('point'))
            mn, on = market.lower(), outcome.lower()
            if price not in (None, '') and outcome:
                if 'total' in mn or 'over' in mn or 'goal' in mn:
                    if 'under' in on or on.startswith('u'): add('totals','Under', price, point, book)
                    elif 'over' in on or on.startswith('o'): add('totals','Over', price, point, book)
                elif mn in {'1x2','h2h','matchwinner','match winner'} or 'winner' in mn:
                    if on in {'home','1','home win','home_win'}: add('h2h','Home', price, None, book)
                    elif on in {'draw','x'}: add('h2h','Draw', price, None, book)
                    elif on in {'away','2','away win','away_win'}: add('h2h','Away', price, None, book)
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    walk(v, market or str(k), outcome, book)
        elif isinstance(obj, list):
            for item in obj: walk(item, market_hint, outcome_hint, book_hint)
    walk(payload)
    # de-dupe
    seen=set(); out=[]
    for o in offers:
        key=(o.get('bookmaker'),o.get('family'),o.get('selection'),o.get('point'),o.get('price'),o.get('source_event_id'))
        if key in seen: continue
        seen.add(key); out.append(o)
    return out


async def _fetch(client: httpx.AsyncClient, base: str, event_id: str, headers: dict[str,str], path: str) -> Any:
    try:
        r = await client.get(f'{base}/events/{event_id}/{path}/', headers=headers)
        if r.status_code == 200: return r.json()
        return {'http_status': r.status_code, 'body_preview': r.text[:300]}
    except Exception as exc:
        return {'error': f'{type(exc).__name__}: {exc}'}


async def _main() -> int:
    key = os.getenv('BZZOIRO_API_KEY')
    base = (os.getenv('BZZOIRO_BASE_URL') or 'https://sports.bzzoiro.com/api/v2').rstrip('/')
    conf = _load(INP, {})
    rows = conf.get('confirmations') if isinstance(conf.get('confirmations'), list) else []
    targets = []
    for row in rows:
        if not isinstance(row, dict): continue
        raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
        eid = _event_id(raw) or _event_id(row)
        if eid and eid not in [x.get('event_id') for x in targets]:
            targets.append({'event_id': eid, 'match_key': row.get('match_key'), 'home_team': row.get('home_team'), 'away_team': row.get('away_team')})
    targets = targets[:max(0, int(float(os.getenv('BZZOIRO_TARGETED_ODDS_DETAIL_LIMIT','60') or 60)))]
    details=[]; all_offers=[]; requests=0
    if key and targets:
        headers={'Authorization': f'Token {key}'}
        async with httpx.AsyncClient(timeout=float(os.getenv('BZZOIRO_TIMEOUT_SECONDS','20') or 20), follow_redirects=True) as client:
            for t in targets:
                eid=str(t['event_id']); payloads={}
                for endpoint in ('odds','odds/comparison'):
                    payloads[endpoint]=await _fetch(client, base, eid, headers, endpoint); requests += 1
                offers=[]
                for payload in payloads.values(): offers.extend(_offers_from_payload(payload, event_id=eid, match_key=str(t.get('match_key') or '')))
                all_offers.extend(offers)
                details.append({**t, 'offers': len(offers), 'sample_offers': offers[:8], 'payload_shapes': {k: type(v).__name__ for k,v in payloads.items()}})
    payload={'status':'ok','created_at_utc':datetime.now(UTC).isoformat(),'targets':len(targets),'requests':requests,'events_with_offers':sum(1 for d in details if d.get('offers')),'offers':len(all_offers),'details':details,'publication_contract_relaxed':False,'diagnosis':'missing_bzzoiro_api_key' if not key else ('no_matched_event_ids' if not targets else ('offers_parsed' if all_offers else 'detail_endpoints_returned_no_parseable_offers'))}
    _write(OUT,payload); _write(RAW, {'created_at_utc':payload['created_at_utc'],'source':'bzzoiro','rows':all_offers,'offer_count':len(all_offers),'diagnosis':payload['diagnosis']}); _write(ODDS, {'created_at_utc':payload['created_at_utc'],'source':'bzzoiro','rows':all_offers,'offer_count':len(all_offers),'diagnosis':payload['diagnosis']})
    print(json.dumps({k:payload[k] for k in ('targets','requests','events_with_offers','offers','diagnosis')}, ensure_ascii=False)); return 0


def main() -> int: return asyncio.run(_main())

if __name__ == '__main__': raise SystemExit(main())
