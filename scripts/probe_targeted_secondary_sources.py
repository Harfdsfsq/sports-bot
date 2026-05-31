from __future__ import annotations

"""Tiny per-run probes for secondary odds/context providers.

Probe-only: no publication decisions are changed directly here.  The companion
``merge_targeted_secondary_context.py`` can turn successful fixture matches into
context-source evidence before controlled fallback evaluates candidates.

Important runtime rule: probe targets must be *active/upcoming* rows from the
current runtime top-up/day inventory.  Near-miss ledgers are useful for priority,
but stale rows from yesterday or already-started matches should not spend quota
or create context evidence that cannot be merged into the current truth roster.
"""

import asyncio
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
OUT = EXPORT / 'latest-targeted-secondary-provider-probe.json'
CACHE = ROOT / '.data' / 'provider_cache' / 'targeted-secondary-probes'
RUN_CACHE = CACHE / 'current-run.json'
NEAR_MISS_LEDGER = ROOT / '.data' / 'rejected-near-miss-ledger.jsonl'
DAY_INV = ROOT / '.data' / 'day_inventory'


def load_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def app_tz() -> ZoneInfo | timezone:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return UTC


def now_utc() -> datetime:
    forced = str(os.getenv('SECONDARY_PROVIDER_PROBE_NOW_UTC') or '').strip()
    if forced:
        dt = parse_dt(forced)
        if dt is not None:
            return dt
    return datetime.now(UTC)


def target_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or os.getenv('DAY_INVENTORY_CACHE_DATE') or '').strip()
    if explicit:
        return explicit
    return now_utc().astimezone(app_tz()).date().isoformat()


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(text)
        except Exception:
            if re.match(r'^20\d\d-\d\d-\d\d$', text):
                try:
                    dt = datetime.fromisoformat(text + 'T00:00:00+00:00')
                except Exception:
                    return None
            else:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def row_date(row: dict[str, Any]) -> str:
    for key in ('date_local', 'commence_time', 'kickoff_utc', 'start_time', 'kickoff', 'event_date', 'date'):
        value = row.get(key)
        if not value:
            continue
        if key == 'date_local' and re.match(r'^20\d\d-\d\d-\d\d$', str(value)):
            return str(value)
        if isinstance(value, dict):
            value = value.get('date') or value.get('datetime')
        dt = parse_dt(value)
        if dt is not None:
            return dt.astimezone(app_tz()).date().isoformat()
        text = str(value)
        if re.match(r'^20\d\d-\d\d-\d\d', text):
            return text[:10]
    key = str(row.get('match_key') or row.get('canonical_match_id') or '')
    match = re.search(r'(20\d\d-\d\d-\d\d)', key)
    return match.group(1) if match else ''


def kickoff_dt(row: dict[str, Any]) -> datetime | None:
    value = metric(row, 'commence_time', 'kickoff_utc', 'start_time', 'kickoff', 'event_date', 'date')
    if isinstance(value, dict):
        value = value.get('date') or value.get('datetime')
    return parse_dt(value)


def norm_name(value: Any) -> str:
    text = str(value or '').lower().strip()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r'[^a-z0-9а-яё]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def compact(value: Any) -> str:
    return norm_name(value).replace(' ', '_')


def identity(home: Any, away: Any, date: str) -> str:
    h = compact(home)
    a = compact(away)
    if not h or not a or not date:
        return ''
    return f'soccer|{h}|{a}|{date}'


