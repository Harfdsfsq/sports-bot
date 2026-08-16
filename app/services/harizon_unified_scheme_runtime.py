from __future__ import annotations

"""Executable HARIZON run-bot scheme.

The bot's target mechanics are encoded here as runtime invariants: a cumulative
300-match day inventory, primary-provider-first enrichment, 2+ odds/context
coverage, totals/spreads-only publication, and a 2-hour line-movement lifecycle.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.utils import ensure_utc

UTC = timezone.utc
REPORT_PATH = Path('.data/exports/latest-harizon-unified-runtime-scheme.json')
PRIMARY_PROVIDERS = ('odds_api_io', 'bzzoiro', 'sstats', 'sportlogic')
ALLOWED_FAMILIES = {'totals', 'spreads'}
CONTEXT_ONLY = {
    'api_football', 'clubelo', 'football_data', 'futrixmetrics', 'gnews', 'newsapi',
    'openfootball', 'openligadb', 'open_meteo', 'thesportsdb', 'weather', 'weatherapi',
    'espn', 'news', 'currents', 'guardian', 'wikidata', 'self_history', 'highlightly',
}
SOURCE_ALIASES = {
    'account1': 'odds_api_io', 'account2': 'odds_api_io',
    'odds_api_io_account1': 'odds_api_io', 'odds_api_io_account2': 'odds_api_io',
    'oddsapiio': 'odds_api_io', 'odds_api': 'odds_api_io',
    'bzzoiro_v2': 'bzzoiro', 'bzzoiro_predictions': 'bzzoiro',
    'sstats_current_odds': 'sstats', 'sportlogic_controlled': 'sportlogic',
    'rapidapi_odds': 'rapidapi_odds_bridge', 'rapidapi_odds_feed': 'rapidapi_odds_bridge',
}
ENV_DEFAULTS = {
    'HARIZON_UNIFIED_SCHEME_ENABLED': 'true',
    'HARIZON_SCHEME_VERSION': 'day-inventory-300-primary-first-v1',
    'DAY_INVENTORY_TARGET_SIZE': '300',
    'DAY_INVENTORY_MAX_MATCHES': '300',
    'DAY_INVENTORY_FORCE_TOP_300': 'true',
    'DAY_INVENTORY_FORCE_FULL_300': 'true',
    'DAY_INVENTORY_PRESERVE_CACHED_EVIDENCE': 'true',
    'MAX_MATCHES_FOR_ODDS_FETCH': '300',
    'HARIZON_PRIMARY_PROVIDERS': ','.join(PRIMARY_PROVIDERS),
    'HARIZON_SUPPLEMENTAL_API_MODE': 'shortlist_and_missing_role_only',
    'SUPPLEMENTAL_PROVIDERS_REQUIRE_SHORTLIST': 'true',
    'SUPPLEMENTAL_PROVIDERS_REQUIRE_MISSING_ROLE': 'true',
    'PUBLICATION_ALLOWED_MARKET_FAMILIES': 'totals,spreads',
    'HARIZON_ALLOWED_PUBLICATION_FAMILIES': 'totals,spreads',
    'PUBLISH_MIN_ODDS_SOURCES': '2',
    'PUBLISH_MIN_CONTEXT_SOURCES': '2',
    'MIN_CONTEXT_SOURCES_PUBLISH': '2',
    'PUBLISH_MIN_BOOKS': '2',
    'MIN_BOOKS_PUBLISH': '2',
    'PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE': 'true',
    'PUBLISH_REJECT_CONTEXT_AS_PRICE_CONFIRMATION': 'false',
    'HARIZON_RUN_INTERVAL_HOURS': '2',
    'HARIZON_LINE_MOVEMENT_GUARD_ENABLED': 'true',
    'HARIZON_MOVEMENT_OBSERVATION_REQUIRED': 'true',
    'ODDS_MOVEMENT_SNAPSHOTS_ENABLED': 'true',
    'ODDS_API_IO_MAX_REQUESTS_PER_RUN': '100',
    'BZZOIRO_MAX_REQUESTS_PER_RUN': '200',
    'SSTATS_MAX_REQUESTS_PER_RUN': '150',
    'SPORTLOGIC_MAX_REQUESTS_PER_RUN': '40',
}


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    if raw in {'0', 'false', 'no', 'off', 'none', 'null'}:
        return False
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _int(value: Any, default: int) -> int:
    try:
        return int(float(str(value).strip())) if value not in (None, '') else default
    except Exception:
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip()) if value not in (None, '') else default
    except Exception:
        return default


def _norm_source(value: Any) -> str:
    raw = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    raw = '_'.join(part for part in raw.split('_') if part)
    if raw in {'', 'unknown', 'none', 'null', 'inventory', 'day_inventory'}:
        return ''
    return SOURCE_ALIASES.get(raw, raw)


def _sources(value: Any) -> list[str]:
    if isinstance(value, dict):
        raw = list(value.keys())
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = str(value or '').replace(';', ',').split(',')
    out: list[str] = []
    for item in raw:
        src = _norm_source(item)
        if src and src not in out:
            out.append(src)
    return out


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _set(obj: Any, key: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[key] = value
    else:
        try:
            setattr(obj, key, value)
        except Exception:
            pass


def _summary(candidate: Any) -> dict[str, Any]:
    value = _get(candidate, 'source_summary', None)
    if not isinstance(value, dict):
        value = {}
        _set(candidate, 'source_summary', value)
    return value


def _diag(candidate: Any) -> dict[str, Any]:
    value = _get(candidate, 'diagnostics', None)
    if not isinstance(value, dict):
        value = {}
        _set(candidate, 'diagnostics', value)
    return value


def _reason(candidate: Any, reason: str) -> None:
    reasons = _get(candidate, 'reasons', None)
    if not isinstance(reasons, list):
        reasons = []
        _set(candidate, 'reasons', reasons)
    if reason not in reasons:
        reasons.append(reason)


def _has_movement_observation(candidate: Any) -> bool:
    s, d = _summary(candidate), _diag(candidate)
    movement = s.get('line_movement') or d.get('line_movement') or s.get('movement') or d.get('movement')
    if isinstance(movement, dict):
        if _truthy(movement.get('has_previous') or movement.get('observed_twice') or movement.get('movement_ok')):
            return True
        if movement.get('previous_price') not in (None, '') or movement.get('previous_decimal_odds') not in (None, ''):
            return True
    if bool(_get(candidate, 'already_used', False)) or _truthy(s.get('reused_candidate')):
        return True
    for key in ('movement_label', 'line_movement_label', 'odds_movement_label'):
        if str(s.get(key) or d.get(key) or '').strip():
            return True
    return False


def _line_movement_decision(candidate: Any, now_utc: datetime | None = None) -> dict[str, Any]:
    now_utc = now_utc or datetime.now(UTC)
    if not _truthy(os.getenv('HARIZON_LINE_MOVEMENT_GUARD_ENABLED'), True):
        return {'passed': True, 'reason': 'disabled'}
    try:
        kickoff = ensure_utc(_get(candidate, 'commence_time'))
    except Exception:
        return {'passed': False, 'reason': 'missing_kickoff'}
    seconds = (kickoff - now_utc).total_seconds()
    if seconds <= 0:
        return {'passed': False, 'reason': 'already_started'}
    interval = max(1, _int(os.getenv('HARIZON_RUN_INTERVAL_HOURS'), 2))
    next_run_cutoff = now_utc + timedelta(hours=interval)
    observed = _has_movement_observation(candidate)
    if kickoff <= next_run_cutoff:
        return {'passed': True, 'reason': 'inside_next_run_window_publish_now', 'seconds_to_kickoff': round(seconds, 3), 'has_prior_observation': observed}
    if observed:
        return {'passed': True, 'reason': 'movement_observed_before_next_window', 'seconds_to_kickoff': round(seconds, 3), 'has_prior_observation': True}
    return {'passed': False, 'reason': 'hold_for_next_run_line_movement', 'seconds_to_kickoff': round(seconds, 3), 'has_prior_observation': False}


def _coverage(candidate: Any) -> dict[str, Any]:
    s, d = _summary(candidate), _diag(candidate)
    odds_sources: set[str] = set()
    context_sources: set[str] = set()
    books: set[str] = set()
    for key in ('odds_sources', 'price_sources', 'selected_odds_sources', 'exact_odds_sources', 'publication_odds_sources', 'provider_sources', 'sources'):
        odds_sources.update(src for src in _sources(s.get(key) or d.get(key)) if src not in CONTEXT_ONLY)
    for key in ('context_sources', 'confirmation_sources', 'merged_context_sources', 'context_provider_sources'):
        context_sources.update(_sources(s.get(key) or d.get(key)))
    raw_offers = _get(candidate, 'raw_bucket_offers', []) or []
    for row in raw_offers if isinstance(raw_offers, list) else []:
        if isinstance(row, dict):
            src = _norm_source(row.get('source') or row.get('provider'))
            if src and src not in CONTEXT_ONLY:
                odds_sources.add(src)
            book = str(row.get('bookmaker') or row.get('bookmaker_name') or '').strip().lower()
            if book:
                books.add(book)
    for key in ('bookmakers', 'books', 'selected_bookmakers'):
        books.update(_sources(s.get(key) or d.get(key)))
    return {
        'odds_sources': sorted(odds_sources),
        'context_sources': sorted(context_sources),
        'bookmakers': sorted(books),
        'odds_source_count': max(len(odds_sources), _int(_get(candidate, 'sources_count', 0), 0), _int(s.get('odds_source_count') or s.get('odds_sources_count'), 0)),
        'context_source_count': max(len(context_sources), _int(s.get('context_source_count') or s.get('context_sources_count'), 0)),
        'bookmaker_count': max(len(books), _int(_get(candidate, 'books_count', 0), 0), _int(s.get('bookmaker_count'), 0)),
        'min_odds_sources': 2,
        'min_context_sources': 2,
        'min_bookmakers': 2,
    }


def _candidate_passes_unified_contract(candidate: Any, now_utc: datetime | None = None) -> tuple[bool, dict[str, Any]]:
    cov = _coverage(candidate)
    move = _line_movement_decision(candidate, now_utc=now_utc)
    family = str(_get(candidate, 'family', '') or '').strip()
    reasons: list[str] = []
    if family not in ALLOWED_FAMILIES:
        reasons.append(f'family_not_allowed={family}')
    if cov['odds_source_count'] < 2:
        reasons.append('odds_sources_lt_2')
    if cov['context_source_count'] < 2:
        reasons.append('context_sources_lt_2')
    if cov['bookmaker_count'] < 2:
        reasons.append('bookmakers_lt_2')
    if not move.get('passed'):
        reasons.append(str(move.get('reason') or 'line_movement_guard_failed'))
    report = {'scheme_version': os.getenv('HARIZON_SCHEME_VERSION', 'day-inventory-300-primary-first-v1'), 'passed': not reasons, 'family': family, 'coverage': cov, 'line_movement': move, 'reasons': reasons}
    return not reasons, report


def _kickoff_utc(row: Any) -> datetime | None:
    """Parse an inventory row kickoff into an aware UTC datetime."""
    if not isinstance(row, dict):
        return None
    raw = row.get('kickoff_utc') or row.get('kickoff_local')
    if raw in (None, ''):
        return None
    text = str(raw).strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_upcoming(row: Any, started_cutoff: datetime) -> bool:
    kickoff = _kickoff_utc(row)
    return kickoff is not None and kickoff >= started_cutoff


def _inventory_rank(row: Any, started_cutoff: datetime) -> tuple[int, float, float]:
    """Rank inventory rows so the top-N cut keeps matches that can still be bet.

    Group 0: upcoming fixtures, soonest kickoff first, higher priority first.
    Group 1: already started/finished fixtures, most recent first (history filler).
    Group 2: rows without a usable kickoff.
    """
    kickoff = _kickoff_utc(row)
    if kickoff is None:
        return (2, 0.0, 0.0)
    stamp = kickoff.timestamp()
    priority = _float(row.get('priority'), 0.0) if isinstance(row, dict) else 0.0
    if kickoff >= started_cutoff:
        return (0, stamp, -priority)
    return (1, -stamp, -priority)


def _finalize_inventory_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    now = datetime.now(UTC).isoformat()
    rows = payload.get('matches') if isinstance(payload.get('matches'), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        coverage = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        meta = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        source_ids = row.get('source_ids') if isinstance(row.get('source_ids'), dict) else {}
        sources_seen = sorted(set(_sources(row.get('sources_seen')) + _sources(source_ids) + _sources(meta.get('sources_seen'))))
        odds_count = _int(coverage.get('odds_source_count') or meta.get('odds_source_count'), 0)
        context_count = _int(coverage.get('context_source_count') or meta.get('context_source_count'), 0)
        odds_ready = bool(coverage.get('odds')) or odds_count >= 2
        context_ready = bool(coverage.get('context')) or context_count >= 2
        row['harizon_contract'] = {
            'target_inventory_rank_max': 300,
            'sources_seen': sources_seen,
            'source_count': len(sources_seen),
            'odds_source_count': odds_count,
            'context_source_count': context_count,
            'needs_odds_backfill': not odds_ready,
            'needs_context_backfill': not context_ready,
            'needs_movement_refresh': bool(odds_ready),
            'primary_providers': list(PRIMARY_PROVIDERS),
            'updated_at_utc': now,
        }
    now_dt = datetime.now(UTC)
    grace_minutes = max(0, _int(os.getenv('DAY_INVENTORY_STARTED_GRACE_MINUTES'), 15))
    started_cutoff = now_dt - timedelta(minutes=grace_minutes)
    rows.sort(key=lambda row: _inventory_rank(row, started_cutoff))
    target = max(1, _int(os.getenv('DAY_INVENTORY_TARGET_SIZE'), 300))
    upcoming_available = sum(1 for row in rows if _is_upcoming(row, started_cutoff))
    if len(rows) > target:
        del rows[target:]
    counts = payload.setdefault('counts', {})
    if isinstance(counts, dict):
        counts['harizon_inventory_target'] = target
        counts['harizon_inventory_kept'] = len(rows)
        counts['harizon_inventory_upcoming_available'] = upcoming_available
        counts['harizon_inventory_upcoming_kept'] = sum(1 for row in rows if _is_upcoming(row, started_cutoff))
        counts['harizon_inventory_started_kept'] = sum(1 for row in rows if not _is_upcoming(row, started_cutoff))
        counts['harizon_needs_odds_backfill'] = sum(1 for row in rows if isinstance(row, dict) and (row.get('harizon_contract') or {}).get('needs_odds_backfill'))
        counts['harizon_needs_context_backfill'] = sum(1 for row in rows if isinstance(row, dict) and (row.get('harizon_contract') or {}).get('needs_context_backfill'))
        counts['harizon_movement_refresh_targets'] = sum(1 for row in rows if isinstance(row, dict) and (row.get('harizon_contract') or {}).get('needs_movement_refresh'))
    payload['harizon_unified_scheme'] = {
        'version': os.getenv('HARIZON_SCHEME_VERSION', 'day-inventory-300-primary-first-v1'),
        'target_matches': target,
        'primary_providers': list(PRIMARY_PROVIDERS),
        'supplemental_mode': os.getenv('HARIZON_SUPPLEMENTAL_API_MODE', 'shortlist_and_missing_role_only'),
        'publication_families': sorted(ALLOWED_FAMILIES),
        'min_odds_sources': 2,
        'min_context_sources': 2,
        'run_interval_hours': _int(os.getenv('HARIZON_RUN_INTERVAL_HOURS'), 2),
        'ranking_policy': 'upcoming_first_then_started_filler',
        'started_grace_minutes': grace_minutes,
        'upcoming_available': upcoming_available,
        'updated_at_utc': now,
    }
    return payload


def apply_env_defaults() -> int:
    applied = 0
    for key, value in ENV_DEFAULTS.items():
        if os.getenv(key) is None:
            os.environ[key] = value
            applied += 1
    return applied


def _write_report(extra: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'installed_at_utc': datetime.now(UTC).isoformat(),
            'scheme_version': os.getenv('HARIZON_SCHEME_VERSION', 'day-inventory-300-primary-first-v1'),
            'primary_providers': list(PRIMARY_PROVIDERS),
            'inventory_target': _int(os.getenv('DAY_INVENTORY_TARGET_SIZE'), 300),
            'publication_contract': {'allowed_families': sorted(ALLOWED_FAMILIES), 'min_odds_sources': 2, 'min_context_sources': 2, 'min_bookmakers': 2},
            'line_movement_contract': {'enabled': _truthy(os.getenv('HARIZON_LINE_MOVEMENT_GUARD_ENABLED'), True), 'run_interval_hours': _int(os.getenv('HARIZON_RUN_INTERVAL_HOURS'), 2)},
            'extra': extra,
        }
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def install() -> dict[str, Any]:
    if not _truthy(os.getenv('HARIZON_UNIFIED_SCHEME_ENABLED'), True):
        return {'enabled': False, 'reason': 'disabled'}
    result: dict[str, Any] = {'enabled': True, 'env_defaults_applied': apply_env_defaults(), 'patches': []}
    try:
        from app.services.day_inventory import DayInventoryStore
        if not getattr(DayInventoryStore, '_harizon_unified_scheme_installed', False):
            original_build = DayInventoryStore.build_payload
            original_save = DayInventoryStore.save_inventory
            def build_payload(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
                return _finalize_inventory_payload(original_build(self, *args, **kwargs))
            def save_inventory(self: Any, payload: dict[str, Any]) -> dict[str, str]:
                return original_save(self, _finalize_inventory_payload(payload))
            DayInventoryStore.build_payload = build_payload
            DayInventoryStore.save_inventory = save_inventory
            DayInventoryStore._harizon_unified_scheme_installed = True
            result['patches'].append('DayInventoryStore')
    except Exception as exc:
        result['day_inventory_error'] = f'{type(exc).__name__}: {exc}'
    try:
        from app.services.runner import PredictionRunner
        if not getattr(PredictionRunner, '_harizon_unified_scheme_installed', False):
            original_filter = PredictionRunner._filter_publishable_candidates
            original_context = PredictionRunner._select_context_enrichment_matches
            def filter_publishable(self: Any, candidates: list[Any]) -> list[Any]:
                base = original_filter(self, candidates)
                kept: list[Any] = []
                blocked = 0
                now = datetime.now(UTC)
                for cand in base:
                    passed, report = _candidate_passes_unified_contract(cand, now_utc=now)
                    _diag(cand)['harizon_unified_scheme'] = report
                    _summary(cand)['harizon_unified_scheme'] = report
                    if passed:
                        kept.append(cand)
                    else:
                        blocked += 1
                        _summary(cand)['publication_blocked_reason'] = 'harizon_unified_scheme_contract'
                        for reason in report.get('reasons') or []:
                            _reason(cand, f'harizon_scheme={reason}')
                self.provider_status['harizon_unified_scheme'] = {'enabled': True, 'candidates_checked': len(base), 'candidates_blocked': blocked, 'candidates_kept': len(kept)}
                return kept
            def select_context(self: Any, matches: list[Any], offers_by_match: dict[str, list[Any]], now_utc: datetime, market_signals_by_match: dict[str, dict[str, Any]] | None = None) -> tuple[list[Any], dict[str, Any]]:
                selected, meta = original_context(self, matches, offers_by_match, now_utc, market_signals_by_match)
                selected_keys = {getattr(m, 'match_key', '') for m in selected}
                limit = max(0, _int(getattr(self.settings, 'context_enrichment_match_limit', 0), 0)) or max(0, _int(os.getenv('DAY_INVENTORY_TARGET_SIZE'), 300))
                remaining = max(0, min(limit, 300) - len(selected))
                if remaining:
                    ranked: list[tuple[tuple[float, str], Any]] = []
                    for m in matches:
                        key = getattr(m, 'match_key', '')
                        if key and key not in selected_keys:
                            has_offers = bool((offers_by_match or {}).get(key))
                            ranked.append(((1.0 if has_offers else 0.0, str(getattr(m, 'commence_time', ''))), m))
                    ranked.sort(key=lambda item: item[0], reverse=True)
                    selected.extend(m for _, m in ranked[:remaining])
                meta = dict(meta or {})
                meta['harizon_unified_scheme'] = {'context_target_selected': len(selected), 'target_inventory_size': 300, 'additive_backfill_enabled': True}
                return selected[:limit], meta
            PredictionRunner._filter_publishable_candidates = filter_publishable
            PredictionRunner._select_context_enrichment_matches = select_context
            PredictionRunner._harizon_unified_scheme_installed = True
            result['patches'].append('PredictionRunner')
    except Exception as exc:
        result['runner_error'] = f'{type(exc).__name__}: {exc}'
    _write_report(result)
    return result
