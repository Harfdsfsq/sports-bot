from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc
_INSTALLED = False
_PLACEHOLDER_SOURCES = {'day_inventory', 'inventory', 'unknown', 'none', 'null', ''}

_TOP_LEAGUE_TERMS = (
    'premier league', 'championship', 'serie a', 'serie b', 'la liga', 'segunda división',
    'bundesliga', '2. bundesliga', 'ligue 1', 'ligue 2', 'eredivisie', 'primeira liga',
    'süper lig', 'super lig', 'mls', 'j1 league', 'k league 1', 'allsvenskan',
    'eliteserien', 'ekstraklasa', 'a-league', 'super league', 'copa libertadores',
    'copa sudamericana', 'champions league', 'europa league', 'conference league',
)
_LOW_VALUE_TERMS = (
    'u17', 'u18', 'u19', 'u20', 'u21', 'u23', 'youth', 'women', ' w', 'amateur',
    'reserve', 'reserves', 'friendly', 'kolmonen', 'danmarksserien', 'landesliga',
    'oberliga', 'third league', 'iii liga', 'tercera división', 'serie d', 'primavera',
)
_FINISHED_TERMS = ('finished', 'ft', 'aet', 'pen', 'after penalties')
_BAD_STATUS_TERMS = ('cancelled', 'canceled', 'postponed', 'abandoned', 'walkover', 'interrupted')
_SCHEDULED_TERMS = ('not started', 'scheduled', 'timed', 'pending', 'pre-match', 'pre match', 'ns')


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _is_day_inventory_process() -> bool:
    argv0 = str(sys.argv[0] if sys.argv else '').replace('\\', '/')
    return argv0.endswith('scripts/build_day_inventory.py') or argv0.endswith('build_day_inventory.py')


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == '':
            return max(minimum, default)
        return max(minimum, int(float(str(raw))))
    except Exception:
        return max(minimum, default)


def _split_sources(row: dict[str, Any]) -> set[str]:
    sources: set[str] = set()
    raw_seen = row.get('sources_seen')
    if isinstance(raw_seen, list):
        values = raw_seen
    else:
        values = str(raw_seen or '').split(',')
    for item in values:
        source = str(item or '').strip()
        if source and source.lower() not in _PLACEHOLDER_SOURCES:
            sources.add(source)
    source_ids = row.get('source_ids')
    if isinstance(source_ids, dict):
        for item in source_ids.keys():
            source = str(item or '').strip()
            if source and source.lower() not in _PLACEHOLDER_SOURCES:
                sources.add(source)
    return sources


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get('metadata')
    return meta if isinstance(meta, dict) else {}


def _coverage(row: dict[str, Any]) -> dict[str, Any]:
    coverage = row.get('coverage')
    return coverage if isinstance(coverage, dict) else {}


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


def _status_text(row: dict[str, Any]) -> str:
    meta = _metadata(row)
    parts = [
        row.get('status'),
        row.get('statusName'),
        meta.get('status'),
        meta.get('statusName'),
        meta.get('event_status'),
        meta.get('period'),
    ]
    return ' '.join(str(item or '') for item in parts).strip().lower()


def _league_text(row: dict[str, Any]) -> str:
    return str(row.get('league_name') or row.get('league_key') or '').strip().lower()


def _team_text(row: dict[str, Any]) -> str:
    return f"{row.get('home_team') or ''} {row.get('away_team') or ''}".lower()


def _is_bad_or_finished(row: dict[str, Any]) -> bool:
    status = _status_text(row)
    return any(term in status for term in _BAD_STATUS_TERMS) or any(term in status for term in _FINISHED_TERMS)


def _is_low_value(row: dict[str, Any]) -> bool:
    league = _league_text(row)
    teams = _team_text(row)
    return str(row.get('tier') or '').lower() == 'low' or any(term in league or term in teams for term in _LOW_VALUE_TERMS)


