from __future__ import annotations

"""Repair the day-inventory coverage matrix from all API evidence available in the run.

This script is intentionally API-safe: by default it does not call providers.  It
normalizes and merges evidence already produced by the runtime/provider discovery
steps into the frozen 300-match inventory.  If the discovery artifact is missing
and API_INVENTORY_REPAIR_RUN_DISCOVERY=true, it runs provider_day_discovery first.

Main goals:
- preserve the top-300 fixture list;
- keep provider source ids/crosswalk aliases per match;
- accumulate information across runs instead of overwriting it;
- separate fixture providers, independent odds providers, price confirmations,
  and context providers;
- make coverage truth reflect Bzzoiro/SStats/SportLogic/odds-api.io evidence
  consistently even when match_key formats differ.
"""

import json
import os
import re
import runpy
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT_DIR = ROOT / '.data' / 'exports'
DAY_DIR = ROOT / '.data' / 'day_inventory'
OUT = EXPORT_DIR / 'latest-api-inventory-full-coverage-repair.json'
DISCOVERY_JSON = EXPORT_DIR / 'provider-day-discovery-canonical-pool.json'
LIVE_ODDS = {'odds_api_io', 'bzzoiro', 'sportlogic'}
CONTEXT_PROVIDERS = {
    'sstats', 'bzzoiro', 'sportlogic', 'football_data', 'football_data_org',
    'thesportsdb', 'allsportsapi', 'highlightly', 'clubelo', 'weatherapi',
    'open_meteo', 'openweathermap', 'wikidata', 'newsapi', 'currents',
    'gnews', 'newsdata', 'guardian', 'futrixmetrics', 'rapidapi_football',
}
FIXTURE_PROVIDERS = {
    'odds_api_io', 'bzzoiro', 'sstats', 'sportlogic', 'football_data',
    'thesportsdb', 'allsportsapi', 'highlightly', 'rapidapi_football',
}
STOPWORDS = {
    'fc','cf','sc','afc','ac','as','cd','sd','fk','sk','club','de','la','the',
    'football','soccer','women','woman','ladies','u17','u18','u19','u20','u21','u23',
    'reserves','reserve','ii','b'
}


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


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def norm_source(value: Any) -> str:
    text = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    aliases = {
        '': '', 'none': '', 'null': '', 'unknown': '', 'inventory': '', 'day_inventory': '',
        'oddsapiio': 'odds_api_io', 'odds_api': 'odds_api_io', 'the_odds_api': 'odds_api_io',
        'odds_api_io_account1': 'odds_api_io', 'odds_api_io_account2': 'odds_api_io',
        'bzzoiro_predictions': 'bzzoiro', 'bzzoiro_current_odds': 'bzzoiro', 'bzzoiro_v2': 'bzzoiro', 'bsd': 'bzzoiro', 'bsd_v2': 'bzzoiro',
        'sport_logic': 'sportlogic', 'sportlogic_io': 'sportlogic',
        'sstats_net': 'sstats', 'sstats_form': 'sstats',
        'football_data_org': 'football_data',
        'sportsdb': 'thesportsdb', 'the_sports_db': 'thesportsdb',
        'api_football': 'rapidapi_football', 'free_football_rapidapi': 'rapidapi_football',
        'openweather': 'openweathermap', 'open_weather_map': 'openweathermap',
        'the_guardian': 'guardian', 'guardian_open_platform': 'guardian',
    }
    return aliases.get(text, text)


def listish(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r'[,|;/]+', value) if x.strip()]
    return []


def norm_team(value: Any) -> str:
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii').lower()
    text = text.replace('&', ' and ')
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return ' '.join(tok for tok in text.split() if tok and tok not in STOPWORDS)


def norm_league(value: Any) -> str:
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii').lower()
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return ' '.join(tok for tok in text.split() if tok)


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return max(0.88, SequenceMatcher(None, a, b).ratio())
    aset, bset = set(a.split()), set(b.split())
    jac = len(aset & bset) / max(1, len(aset | bset))
    return max(jac, SequenceMatcher(None, a, b).ratio())


