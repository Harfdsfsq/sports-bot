from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc
_INSTALLED = False
_PLACEHOLDERS = {'', 'day_inventory', 'inventory', 'unknown', 'none', 'null'}
_TOP_TERMS = (
    'premier league', 'championship', 'serie a', 'serie b', 'la liga', 'segunda división',
    'bundesliga', '2. bundesliga', 'ligue 1', 'ligue 2', 'eredivisie', 'primeira liga',
    'süper lig', 'super lig', 'mls', 'j1 league', 'j2 league', 'k league 1', 'allsvenskan',
    'eliteserien', 'ekstraklasa', 'a-league', 'copa libertadores', 'copa sudamericana',
    'champions league', 'europa league', 'conference league', 'super league 1', 'parva liga',
)
_LOW_TERMS = (
    'u17', 'u18', 'u19', 'u20', 'u21', 'u23', 'youth', 'women', ' w', 'amateur',
    'reserve', 'reserves', 'friendly', 'primavera', 'kolmonen', 'kakkonen', 'ykkonen',
    'danmarksserien', 'landesliga', 'oberliga', 'regionalliga', 'vtoraya', 'tweede divisie',
    'derde divisie', '3rd division', 'third division', '2nd division', 'division 2',
    'division 3', 'league two', 'npl', 'state league', 'segunda división rfef',
    'promotion playoffs', 'play-offs', 'liga 3', 'serie d', 'hessenliga',
)
_FINISHED = ('finished', 'ft', 'aet', 'after penalties', 'pen')
_BAD = ('cancelled', 'canceled', 'postponed', 'abandoned', 'walkover', 'interrupted')
_SCHEDULED = ('not started', 'scheduled', 'timed', 'pending', 'pre-match', 'pre match', 'ns')


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = os.getenv(name)
        if value is None or str(value).strip() == '':
            return max(minimum, default)
        return max(minimum, int(float(str(value))))
    except Exception:
        return max(minimum, default)


def _is_day_inventory_process() -> bool:
    argv0 = str(sys.argv[0] if sys.argv else '').replace('\\', '/')
    return argv0.endswith('scripts/build_day_inventory.py') or argv0.endswith('build_day_inventory.py')