def _top_match_score(row: dict[str, Any]) -> float:
    sources = _split_sources(row)
    coverage = _coverage(row)
    meta = _metadata(row)
    league = _league_text(row)
    teams = _team_text(row)
    status = _status_text(row)
    score = float(row.get('priority') or 0.0)

    if 'odds_api_io' in sources:
        score += 215.0
    if 'bzzoiro' in sources:
        score += 95.0
    if 'football_data' in sources:
        score += 70.0
    if 'thesportsdb' in sources:
        score += 40.0
    if 'sportlogic' in sources:
        score += 35.0
    if 'allsportsapi' in sources:
        score += 30.0
    if 'sstats' in sources:
        score += 18.0

    if len(sources) >= 2:
        score += 90.0 + min(40.0, (len(sources) - 2) * 12.0)

    if bool(coverage.get('odds')):
        score += 90.0
    if bool(coverage.get('context')):
        score += 35.0
    if bool(coverage.get('xg')):
        score += 25.0
    if bool(coverage.get('form')):
        score += 15.0

    if any(term in league for term in _TOP_LEAGUE_TERMS):
        score += 75.0
    if any(term in league or term in teams for term in _LOW_VALUE_TERMS):
        score -= 120.0
    if str(row.get('tier') or '').lower() == 'low':
        score -= 95.0

    if any(term in status for term in _BAD_STATUS_TERMS):
        score -= 500.0
    elif any(term in status for term in _FINISHED_TERMS):
        score -= 320.0
    elif any(term in status for term in _SCHEDULED_TERMS):
        score += 55.0

    if meta.get('odds_count') or meta.get('has_sstats_odds'):
        score += 25.0

    kickoff = _parse_dt(row.get('kickoff_utc'))
    if kickoff is not None:
        hours = (kickoff - datetime.now(UTC)).total_seconds() / 3600.0
        if 0 <= hours <= 6:
            score += 50.0
        elif 6 < hours <= 12:
            score += 40.0
        elif 12 < hours <= 24:
            score += 28.0
        elif hours < -2:
            score -= 240.0
        elif hours < 0:
            score -= 85.0

    return round(score, 4)


def _bucket_name(row: dict[str, Any]) -> str:
    sources = _split_sources(row)
    if 'odds_api_io' in sources:
        return 'odds_api_io'
    if 'bzzoiro' in sources or len(sources) >= 2:
        return 'multi_source_or_bzzoiro'
    if sources == {'sstats'} or ('sstats' in sources and not {'odds_api_io', 'bzzoiro', 'football_data', 'thesportsdb'} & sources):
        return 'sstats_only'
    return 'other'


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[tuple[float, str, str, dict[str, Any]]] = []
    for row in rows:
        score = _top_match_score(row)
        row['inventory_top_score'] = score
        row['inventory_selection_bucket'] = _bucket_name(row)
        kickoff = str(row.get('kickoff_utc') or '')
        key = str(row.get('canonical_match_id') or row.get('match_key') or '')
        scored.append((score, kickoff, key, row))
    return [item[3] for item in sorted(scored, key=lambda item: (-item[0], item[1], item[2]))]


def _take_unique(pool: list[dict[str, Any]], selected: list[dict[str, Any]], selected_keys: set[str], limit: int) -> list[dict[str, Any]]:
    taken: list[dict[str, Any]] = []
    if limit <= 0:
        return taken
    for row in pool:
        key = str(row.get('canonical_match_id') or row.get('match_key') or '')
        if not key or key in selected_keys:
            continue
        selected.append(row)
        selected_keys.add(key)
        taken.append(row)
        if len(taken) >= limit:
            break
    return taken