def row_identity(row: dict[str, Any]) -> dict[str, Any]:
    dt = parse_dt(row.get('kickoff_utc') or row.get('kickoff_local') or row.get('commence_time'))
    return {
        'home': str(row.get('home_team') or row.get('home') or ''),
        'away': str(row.get('away_team') or row.get('away') or ''),
        'league': str(row.get('league_name') or row.get('league') or ''),
        'date': dt.date().isoformat() if dt else str(row.get('date_local') or target_date()),
        'dt': dt,
        'home_norm': norm_team(row.get('home_team') or row.get('home') or ''),
        'away_norm': norm_team(row.get('away_team') or row.get('away') or ''),
        'league_norm': norm_league(row.get('league_name') or row.get('league') or ''),
    }


def alias_keys(row: dict[str, Any]) -> set[str]:
    ident = row_identity(row)
    keys = {str(row.get(k) or '').strip() for k in ('match_key', 'canonical_match_id', 'loose_key') if str(row.get(k) or '').strip()}
    date = ident['date']
    h, a = ident['home_norm'], ident['away_norm']
    if h and a:
        keys.update({f'{date}|{h}|{a}', f'{date}|{a}|{h}', f'soccer|{h}|{a}|{date}', f'soccer|{a}|{h}|{date}', f'{h}|{a}', f'{a}|{h}'})
    return keys


def score_to_row(row: dict[str, Any], event: dict[str, Any]) -> float:
    a = row_identity(row)
    b = {
        'home_norm': norm_team(event.get('home_team') or event.get('home') or ''),
        'away_norm': norm_team(event.get('away_team') or event.get('away') or ''),
        'league_norm': norm_league(event.get('league_name') or event.get('league') or ''),
        'dt': parse_dt(event.get('kickoff_utc') or event.get('event_date') or event.get('start_time')),
    }
    pair = (similarity(a['home_norm'], b['home_norm']) + similarity(a['away_norm'], b['away_norm'])) / 2.0
    swapped = (similarity(a['home_norm'], b['away_norm']) + similarity(a['away_norm'], b['home_norm'])) / 2.0
    if swapped > pair + 0.10:
        pair -= 0.22
    league = similarity(a['league_norm'], b['league_norm']) if a['league_norm'] and b['league_norm'] else 0.45
    time_bonus = 0.0
    if a['dt'] and b['dt']:
        delta_min = abs((a['dt'] - b['dt']).total_seconds()) / 60.0
        if delta_min <= 20:
            time_bonus = 0.16
        elif delta_min <= 90:
            time_bonus = 0.08
        elif delta_min <= 360:
            time_bonus = 0.02
        else:
            time_bonus = -0.18
    return max(0.0, min(1.0, pair * 0.78 + league * 0.08 + time_bonus))


def find_row(event: dict[str, Any], rows: list[dict[str, Any]], by_alias: dict[str, dict[str, Any]], min_score: float) -> tuple[dict[str, Any] | None, float]:
    for key in listish(event.get('canonical_match_key')) + listish(event.get('match_key')):
        if key in by_alias:
            return by_alias[key], 1.0
    # Exact date/home/away aliases from canonical discovery.
    dt = parse_dt(event.get('kickoff_utc') or event.get('event_date') or event.get('start_time'))
    date = dt.date().isoformat() if dt else target_date()
    h, a = norm_team(event.get('home_team') or ''), norm_team(event.get('away_team') or '')
    for key in (f'{date}|{h}|{a}', f'{date}|{a}|{h}', f'soccer|{h}|{a}|{date}', f'soccer|{a}|{h}|{date}', f'{h}|{a}', f'{a}|{h}'):
        if key in by_alias:
            return by_alias[key], 0.98
    best_row, best_score = None, 0.0
    for row in rows:
        score = score_to_row(row, event)
        if score > best_score:
            best_row, best_score = row, score
    if best_score >= min_score:
        return best_row, best_score
    return None, best_score