def _sources(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    raw = row.get('sources_seen')
    values = raw if isinstance(raw, list) else str(raw or '').split(',')
    for value in values:
        source = str(value or '').strip()
        if source and source.lower() not in _PLACEHOLDERS:
            out.add(source)
    source_ids = row.get('source_ids')
    if isinstance(source_ids, dict):
        for value in source_ids.keys():
            source = str(value or '').strip()
            if source and source.lower() not in _PLACEHOLDERS:
                out.add(source)
    return out


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get('metadata')
    return value if isinstance(value, dict) else {}


def _coverage(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get('coverage')
    return value if isinstance(value, dict) else {}


def _parse_dt(value: Any) -> datetime | None:
    try:
        text = str(value or '').strip()
        if not text:
            return None
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _league(row: dict[str, Any]) -> str:
    return str(row.get('league_name') or row.get('league_key') or '').lower()


def _teams(row: dict[str, Any]) -> str:
    return f"{row.get('home_team') or ''} {row.get('away_team') or ''}".lower()


def _status(row: dict[str, Any]) -> str:
    meta = _meta(row)
    parts = [row.get('status'), row.get('statusName'), meta.get('status'), meta.get('statusName'), meta.get('event_status'), meta.get('period')]
    return ' '.join(str(item or '') for item in parts).lower()


def _is_bad_status(row: dict[str, Any]) -> bool:
    status = _status(row)
    return any(term in status for term in _BAD) or any(term in status for term in _FINISHED)


def _is_low_value(row: dict[str, Any]) -> bool:
    text = _league(row) + ' ' + _teams(row)
    return str(row.get('tier') or '').lower() == 'low' or any(term in text for term in _LOW_TERMS)


def _is_topish(row: dict[str, Any]) -> bool:
    text = _league(row)
    return any(term in text for term in _TOP_TERMS)


def _bucket(row: dict[str, Any]) -> str:
    sources = _sources(row)
    if 'odds_api_io' in sources:
        return 'odds_api_io'
    if 'bzzoiro' in sources or len(sources) >= 2:
        return 'multi_source_or_bzzoiro'
    if sources == {'sstats'} or ('sstats' in sources and not ({'odds_api_io', 'bzzoiro', 'football_data', 'thesportsdb'} & sources)):
        return 'sstats_only'
    return 'other'


def _score(row: dict[str, Any]) -> float:
    sources = _sources(row)
    cov = _coverage(row)
    meta = _meta(row)
    status = _status(row)
    score = float(row.get('priority') or 0.0)
    if 'odds_api_io' in sources:
        score += 230
    if 'bzzoiro' in sources:
        score += 105
    if 'football_data' in sources:
        score += 70
    if 'thesportsdb' in sources:
        score += 45
    if 'sstats' in sources:
        score += 15
    if len(sources) >= 2:
        score += 95 + min(45, (len(sources) - 2) * 15)
    if cov.get('odds'):
        score += 95
    if cov.get('context'):
        score += 35
    if cov.get('xg'):
        score += 25
    if cov.get('form'):
        score += 15
    if _is_topish(row):
        score += 85
    if _is_low_value(row):
        score -= 150
    if any(term in status for term in _BAD):
        score -= 600
    elif any(term in status for term in _FINISHED):
        score -= 420
    elif any(term in status for term in _SCHEDULED):
        score += 65
    if meta.get('odds_count') or meta.get('has_sstats_odds'):
        score += 25
    kickoff = _parse_dt(row.get('kickoff_utc'))
    if kickoff is not None:
        hours = (kickoff - datetime.now(UTC)).total_seconds() / 3600
        if 0 <= hours <= 6:
            score += 55
        elif 6 < hours <= 12:
            score += 42
        elif 12 < hours <= 24:
            score += 28
        elif hours < -2:
            score -= 300
        elif hours < 0:
            score -= 100
    return round(score, 4)


def _sort(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[tuple[float, str, str, dict[str, Any]]] = []
    for row in rows:
        row['inventory_top_score'] = _score(row)
        row['inventory_selection_bucket'] = _bucket(row)
        row['inventory_low_value'] = _is_low_value(row)
        row['inventory_top_league_like'] = _is_topish(row)
        scored.append((float(row['inventory_top_score']), str(row.get('kickoff_utc') or ''), str(row.get('canonical_match_id') or row.get('match_key') or ''), row))
    return [item[3] for item in sorted(scored, key=lambda item: (-item[0], item[1], item[2]))]


def _key(row: dict[str, Any]) -> str:
    return str(row.get('canonical_match_id') or row.get('match_key') or '').strip()


def _take(pool: list[dict[str, Any]], selected: list[dict[str, Any]], keys: set[str], limit: int) -> int:
    count = 0
    for row in pool:
        if count >= limit:
            break
        key = _key(row)
        if not key or key in keys:
            continue
        selected.append(row)
        keys.add(key)
        count += 1
    return count


def _bucket_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        name = str(row.get('inventory_selection_bucket') or _bucket(row))
        out[name] = out.get(name, 0) + 1
    return out


def _select(rows: list[dict[str, Any]], max_matches: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    odds_target = _env_int('DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES', 160, 0)
    multi_max = _env_int('DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES', 80, 0)
    sstats_max = _env_int('DAY_INVENTORY_SSTATS_ONLY_MAX_MATCHES', 60, 0)
    sstats_overflow_max = _env_int('DAY_INVENTORY_SSTATS_OVERFLOW_MAX_MATCHES', 60, 0)
    other_max = _env_int('DAY_INVENTORY_OTHER_MAX_MATCHES', 60, 0)
    low_fallback_max = _env_int('DAY_INVENTORY_LOW_VALUE_FALLBACK_MAX_MATCHES', 30, 0)
    force_full = _truthy(os.getenv('DAY_INVENTORY_FORCE_FULL_300'), True)

    sorted_rows = _sort([row for row in rows if isinstance(row, dict)])
    non_bad = [row for row in sorted_rows if not _is_bad_status(row)]
    good = [row for row in non_bad if not _is_low_value(row)]
    low = [row for row in non_bad if _is_low_value(row)]

    buckets = {'odds_api_io': [], 'multi_source_or_bzzoiro': [], 'sstats_only': [], 'other': []}
    for row in good:
        buckets.setdefault(str(row.get('inventory_selection_bucket') or _bucket(row)), []).append(row)

    selected: list[dict[str, Any]] = []
    keys: set[str] = set()
    taken: dict[str, int] = {}
    taken['odds_api_io'] = _take(buckets.get('odds_api_io', []), selected, keys, min(max_matches - len(selected), odds_target))
    taken['multi_source_or_bzzoiro'] = _take(buckets.get('multi_source_or_bzzoiro', []), selected, keys, min(max_matches - len(selected), multi_max))
    taken['sstats_only'] = _take(buckets.get('sstats_only', []), selected, keys, min(max_matches - len(selected), sstats_max))
    taken['other'] = _take(buckets.get('other', []), selected, keys, min(max_matches - len(selected), other_max))

    # Good-quality fill without breaking the normal SStats cap.
    good_fill_pool = [row for row in good if _bucket(row) != 'sstats_only']
    taken['global_good_non_sstats_fill'] = _take(good_fill_pool, selected, keys, max_matches - len(selected))

    # Controlled SStats-only overflow, still non-low and non-finished.
    sstats_overflow_pool = [row for row in buckets.get('sstats_only', []) if _key(row) not in keys]
    taken['sstats_controlled_overflow_fill'] = _take(sstats_overflow_pool, selected, keys, min(max_matches - len(selected), sstats_overflow_max))

    # Low-value fallback is capped and only used if the day lacks enough strong matches.
    taken['low_value_fallback_fill'] = _take(low, selected, keys, min(max_matches - len(selected), low_fallback_max))

    # Final forced fill keeps the inventory at 300 when requested; visible in summary.
    if force_full and len(selected) < max_matches:
        taken['force_full_last_resort_fill'] = _take(non_bad, selected, keys, max_matches - len(selected))
    else:
        taken['force_full_last_resort_fill'] = 0

    meta = {
        'bucket_mode': 'strict_caps_with_controlled_sstats_overflow_v2',
        'bucket_targets': {
            'odds_api_io_target': odds_target,
            'multi_source_or_bzzoiro_max': multi_max,
            'sstats_only_initial_max': sstats_max,
            'sstats_controlled_overflow_max': sstats_overflow_max,
            'other_max': other_max,
            'low_value_fallback_max': low_fallback_max,
            'force_full_300': force_full,
        },
        'bucket_selected_counts': taken,
        'selected_counts_by_actual_bucket': _bucket_counts(selected),
        'candidate_counts_by_bucket_good': {name: len(value) for name, value in buckets.items()},
        'candidate_rows_total': len(sorted_rows),
        'candidate_rows_non_bad': len(non_bad),
        'candidate_rows_good_non_low': len(good),
        'candidate_rows_low_value': len(low),
    }
    return selected[:max_matches], meta


def _coverage_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    now = datetime.now(UTC)
    out = {k: 0 for k in (
        'matches_with_odds', 'matches_with_context', 'matches_with_weather', 'matches_with_news',
        'matches_with_xg', 'matches_with_form', 'matches_ready_for_model', 'matches_ready_for_publish',
        'matches_next_6h', 'matches_next_6h_ready', 'matches_next_12h', 'matches_next_12h_ready'
    )}
    for row in rows:
        cov = _coverage(row)
        if cov.get('odds'):
            out['matches_with_odds'] += 1
        if cov.get('context'):
            out['matches_with_context'] += 1
        if cov.get('weather'):
            out['matches_with_weather'] += 1
        if cov.get('news'):
            out['matches_with_news'] += 1
        if cov.get('xg'):
            out['matches_with_xg'] += 1
        if cov.get('form'):
            out['matches_with_form'] += 1
        ready = bool(cov.get('ready_for_model'))
        if ready:
            out['matches_ready_for_model'] += 1
        if cov.get('ready_for_publish'):
            out['matches_ready_for_publish'] += 1
        kickoff = _parse_dt(row.get('kickoff_utc'))
        if kickoff is None:
            continue
        hours = (kickoff - now).total_seconds() / 3600
        if 0 <= hours <= 6:
            out['matches_next_6h'] += 1
            if ready:
                out['matches_next_6h_ready'] += 1
        if 0 <= hours <= 12:
            out['matches_next_12h'] += 1
            if ready:
                out['matches_next_12h_ready'] += 1
    return out


def _recompute(payload: dict[str, Any], rows: list[dict[str, Any]], raw_total: int, max_matches: int, selection_meta: dict[str, Any]) -> None:
    source_counts: dict[str, int] = {}
    all_source_counts: dict[str, int] = {}
    league_counts: dict[str, int] = {}
    multi_source = 0
    actual_bucket_counts = _bucket_counts(rows)
    for row in rows:
        sources = _sources(row)
        primary = str(row.get('source') or '').strip()
        if primary and primary.lower() not in _PLACEHOLDERS:
            source_counts[primary] = source_counts.get(primary, 0) + 1
        elif sources:
            first = sorted(sources)[0]
            source_counts[first] = source_counts.get(first, 0) + 1
        if len(sources) >= 2:
            multi_source += 1
        for source in sources:
            all_source_counts[source] = all_source_counts.get(source, 0) + 1
        league = str(row.get('league_name') or '').strip()
        if league:
            league_counts[league] = league_counts.get(league, 0) + 1

    counts = dict(payload.get('counts') or {})
    payload['counts_before_top_selection'] = dict(counts)
    counts.update(_coverage_counts(rows))
    counts['matches_total_raw_before_top_selection'] = raw_total
    counts['matches_total_before_top_selection'] = raw_total
    counts['matches_total'] = len(rows)
    counts['matches_selected_top'] = len(rows)
    counts['day_inventory_top_match_limit'] = max_matches
    counts['matches_pruned_by_top_selection'] = max(0, raw_total - len(rows))
    counts['fixture_sources_seen'] = len(all_source_counts)
    counts['multi_source_fixture_matches'] = multi_source
    counts['providers_seen'] = len(source_counts)
    counts['leagues_seen'] = len(league_counts)
    for key, value in actual_bucket_counts.items():
        counts[f'matches_selected_bucket_{key}'] = value
    payload['counts'] = counts
    payload['source_match_counts'] = dict(sorted(source_counts.items(), key=lambda item: (-item[1], item[0])))
    payload['all_source_match_counts'] = dict(sorted(all_source_counts.items(), key=lambda item: (-item[1], item[0])))
    payload['league_match_counts'] = dict(sorted(league_counts.items(), key=lambda item: (-item[1], item[0]))[:50])
    payload['inventory_selection'] = {
        'enabled': True,
        'mode': 'strict_bucketed_top_matches_by_priority',
        'score_version': 'top300_v4_strict_bucket_caps',
        'max_matches': max_matches,
        'raw_total_before_selection': raw_total,
        'selected_matches': len(rows),
        'pruned_matches': max(0, raw_total - len(rows)),
        **selection_meta,
    }


def _select_top(payload: dict[str, Any], max_matches: int) -> dict[str, Any]:
    rows = payload.get('matches')
    if not isinstance(rows, list):
        return payload
    raw_total = len(rows)
    if raw_total <= max_matches:
        payload['inventory_selection'] = {'enabled': True, 'mode': 'strict_bucketed_top_matches_by_priority', 'score_version': 'top300_v4_strict_bucket_caps', 'max_matches': max_matches, 'raw_total_before_selection': raw_total, 'selected_matches': raw_total, 'pruned_matches': 0}
        return payload
    selected, meta = _select([row for row in rows if isinstance(row, dict)], max_matches)
    selected.sort(key=lambda row: (str(row.get('kickoff_utc') or ''), -float(row.get('inventory_top_score') or 0), str(row.get('league_name') or ''), str(row.get('home_team') or '')))
    payload['matches'] = selected
    _recompute(payload, selected, raw_total, max_matches, meta)
    return payload


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    if not _is_day_inventory_process():
        return {'status': 'skipped_not_day_inventory_process'}
    if not _truthy(os.getenv('DAY_INVENTORY_TOP_MATCHES_ENABLED'), True):
        return {'status': 'disabled'}
    try:
        from app.services.day_inventory import DayInventoryStore
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}
    original = getattr(DayInventoryStore, 'build_payload', None)
    if not callable(original):
        return {'status': 'missing_build_payload'}
    if getattr(DayInventoryStore, '_harizon_bucketed_top_v2_patch', False):
        return {'status': 'already_patched'}

    def patched_build_payload(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        payload = original(self, *args, **kwargs)
        return _select_top(payload, _env_int('DAY_INVENTORY_MAX_MATCHES', 300, 1))

    DayInventoryStore.build_payload = patched_build_payload
    DayInventoryStore._harizon_bucketed_top_v2_patch = True
    _INSTALLED = True
    return {'status': 'installed', 'patch': 'day_inventory_bucketed_top_v2', 'max_matches': _env_int('DAY_INVENTORY_MAX_MATCHES', 300, 1)}