def _select_bucketed(rows: list[dict[str, Any]], max_matches: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    odds_target = min(max_matches, _env_int('DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES', 160, 0))
    multi_max = min(max_matches, _env_int('DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES', 80, 0))
    sstats_max = min(max_matches, _env_int('DAY_INVENTORY_SSTATS_ONLY_MAX_MATCHES', 60, 0))
    other_max = min(max_matches, _env_int('DAY_INVENTORY_OTHER_MAX_MATCHES', 60, 0))
    exclude_bad = _truthy(os.getenv('DAY_INVENTORY_EXCLUDE_BAD_STATUS_FROM_TOP'), True)
    exclude_low_value_first = _truthy(os.getenv('DAY_INVENTORY_EXCLUDE_LOW_VALUE_FROM_FIRST_PASS'), True)

    sorted_rows = _sort_rows([row for row in rows if isinstance(row, dict)])
    candidate_rows = [row for row in sorted_rows if not (exclude_bad and _is_bad_or_finished(row))]
    if exclude_low_value_first:
        first_pass = [row for row in candidate_rows if not _is_low_value(row)]
        fallback_pass = [row for row in candidate_rows if _is_low_value(row)]
    else:
        first_pass = candidate_rows
        fallback_pass = []

    buckets: dict[str, list[dict[str, Any]]] = {
        'odds_api_io': [],
        'multi_source_or_bzzoiro': [],
        'sstats_only': [],
        'other': [],
    }
    for row in first_pass:
        buckets.setdefault(str(row.get('inventory_selection_bucket') or _bucket_name(row)), []).append(row)

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    bucket_selected_counts: dict[str, int] = {}

    bucket_selected_counts['odds_api_io'] = len(_take_unique(buckets.get('odds_api_io', []), selected, selected_keys, odds_target))
    bucket_selected_counts['multi_source_or_bzzoiro'] = len(_take_unique(buckets.get('multi_source_or_bzzoiro', []), selected, selected_keys, min(multi_max, max_matches - len(selected))))
    bucket_selected_counts['sstats_only'] = len(_take_unique(buckets.get('sstats_only', []), selected, selected_keys, min(sstats_max, max_matches - len(selected))))
    bucket_selected_counts['other'] = len(_take_unique(buckets.get('other', []), selected, selected_keys, min(other_max, max_matches - len(selected))))

    # If the required 300 is not full, fill by global score from the remaining good rows.
    remaining_first_pass = [row for row in first_pass if str(row.get('canonical_match_id') or row.get('match_key') or '') not in selected_keys]
    fill_good = _take_unique(remaining_first_pass, selected, selected_keys, max_matches - len(selected))
    bucket_selected_counts['global_good_fill'] = len(fill_good)

    # Last-resort fallback: low-value rows, still sorted by score, only if there are not enough good rows.
    fill_low = _take_unique(fallback_pass, selected, selected_keys, max_matches - len(selected))
    bucket_selected_counts['low_value_fallback_fill'] = len(fill_low)

    selection_meta = {
        'bucket_mode': 'odds_multi_sstats_caps_v1',
        'bucket_targets': {
            'odds_api_io_target': odds_target,
            'multi_source_or_bzzoiro_max': multi_max,
            'sstats_only_max': sstats_max,
            'other_max': other_max,
        },
        'bucket_selected_counts': bucket_selected_counts,
        'candidate_counts_by_bucket': {name: len(values) for name, values in buckets.items()},
        'excluded_bad_status_from_first_pass': exclude_bad,
        'excluded_low_value_from_first_pass': exclude_low_value_first,
        'candidate_rows_after_bad_status_filter': len(candidate_rows),
        'candidate_rows_first_pass': len(first_pass),
        'candidate_rows_low_value_fallback': len(fallback_pass),
    }
    return selected[:max_matches], selection_meta


def _recompute_selected_coverage_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    now = datetime.now(UTC)
    counts = {
        'matches_with_odds': 0,
        'matches_with_context': 0,
        'matches_with_weather': 0,
        'matches_with_news': 0,
        'matches_with_xg': 0,
        'matches_with_form': 0,
        'matches_ready_for_model': 0,
        'matches_ready_for_publish': 0,
        'matches_next_6h': 0,
        'matches_next_6h_ready': 0,
        'matches_next_12h': 0,
        'matches_next_12h_ready': 0,
    }
    for row in rows:
        coverage = _coverage(row)
        if bool(coverage.get('odds')):
            counts['matches_with_odds'] += 1
        if bool(coverage.get('context')):
            counts['matches_with_context'] += 1
        if bool(coverage.get('weather')):
            counts['matches_with_weather'] += 1
        if bool(coverage.get('news')):
            counts['matches_with_news'] += 1
        if bool(coverage.get('xg')):
            counts['matches_with_xg'] += 1
        if bool(coverage.get('form')):
            counts['matches_with_form'] += 1
        ready = bool(coverage.get('ready_for_model'))
        if ready:
            counts['matches_ready_for_model'] += 1
        if bool(coverage.get('ready_for_publish')):
            counts['matches_ready_for_publish'] += 1
        kickoff = _parse_dt(row.get('kickoff_utc'))
        if kickoff is None:
            continue
        hours = (kickoff - now).total_seconds() / 3600.0
        if 0 <= hours <= 6:
            counts['matches_next_6h'] += 1
            if ready:
                counts['matches_next_6h_ready'] += 1
        if 0 <= hours <= 12:
            counts['matches_next_12h'] += 1
            if ready:
                counts['matches_next_12h_ready'] += 1
    return counts


def _recompute_counts(payload: dict[str, Any], rows: list[dict[str, Any]], *, raw_total_before_selection: int, max_matches: int, selection_meta: dict[str, Any]) -> None:
    source_counts: dict[str, int] = {}
    all_source_counts: dict[str, int] = {}
    league_counts: dict[str, int] = {}
    selected_bucket_counts: dict[str, int] = {}
    multi_source = 0
    for row in rows:
        sources = _split_sources(row)
        bucket = str(row.get('inventory_selection_bucket') or _bucket_name(row))
        selected_bucket_counts[bucket] = selected_bucket_counts.get(bucket, 0) + 1
        primary_source = str(row.get('source') or '').strip()
        if primary_source and primary_source.lower() not in _PLACEHOLDER_SOURCES:
            source_counts[primary_source] = source_counts.get(primary_source, 0) + 1
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
    original_counts = dict(counts)
    selected_coverage_counts = _recompute_selected_coverage_counts(rows)
    counts.update(selected_coverage_counts)
    counts['matches_total_raw_before_top_selection'] = raw_total_before_selection
    counts['matches_total_before_top_selection'] = raw_total_before_selection
    counts['matches_total'] = len(rows)
    counts['matches_selected_top'] = len(rows)
    counts['day_inventory_top_match_limit'] = max_matches
    counts['matches_pruned_by_top_selection'] = max(0, raw_total_before_selection - len(rows))
    counts['fixture_sources_seen'] = len(all_source_counts)
    counts['multi_source_fixture_matches'] = multi_source
    counts['providers_seen'] = len(source_counts)
    counts['leagues_seen'] = len(league_counts)
    for bucket_name, bucket_count in selected_bucket_counts.items():
        counts[f'matches_selected_bucket_{bucket_name}'] = bucket_count

    payload['counts_before_top_selection'] = original_counts
    payload['counts'] = counts
    payload['source_match_counts'] = dict(sorted(source_counts.items(), key=lambda item: (-item[1], item[0])))
    payload['all_source_match_counts'] = dict(sorted(all_source_counts.items(), key=lambda item: (-item[1], item[0])))
    payload['league_match_counts'] = dict(sorted(league_counts.items(), key=lambda item: (-item[1], item[0]))[:50])
    payload['inventory_selection'] = {
        'enabled': True,
        'mode': 'bucketed_top_matches_by_priority',
        'max_matches': max_matches,
        'raw_total_before_selection': raw_total_before_selection,
        'selected_matches': len(rows),
        'pruned_matches': max(0, raw_total_before_selection - len(rows)),
        'score_version': 'top300_v3_bucketed_caps',
        **selection_meta,
    }


def _select_top_matches(payload: dict[str, Any], max_matches: int) -> dict[str, Any]:
    rows = payload.get('matches')
    if not isinstance(rows, list):
        return payload
    raw_total = len(rows)
    if max_matches <= 0 or raw_total <= max_matches:
        payload['inventory_selection'] = {
            'enabled': True,
            'mode': 'bucketed_top_matches_by_priority',
            'max_matches': max_matches,
            'raw_total_before_selection': raw_total,
            'selected_matches': raw_total,
            'pruned_matches': 0,
            'score_version': 'top300_v3_bucketed_caps',
        }
        return payload

    selected, selection_meta = _select_bucketed([row for row in rows if isinstance(row, dict)], max_matches)
    selected.sort(key=lambda row: (str(row.get('kickoff_utc') or ''), -float(row.get('inventory_top_score') or 0.0), str(row.get('league_name') or ''), str(row.get('home_team') or '')))
    payload['matches'] = selected
    _recompute_counts(payload, selected, raw_total_before_selection=raw_total, max_matches=max_matches, selection_meta=selection_meta)
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
    if getattr(DayInventoryStore, '_harizon_top_matches_patch', False):
        return {'status': 'already_patched'}

    def patched_build_payload(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        payload = original(self, *args, **kwargs)
        max_matches = _env_int('DAY_INVENTORY_MAX_MATCHES', 300, 1)
        return _select_top_matches(payload, max_matches)

    DayInventoryStore.build_payload = patched_build_payload
    DayInventoryStore._harizon_top_matches_patch = True
    _INSTALLED = True
    return {'status': 'installed', 'max_matches': _env_int('DAY_INVENTORY_MAX_MATCHES', 300, 1)}