def row_has_bzzoiro_context_hint(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    if any(bool(md.get(key)) for key in (
        'bzzoiro_context_fields', 'bzzoiro_has_prediction', 'bzzoiro_has_context_hint',
        'bzzoiro_context_gap_annotated_at_utc', 'bzzoiro_line_evidence_context_bridge',
    )):
        return True
    source_ids = row.get('source_ids') if isinstance(row.get('source_ids'), dict) else {}
    provider_ids = md.get('provider_source_ids') if isinstance(md.get('provider_source_ids'), dict) else {}
    has_bzz_id = any(str(k).lower().startswith(('bzzoiro', 'bsd')) for k in list(source_ids.keys()) + list(provider_ids.keys()))
    return bool(has_bzz_id and (cov.get('context') or cov.get('xg') or md.get('bzzoiro_raw_source')))


def add_sources(row: dict[str, Any], *, fixture: set[str] | None = None, odds: set[str] | None = None, context: set[str] | None = None, books: set[str] | None = None, reason: str = '') -> dict[str, int]:
    changed = {'fixture': 0, 'odds': 0, 'context': 0, 'books': 0}
    fixture = {norm_source(x) for x in (fixture or set()) if norm_source(x)}
    odds = {norm_source(x) for x in (odds or set()) if norm_source(x) in LIVE_ODDS}
    context = {norm_source(x) for x in (context or set()) if norm_source(x) in CONTEXT_PROVIDERS}
    if row_has_bzzoiro_context_hint(row):
        context.add('bzzoiro')
    books = {str(x).strip() for x in (books or set()) if str(x).strip()}
    md = row.setdefault('metadata', {})
    if not isinstance(md, dict):
        md = {}; row['metadata'] = md
    cov = row.setdefault('coverage', {})
    if not isinstance(cov, dict):
        cov = {}; row['coverage'] = cov
    def merge_list(key: str, vals: set[str]) -> int:
        old = {norm_source(x) if key != 'books' else str(x).strip() for x in listish(row.get(key)) + listish(md.get(key)) if str(x).strip()}
        new = set(old) | vals
        cleaned = sorted(x for x in new if x)
        row[key] = cleaned
        md[key] = cleaned
        return int(len(new) > len(old))
    changed['fixture'] = merge_list('fixture_sources', fixture)
    changed['odds'] = merge_list('odds_sources', odds) or merge_list('line_sources', odds)
    changed['context'] = merge_list('context_sources', context) or merge_list('context_confirmations', context)
    changed['books'] = merge_list('books', books)
    odds_count = len({norm_source(x) for x in listish(row.get('odds_sources')) + listish(row.get('line_sources')) if norm_source(x) in LIVE_ODDS})
    context_count = len({norm_source(x) for x in listish(row.get('context_sources')) + listish(row.get('context_confirmations')) if norm_source(x) in CONTEXT_PROVIDERS})
    book_count = len(set(listish(row.get('books')) + listish(md.get('books'))))
    price_count = max(as_int(row.get('price_confirmation_sources_count')), as_int(md.get('price_confirmation_sources_count')), as_int(row.get('books_count')), as_int(md.get('books_count')), book_count)
    if odds_count:
        cov['odds'] = True
    if context_count:
        cov['context'] = True
    cov['odds_sources_count'] = odds_count
    cov['context_sources_count'] = context_count
    cov['price_confirmation_sources_count'] = price_count
    row['odds_sources_count'] = odds_count
    row['context_sources_count'] = context_count
    row['books_count'] = max(book_count, as_int(row.get('books_count')), as_int(md.get('books_count')))
    row['price_confirmation_sources_count'] = price_count
    md['odds_sources_count'] = odds_count
    md['context_sources_count'] = context_count
    md['books_count'] = row['books_count']
    md['price_confirmation_sources_count'] = price_count
    if reason:
        notes = md.setdefault('coverage_repair_notes', [])
        if isinstance(notes, list) and reason not in notes:
            notes.append(reason)
    cov['ready_for_model'] = bool(cov.get('ready_for_model')) or (bool(cov.get('odds')) and bool(cov.get('context')))
    return changed


def merge_source_ids(row: dict[str, Any], source_ids: dict[str, Any]) -> int:
    if not isinstance(source_ids, dict):
        return 0
    row_ids = row.setdefault('source_ids', {})
    if not isinstance(row_ids, dict):
        row_ids = {}; row['source_ids'] = row_ids
    md = row.setdefault('metadata', {})
    if not isinstance(md, dict):
        md = {}; row['metadata'] = md
    md_ids = md.setdefault('provider_source_ids', {})
    if not isinstance(md_ids, dict):
        md_ids = {}; md['provider_source_ids'] = md_ids
    changed = 0
    for provider, value in source_ids.items():
        p = norm_source(provider)
        if not p or value in (None, '', []):
            continue
        if p not in row_ids:
            changed += 1
        row_ids[p] = value
        md_ids[p] = value
    sources_seen = {norm_source(x) for x in listish(row.get('sources_seen')) if norm_source(x)} | set(row_ids.keys())
    row['sources_seen'] = sorted(sources_seen)
    return changed


def discovery_events(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for item in payload.get('canonical_matches_sample') or []:
        if isinstance(item, dict):
            out.append(item)
    # Keep raw source_events as additional hints when canonical sample is small.
    for item in payload.get('targeted_enrichment_plan') or []:
        if isinstance(item, dict):
            out.append(item)
    return out


def run_discovery_if_needed() -> None:
    if DISCOVERY_JSON.exists() and DISCOVERY_JSON.stat().st_size > 0:
        return
    if str(os.getenv('API_INVENTORY_REPAIR_RUN_DISCOVERY') or '').lower() not in {'1','true','yes','on'}:
        return
    try:
        runpy.run_path(str(ROOT / 'scripts' / 'provider_day_discovery_canonical_pool.py'), run_name='__main__')
    except SystemExit:
        pass
    except Exception:
        pass


def main() -> int:
    started = datetime.now(UTC)
    run_discovery_if_needed()
    d = target_date()
    inv_path = DAY_DIR / f'{d}.json'
    inv = load_json(inv_path, {})
    rows = [r for r in inv.get('matches', []) if isinstance(r, dict)] if isinstance(inv, dict) else []
    by_alias: dict[str, dict[str, Any]] = {}
    for row in rows:
        for k in alias_keys(row):
            by_alias.setdefault(k, row)
    min_score = float(os.getenv('API_INVENTORY_REPAIR_MIN_MATCH_SCORE') or '0.72')
    report: dict[str, Any] = {
        'status': 'ok' if rows else 'no_inventory',
        'date_local': d,
        'inventory_path': str(inv_path),
        'started_at_utc': started.isoformat(),
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'matches_total': len(rows),
        'changes': Counter(),
        'matched_events': 0,
        'unmatched_events': 0,
        'provider_matches_added': Counter(),
        'notes': [],
        'examples': [],
    }
    if not rows:
        write_json(OUT, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    # 1) Merge provider-day discovery/canonical pool fixture source ids.
    disc = load_json(DISCOVERY_JSON, {})
    for event in discovery_events(disc):
        providers = {norm_source(x) for x in listish(event.get('providers')) if norm_source(x)}
        source_ids = event.get('source_ids') if isinstance(event.get('source_ids'), dict) else {}
        row, score = find_row(event, rows, by_alias, min_score)
        if not row:
            report['unmatched_events'] += 1
            continue
        report['matched_events'] += 1
        changed_ids = merge_source_ids(row, source_ids)
        changed = add_sources(row, fixture=providers & FIXTURE_PROVIDERS, context=providers & CONTEXT_PROVIDERS, reason='provider_day_discovery')
        # A provider appearing in canonical discovery is not automatically an odds source.
        # Only SportLogic/Bzzoiro count as odds here when source ids are paired with later line evidence.
        report['changes'].update({'source_ids': changed_ids, **changed})
        for provider in providers:
            report['provider_matches_added'][provider] += 1

    # 2) Read coverage truth rows to backfill source counters from latest repair artifacts.
    truth = load_json(EXPORT_DIR / 'latest-day-inventory-coverage-truth.json', {})
    for trow in truth.get('rows') or []:
        if not isinstance(trow, dict):
            continue
        row, score = find_row(trow, rows, by_alias, min_score)
        if not row:
            continue
        odds = {norm_source(x) for x in listish(trow.get('odds_sources')) if norm_source(x)}
        context = {norm_source(x) for x in listish(trow.get('context_sources')) if norm_source(x)}
        changed = add_sources(row, odds=odds, context=context, reason='coverage_truth_backfill')
        report['changes'].update(changed)

    # 3) Promote Bzzoiro event/prediction metadata into context_sources.
    # Provider-day discovery often stores Bzzoiro prediction/event evidence as
    # metadata flags but leaves context_sources empty.  That made the report show
    # Bzzoiro contexts while the frozen inventory did not count Bzzoiro as a
    # context provider.  This promotion is evidence-based: it requires an actual
    # Bzzoiro event/prediction/source id or explicit context flag.
    bzz_context_hint_promoted = 0
    for row in rows:
        if row_has_bzzoiro_context_hint(row):
            before = set(listish(row.get('context_sources')) + listish((row.get('metadata') or {}).get('context_sources') if isinstance(row.get('metadata'), dict) else []))
            add_sources(row, context={'bzzoiro'}, reason='bzzoiro_event_prediction_context_hint')
            after = set(listish(row.get('context_sources')) + listish((row.get('metadata') or {}).get('context_sources') if isinstance(row.get('metadata'), dict) else []))
            if len(after) > len(before):
                bzz_context_hint_promoted += 1
    report['changes']['bzzoiro_context_hint_promotions'] = bzz_context_hint_promoted

    # 4) Promote Bzzoiro line evidence to a lightweight context source only when a match
    # already has another context source. This helps coverage truth reflect the fact that
    # Bzzoiro has independently identified the event, without inventing xG.
    promoted = 0
    for row in rows:
        ctx = {norm_source(x) for x in listish(row.get('context_sources')) + listish((row.get('metadata') or {}).get('context_sources') if isinstance(row.get('metadata'), dict) else []) if norm_source(x)}
        odds = {norm_source(x) for x in listish(row.get('odds_sources')) + listish(row.get('line_sources')) if norm_source(x)}
        has_other_context = bool(ctx - {'bzzoiro'})
        if 'bzzoiro' in odds and has_other_context and 'bzzoiro' not in ctx:
            add_sources(row, context={'bzzoiro'}, reason='bzzoiro_line_evidence_as_event_context')
            promoted += 1
    report['changes']['bzzoiro_line_context_promotions'] = promoted

    # 5) Recompute summary counters and cap the inventory to the frozen target size, preserving
    # enriched rows and kickoff priority.
    target = max(300, as_int(os.getenv('DAY_INVENTORY_TARGET_SIZE') or os.getenv('DAY_INVENTORY_MAX_MATCHES'), 300))
    def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        dt = parse_dt(row.get('kickoff_utc') or row.get('kickoff_local'))
        score = as_int(row.get('priority'), 0)
        c = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        return (dt or datetime.max.replace(tzinfo=UTC), -score, -as_int(c.get('odds_sources_count')), -as_int(c.get('context_sources_count')), str(row.get('league_name') or ''))
    rows.sort(key=sort_key)
    inv['matches'] = rows[:target]
    now_s = datetime.now(UTC).isoformat()
    inv['updated_at_utc'] = now_s
    counts = inv.setdefault('counts', {})
    if isinstance(counts, dict):
        counts['matches_total'] = len(inv['matches'])
        counts['matches_with_1plus_line_or_odds'] = sum(1 for r in inv['matches'] if as_int((r.get('coverage') or {}).get('odds_sources_count')) >= 1 or as_int(r.get('price_confirmation_sources_count')) >= 1 or bool((r.get('coverage') or {}).get('odds')))
        counts['matches_with_1plus_context_sources'] = sum(1 for r in inv['matches'] if as_int((r.get('coverage') or {}).get('context_sources_count')) >= 1 or bool((r.get('coverage') or {}).get('context')))
        counts['matches_with_2plus_odds_sources'] = sum(1 for r in inv['matches'] if as_int((r.get('coverage') or {}).get('odds_sources_count')) >= 2)
        counts['matches_with_2plus_context_sources'] = sum(1 for r in inv['matches'] if as_int((r.get('coverage') or {}).get('context_sources_count')) >= 2)
        counts['matches_ready_for_model'] = sum(1 for r in inv['matches'] if bool((r.get('coverage') or {}).get('ready_for_model')))
        counts['api_inventory_full_coverage_repair_updated_utc'] = now_s
    src = inv.setdefault('sources', {})
    if isinstance(src, dict):
        src['api_inventory_full_coverage_repair'] = {
            'updated_at_utc': now_s,
            'matched_events': report['matched_events'],
            'unmatched_events': report['unmatched_events'],
            'changes': dict(report['changes']),
            'provider_matches_added': dict(report['provider_matches_added']),
        }
    for path in (inv_path, DAY_DIR / 'latest.json', DAY_DIR / 'current.json', DAY_DIR / 'today.json'):
        write_json(path, inv)
    report['updated_at_utc'] = now_s
    report['changes'] = dict(report['changes'])
    report['provider_matches_added'] = dict(report['provider_matches_added'])
    report['final_counts'] = counts if isinstance(counts, dict) else {}
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