def rows_from(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ('items', 'rows', 'candidates', 'selected_all', 'evaluated', 'fallback_evaluated', 'latest_rescue_candidates', 'watchlist'):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend([x for x in value if isinstance(x, dict)])
        elif isinstance(value, dict):
            out.append(value)
    selected = payload.get('selected')
    if isinstance(selected, dict):
        out.append(selected)
    samples = payload.get('samples') if isinstance(payload.get('samples'), dict) else {}
    for key in ('fallback_evaluated', 'rescue_candidates', 'near_misses'):
        value = samples.get(key)
        if isinstance(value, list):
            out.extend([x for x in value if isinstance(x, dict)])
    sample = payload.get('sample')
    if isinstance(sample, list):
        out.extend([x for x in sample if isinstance(x, dict)])
    return out


def ledger_rows(limit: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if NEAR_MISS_LEDGER.exists():
            for line in NEAR_MISS_LEDGER.read_text(encoding='utf-8').splitlines()[-max(limit * 3, 20):]:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
    except Exception:
        return rows[-limit:]
    return rows[-limit:]


def metric(row: dict[str, Any], *keys: str) -> Any:
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
    raw_metrics = raw.get('metrics') if isinstance(raw.get('metrics'), dict) else {}
    nested = row.get('candidate') if isinstance(row.get('candidate'), dict) else {}
    for key in keys:
        if row.get(key) not in (None, ''):
            return row.get(key)
        if metrics.get(key) not in (None, ''):
            return metrics.get(key)
        if raw.get(key) not in (None, ''):
            return raw.get(key)
        if raw_metrics.get(key) not in (None, ''):
            return raw_metrics.get(key)
        if nested.get(key) not in (None, ''):
            return nested.get(key)
    return None


def name_parts(row: dict[str, Any]) -> tuple[str, str]:
    home = str(row.get('home_team') or row.get('home') or row.get('event_home_team') or '').lower()
    away = str(row.get('away_team') or row.get('away') or row.get('event_away_team') or '').lower()
    if not home and isinstance(row.get('homeTeam'), dict):
        home = str(row['homeTeam'].get('name') or '').lower()
        away = str(row.get('awayTeam', {}).get('name') if isinstance(row.get('awayTeam'), dict) else '').lower()
    if not home and isinstance(row.get('teams'), dict):
        home_obj = row['teams'].get('home')
        away_obj = row['teams'].get('away')
        home = str(home_obj.get('name') if isinstance(home_obj, dict) else '').lower()
        away = str(away_obj.get('name') if isinstance(away_obj, dict) else '').lower()
    if not home:
        home = str(metric(row, 'home_team', 'home') or '').lower()
        away = str(metric(row, 'away_team', 'away') or '').lower()
    return home, away


def target_match_key(row: dict[str, Any]) -> str:
    value = str(metric(row, 'match_key', 'canonical_match_id') or '').strip()
    if value:
        return value
    home = str(metric(row, 'home_team', 'home') or '').strip().lower()
    away = str(metric(row, 'away_team', 'away') or '').strip().lower()
    date = str(metric(row, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10] or row_date(row)
    return f'soccer|{home}|{away}|{date}'


def active_inventory_rows(limit: int = 300) -> list[dict[str, Any]]:
    date = target_date()
    paths = [
        DAY_INV / f'runtime_topup_roster_{date}.json',
        DAY_INV / f'{date}.json',
        DAY_INV / 'current.json',
        DAY_INV / 'latest.json',
        DAY_INV / 'today.json',
    ]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        payload = load_json(path, {})
        for row in rows_from(payload.get('matches') if isinstance(payload, dict) else payload):
            if not isinstance(row, dict):
                continue
            key = target_match_key(row)
            if not key or key in seen:
                continue
            if row_date(row) not in {'', date, (now_utc() + timedelta(days=1)).astimezone(app_tz()).date().isoformat()}:
                continue
            seen.add(key)
            out.append(row)
            if len(out) >= limit:
                return out
    return out


def active_indexes() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_key: dict[str, dict[str, Any]] = {}
    by_identity: dict[str, dict[str, Any]] = {}
    for row in active_inventory_rows():
        key = target_match_key(row)
        if key:
            by_key.setdefault(key, row)
        home, away = name_parts(row)
        date = row_date(row) or str(metric(row, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10]
        ident = identity(home, away, date)
        if ident:
            by_identity.setdefault(ident, row)
            by_identity.setdefault(identity(away, home, date), row)
    return by_key, by_identity


def is_upcoming(row: dict[str, Any], *, now: datetime, max_hours: float) -> bool:
    dt = kickoff_dt(row)
    if dt is None:
        # Keep undated rows only when they are already tied to active inventory.
        return True
    delta_h = (dt - now).total_seconds() / 3600.0
    grace_min = as_int(os.getenv('SECONDARY_PROVIDER_PROBE_STARTED_GRACE_MINUTES'), 10)
    return delta_h >= -(grace_min / 60.0) and delta_h <= max_hours


def normalize_to_active_row(row: dict[str, Any], by_key: dict[str, dict[str, Any]], by_identity: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    key = target_match_key(row)
    if key in by_key:
        active = by_key[key]
        merged = {**row}
        merged.setdefault('home_team', active.get('home_team') or active.get('home'))
        merged.setdefault('away_team', active.get('away_team') or active.get('away'))
        merged.setdefault('commence_time', active.get('commence_time') or active.get('kickoff_utc') or active.get('start_time'))
        merged['match_key'] = target_match_key(active)
        return merged
    home, away = name_parts(row)
    date = row_date(row) or str(metric(row, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10]
    for ident in (identity(home, away, date), identity(away, home, date)):
        active = by_identity.get(ident)
        if active:
            merged = {**row}
            merged.setdefault('home_team', active.get('home_team') or active.get('home'))
            merged.setdefault('away_team', active.get('away_team') or active.get('away'))
            merged.setdefault('commence_time', active.get('commence_time') or active.get('kickoff_utc') or active.get('start_time'))
            merged['match_key'] = target_match_key(active)
            return merged
    # Alias fallback: provider/candidate names often drop suffixes like CF/SD or accents.
    # Use token intersection only inside the already-scoped active inventory.
    th, ta = tokens(home), tokens(away)
    for active in by_key.values():
        ah, aa = name_parts(active)
        adate = row_date(active) or str(metric(active, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10]
        if date and adate and date != adate:
            continue
        direct = th.intersection(tokens(ah)) and ta.intersection(tokens(aa))
        swapped = th.intersection(tokens(aa)) and ta.intersection(tokens(ah))
        if direct or swapped:
            merged = {**row}
            merged.setdefault('home_team', active.get('home_team') or active.get('home'))
            merged.setdefault('away_team', active.get('away_team') or active.get('away'))
            merged.setdefault('commence_time', active.get('commence_time') or active.get('kickoff_utc') or active.get('start_time'))
            merged['match_key'] = target_match_key(active)
            return merged
    return None


def target_rows(limit: int = 8) -> list[dict[str, Any]]:
    paths = [
        EXPORT / 'latest-rejected-near-miss-report.json',
        EXPORT / 'latest-controlled-fallback-report.json',
        EXPORT / 'latest-rescue-candidates.json',
        EXPORT / 'latest-near-miss-enrichment-queue.json',
        ROOT / '.logs' / 'debug-last-run.json',
    ]
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(rows_from(load_json(path, {})))
    rows.extend(ledger_rows(limit=limit * 2))

    by_key, by_identity = active_indexes()
    active_rows = active_inventory_rows(limit=300)
    now = now_utc()
    max_hours = float(os.getenv('SECONDARY_PROVIDER_PROBE_TARGET_MAX_HOURS', '36') or 36)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def add(row: dict[str, Any], *, require_active: bool = True) -> None:
        if len(out) >= limit:
            return
        normalized = normalize_to_active_row(row, by_key, by_identity) if require_active else row
        if normalized is None:
            return
        if not is_upcoming(normalized, now=now, max_hours=max_hours):
            return
        home = str(metric(normalized, 'home_team', 'home') or '').strip()
        away = str(metric(normalized, 'away_team', 'away') or '').strip()
        if not home or not away:
            return
        key = target_match_key(normalized)
        if not key or key in seen:
            return
        seen.add(key)
        out.append(normalized)

    # Candidate/ledger rows are allowed only if they still map to current runtime inventory.
    for row in rows:
        add(row, require_active=True)

    # Fill the remaining slots from the current runtime inventory so probes spend
    # quota on future rows that can actually be merged into coverage truth.
    for row in active_rows:
        add(row, require_active=False)

    return out[:limit]


class ProbeBudget:
    def __init__(self, provider: str, limit: int) -> None:
        self.provider = provider
        self.limit = max(0, limit)
        self.used = 0
        self.stopped = False
        self.http_statuses: list[int] = []
        self.errors: list[str] = []

    def can(self) -> bool:
        return not self.stopped and (self.limit <= 0 or self.used < self.limit)

    async def get(self, client: httpx.AsyncClient, url: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> Any:
        if not self.can():
            return None
        self.used += 1
        try:
            resp = await client.get(url, headers=headers or {}, params=params or {})
            self.http_statuses.append(resp.status_code)
            if resp.status_code == 429 or resp.status_code >= 500:
                self.stopped = True
            if resp.status_code >= 400:
                self.errors.append(f'{resp.status_code}:{resp.text[:220]}')
                return None
            try:
                return resp.json()
            except Exception:
                return {'_text_preview': resp.text[:500]}
        except Exception as exc:
            self.errors.append(f'{type(exc).__name__}: {exc}')
            self.stopped = True
            return None


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('response', 'data', 'results', 'items', 'events', 'matches', 'result'):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return [payload]
    return []


def tokens(text: str) -> set[str]:
    normalized = norm_name(text).replace('-', ' ').replace('.', ' ')
    return {x for x in normalized.split() if len(x) >= 3 and x not in {'club', 'football', 'futbol', 'fc', 'sc', 'cf', 'city'}}


def kickoff_date(row: dict[str, Any]) -> str:
    value = row.get('date') or row.get('commence_time') or row.get('kickoff_utc') or row.get('start_time') or row.get('event_date')
    if isinstance(value, dict):
        value = value.get('date') or value.get('datetime')
    text = str(value or '')
    return text[:10] if len(text) >= 10 else ''


def matched_contexts(rows: list[dict[str, Any]], targets: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    active_keys, active_idents = active_indexes()
    for t in targets:
        normalized_t = normalize_to_active_row(t, active_keys, active_idents) or t
        th, ta = name_parts({'home_team': metric(normalized_t, 'home_team', 'home'), 'away_team': metric(normalized_t, 'away_team', 'away')})
        if not th or not ta:
            continue
        th_tokens = tokens(th)
        ta_tokens = tokens(ta)
        t_date = str(metric(normalized_t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10] or row_date(normalized_t)
        for row in rows:
            rh, ra = name_parts(row)
            if not rh or not ra:
                continue
            same_date = not t_date or not kickoff_date(row) or kickoff_date(row) == t_date
            direct = th_tokens.intersection(tokens(rh)) and ta_tokens.intersection(tokens(ra))
            swapped = th_tokens.intersection(tokens(ra)) and ta_tokens.intersection(tokens(rh))
            if same_date and (direct or swapped):
                mk = target_match_key(normalized_t)
                if mk and mk not in seen:
                    seen.add(mk)
                    out.append({
                        'match_key': mk,
                        'home_team': metric(normalized_t, 'home_team', 'home'),
                        'away_team': metric(normalized_t, 'away_team', 'away'),
                        'kickoff_utc': metric(normalized_t, 'commence_time', 'kickoff_utc', 'start_time'),
                        'provider': provider,
                        'provider_event_id': row.get('id') or row.get('event_key') or (row.get('fixture', {}).get('id') if isinstance(row.get('fixture'), dict) else None),
                        'provider_league': row.get('league'),
                        'provider_country': row.get('country'),
                        'provider_date': row.get('date') or row.get('event_date'),
                    })
                break
    return out


async def probe_highlightly(client: httpx.AsyncClient, targets: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.getenv('HIGHLIGHTLY_API_KEY') or os.getenv('HIGHLIGHTLY_KEY') or os.getenv('RAPIDAPI_KEY')
    budget = ProbeBudget('highlightly', as_int(os.getenv('HIGHLIGHTLY_PROBE_MAX_REQUESTS'), 3))
    result = {'provider': 'highlightly', 'configured': bool(key), 'requests': 0, 'matches_seen': 0, 'matched_targets': 0, 'matched_contexts': [], 'samples': [], 'http_statuses': [], 'errors': []}
    if not key:
        return result
    base = (os.getenv('HIGHLIGHTLY_BASE_URL') or 'https://soccer.highlightly.net').rstrip('/')
    host = os.getenv('HIGHLIGHTLY_RAPIDAPI_HOST') or ('football-highlights-api.p.rapidapi.com' if 'rapidapi' in base else '')
    if host:
        headers = {'x-rapidapi-key': key, 'x-rapidapi-host': host}
        result['auth_header_mode'] = 'x-rapidapi-key'
    else:
        headers = {'x-rapidapi-key': key}
        result['auth_header_mode'] = 'x-rapidapi-key'
    dates = sorted({str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10] for t in targets if str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10]})[:2]
    if not dates:
        dates = [now_utc().date().isoformat()]
    rows: list[dict[str, Any]] = []
    for d in dates:
        payload = await budget.get(client, f'{base}/matches', headers=headers, params={'date': d, 'limit': 100, 'timezone': 'Etc/UTC'})
        rows.extend(extract_rows(payload))
    contexts = matched_contexts(rows, targets, 'highlightly')
    result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors, 'matches_seen': len(rows), 'samples': rows[:3], 'matched_contexts': contexts})
    result['matched_targets'] = len(contexts)
    if contexts and budget.can() and os.getenv('HIGHLIGHTLY_PROBE_ODDS', 'false').lower() in {'1', 'true', 'yes', 'on'}:
        event_id = contexts[0].get('provider_event_id')
        if event_id:
            odds_payload = await budget.get(client, f'{base}/odds', headers=headers, params={'matchId': event_id})
            result['odds_probe_rows'] = len(extract_rows(odds_payload))
    else:
        result['odds_probe_rows'] = 0
    result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors})
    return result


async def probe_allsportsapi(client: httpx.AsyncClient, targets: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.getenv('ALLSPORTSAPI_API_KEY')
    budget = ProbeBudget('allsportsapi', as_int(os.getenv('ALLSPORTSAPI_PROBE_MAX_REQUESTS'), 3))
    result = {'provider': 'allsportsapi', 'configured': bool(key), 'requests': 0, 'fixtures_seen': 0, 'matched_targets': 0, 'matched_contexts': [], 'odds_probe_rows': 0, 'samples': [], 'http_statuses': [], 'errors': []}
    if not key:
        return result
    base = (os.getenv('ALLSPORTSAPI_BASE_URL') or 'https://apiv2.allsportsapi.com/football/').rstrip('/') + '/'
    dates = sorted({str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10] for t in targets if str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10]})[:2]
    if not dates:
        dates = [now_utc().date().isoformat()]
    rows: list[dict[str, Any]] = []
    for d in dates:
        payload = await budget.get(client, base, params={'met': 'Fixtures', 'APIkey': key, 'from': d, 'to': d, 'timezone': 'UTC'})
        rows.extend(extract_rows(payload.get('result') if isinstance(payload, dict) else payload))
    contexts = matched_contexts(rows, targets, 'allsportsapi')
    result['fixtures_seen'] = len(rows); result['matched_targets'] = len(contexts); result['matched_contexts'] = contexts; result['samples'] = rows[:3]
    if rows and budget.can() and os.getenv('ALLSPORTSAPI_PROBE_ODDS', 'false').lower() in {'1', 'true', 'yes', 'on'}:
        event_id = str(rows[0].get('event_key') or rows[0].get('match_id') or '')
        if event_id:
            odds_payload = await budget.get(client, base, params={'met': 'Odds', 'APIkey': key, 'matchId': event_id})
            result['odds_probe_rows'] = len(extract_rows(odds_payload.get('result') if isinstance(odds_payload, dict) else odds_payload))
    result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors})
    return result


async def probe_api_football(client: httpx.AsyncClient, targets: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.getenv('API_FOOTBALL_KEY') or os.getenv('API_FOOTBALL_API_KEY') or os.getenv('RAPIDAPI_KEY')
    budget = ProbeBudget('api_football', as_int(os.getenv('API_FOOTBALL_PROBE_MAX_REQUESTS'), 4))
    result = {'provider': 'api_football', 'configured': bool(key), 'requests': 0, 'fixtures_seen': 0, 'matched_targets': 0, 'matched_contexts': [], 'statistics_probe_rows': 0, 'lineups_probe_rows': 0, 'samples': [], 'http_statuses': [], 'errors': []}
    if not key:
        return result
    base = (os.getenv('API_FOOTBALL_BASE_URL') or 'https://v3.football.api-sports.io').rstrip('/')
    headers = {'x-apisports-key': key}
    if os.getenv('API_FOOTBALL_RAPIDAPI_HOST'):
        headers = {'x-rapidapi-key': key, 'x-rapidapi-host': os.getenv('API_FOOTBALL_RAPIDAPI_HOST') or 'api-football-v1.p.rapidapi.com'}
    dates = sorted({str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10] for t in targets if str(metric(t, 'commence_time', 'kickoff_utc', 'start_time') or '')[:10]})[:2]
    if not dates:
        dates = [now_utc().date().isoformat()]
    rows: list[dict[str, Any]] = []
    for d in dates:
        payload = await budget.get(client, f'{base}/fixtures', headers=headers, params={'date': d})
        rows.extend(extract_rows(payload))
    contexts = matched_contexts(rows, targets, 'api_football')
    result['fixtures_seen'] = len(rows); result['matched_targets'] = len(contexts); result['matched_contexts'] = contexts; result['samples'] = rows[:3]
    fixture_id = ''
    if rows:
        fixture = rows[0].get('fixture') if isinstance(rows[0].get('fixture'), dict) else {}
        fixture_id = str(fixture.get('id') or rows[0].get('fixture_id') or '')
    if fixture_id and budget.can() and os.getenv('API_FOOTBALL_PROBE_DETAILS', 'false').lower() in {'1', 'true', 'yes', 'on'}:
        stats_payload = await budget.get(client, f'{base}/fixtures/statistics', headers=headers, params={'fixture': fixture_id})
        result['statistics_probe_rows'] = len(extract_rows(stats_payload))
    if fixture_id and budget.can() and os.getenv('API_FOOTBALL_PROBE_DETAILS', 'false').lower() in {'1', 'true', 'yes', 'on'}:
        lineup_payload = await budget.get(client, f'{base}/fixtures/lineups', headers=headers, params={'fixture': fixture_id})
        result['lineups_probe_rows'] = len(extract_rows(lineup_payload))
    result.update({'requests': budget.used, 'http_statuses': budget.http_statuses, 'errors': budget.errors})
    return result


def current_run_id() -> str:
    return str(os.getenv('GITHUB_RUN_ID') or os.getenv('RUN_ID') or '').strip()


def reusable_payload() -> dict[str, Any] | None:
    if os.getenv('SECONDARY_PROVIDER_PROBE_REUSE_SAME_RUN', 'true').lower() not in {'1', 'true', 'yes', 'on'}:
        return None
    run_id = current_run_id()
    payload = load_json(RUN_CACHE, {})
    if run_id and isinstance(payload, dict) and str(payload.get('github_run_id') or '') == run_id:
        write_json(OUT, payload)
        return payload
    return None


async def main_async() -> int:
    if os.getenv('EXTERNAL_SIGNAL_PROBES_ENABLED', 'true').lower() not in {'1', 'true', 'yes', 'on', 'force'}:
        write_json(OUT, {'status': 'disabled'})
        return 0
    cached = reusable_payload()
    if cached is not None:
        print(json.dumps(cached, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    targets = target_rows(as_int(os.getenv('SECONDARY_PROVIDER_PROBE_TARGET_LIMIT'), 8))
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        results = await asyncio.gather(
            probe_highlightly(client, targets),
            probe_allsportsapi(client, targets),
            probe_api_football(client, targets),
        )
    payload = {
        'status': 'ok',
        'github_run_id': current_run_id(),
        'created_at_utc': now_utc().isoformat(),
        'target_count': len(targets),
        'target_sample': [
            {'match_key': target_match_key(t), 'home': metric(t, 'home_team', 'home'), 'away': metric(t, 'away_team', 'away'), 'kickoff': metric(t, 'commence_time', 'kickoff_utc', 'start_time')}
            for t in targets[:8]
        ],
        'providers': {row['provider']: row for row in results},
        'notes': [
            'Probe-only: no publication decisions are changed directly.',
            'All limits are per-run; any 429/5xx stops that provider for this probe only.',
            'Matched fixture rows can be merged as context evidence by merge_targeted_secondary_context.py.',
            'Targets are filtered to active/upcoming runtime inventory rows so stale near-misses do not spend quota or create unmergeable context keys.',
        ],
    }
    write_json(OUT, payload)
    CACHE.mkdir(parents=True, exist_ok=True)
    write_json(RUN_CACHE, payload)
    write_json(CACHE / f"probe-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main_async()))
