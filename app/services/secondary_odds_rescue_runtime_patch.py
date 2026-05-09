from __future__ import annotations

"""Runtime secondary odds rescue layer.

Primary lines come from odds-api.io. Publication guards require at least two
independent odds sources. SportLogic/AllSportsAPI often return fixtures/context
without prices, so this patch wires the existing RapidApiOddsBridgeProvider into
the first non-empty primary fetch_offers result instead of waiting for a SportLogic
odds slot that may never execute.

The patch only adds extra offers and diagnostics. It does not change model,
quality, xG, publication-family, or Telegram guards.
"""

import asyncio
import json
import os
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-secondary-odds-rescue.json'
_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _provider_name(runner: Any, provider: Any) -> str:
    try:
        name_fn = getattr(runner, '_provider_name', None)
        if callable(name_fn):
            return str(name_fn(provider) or '').strip().lower()
    except Exception:
        pass
    try:
        module = getattr(provider.__class__, '__module__', '')
        name = getattr(provider.__class__, '__name__', '')
        text = f'{module}.{name}'.lower()
        if 'odds_api_io' in text or 'oddsapiio' in text:
            return 'odds_api_io'
        if 'sportlogic' in text:
            return 'sportlogic'
        if 'allsports' in text:
            return 'allsportsapi'
        if 'rapidapi' in text:
            return 'rapidapi_odds_bridge'
        return module.rsplit('.', 1)[-1].lower() or name.lower() or 'unknown'
    except Exception:
        return 'unknown'


def _merge_offer_maps(base: Any, extra: Any) -> dict[str, list[Any]]:
    merged: dict[str, list[Any]] = defaultdict(list)
    if isinstance(base, dict):
        for match_key, offers in base.items():
            if isinstance(offers, list):
                merged[str(match_key)].extend(offers)
    if isinstance(extra, dict):
        for match_key, offers in extra.items():
            if isinstance(offers, list):
                merged[str(match_key)].extend(offers)
    return {k: v for k, v in merged.items() if v}


def _count_offers(offers_by_match: Any) -> int:
    if not isinstance(offers_by_match, dict):
        return 0
    return sum(len(v) for v in offers_by_match.values() if isinstance(v, list))


def _source_counts(offers_by_match: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(offers_by_match, dict):
        return counts
    for offers in offers_by_match.values():
        if not isinstance(offers, list):
            continue
        for offer in offers:
            source = str(getattr(offer, 'source', '') or (offer.get('source') if isinstance(offer, dict) else '') or 'unknown').strip() or 'unknown'
            counts[source] = counts.get(source, 0) + 1
    return counts


def _sample_offers(offers_by_match: Any, limit: int = 8) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(offers_by_match, dict):
        return rows
    for match_key, offers in offers_by_match.items():
        if not isinstance(offers, list):
            continue
        for offer in offers:
            try:
                row = asdict(offer)
            except Exception:
                row = dict(offer) if isinstance(offer, dict) else {}
            row['match_key'] = match_key
            rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows


def _write_report(payload: dict[str, Any]) -> None:
    try:
        EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _select_matches(matches: Any) -> list[Any]:
    if not isinstance(matches, list):
        return []
    soccer = [m for m in matches if str(getattr(m, 'sport_key', '') or '').lower() == 'soccer']
    now = datetime.now(UTC)
    max_matches = max(1, _to_int(os.getenv('SECONDARY_ODDS_RESCUE_MATCH_LIMIT') or os.getenv('RAPIDAPI_ODDS_MATCH_LIMIT') or 14, 14))
    max_hours = max(1, _to_int(os.getenv('SECONDARY_ODDS_RESCUE_WINDOW_HOURS') or os.getenv('SECONDARY_ODDS_RESCUE_NEAR_WINDOW_HOURS') or 8, 8))
    filtered = []
    for match in soccer:
        start = getattr(match, 'commence_time', None)
        try:
            hours = (start.astimezone(UTC) - now).total_seconds() / 3600.0
        except Exception:
            hours = 999.0
        if -0.25 <= hours <= max_hours:
            filtered.append((hours, match))
    filtered.sort(key=lambda item: (abs(item[0]), getattr(item[1], 'league_name', ''), getattr(item[1], 'home_team', '')))
    return [m for _, m in filtered[:max_matches]]


def _should_trigger(provider_name: str, base_offers: Any) -> tuple[bool, str]:
    mode = str(os.getenv('SECONDARY_ODDS_RESCUE_TRIGGER_PROVIDER') or os.getenv('SECONDARY_ODDS_RESCUE_TRIGGER') or 'primary_odds_non_empty').strip().lower()
    base_count = _count_offers(base_offers)
    min_primary = max(1, _to_int(os.getenv('SECONDARY_ODDS_RESCUE_MIN_PRIMARY_OFFERS') or 80, 80))
    if mode in {'auto', 'primary', 'primary_odds', 'primary_odds_non_empty', 'odds_api_io_empty_or_thin'}:
        if base_count >= min_primary:
            return True, f'primary_offers:{base_count}>={min_primary}'
        return False, f'base_offers_below_min:{base_count}/{min_primary}'
    allowed = {item.strip().lower() for item in mode.split(',') if item.strip()}
    if provider_name in allowed:
        return True, f'explicit_provider:{provider_name}'
    return False, f'provider_not_trigger:{provider_name};allowed={sorted(allowed)};base_offers={base_count}'


async def _fetch_rapidapi_secondary(runner: Any, matches: list[Any]) -> tuple[dict[str, list[Any]], dict[str, Any], dict[str, Any]]:
    from app.providers.rapidapi_odds_bridge import RapidApiOddsBridgeProvider

    os.environ.setdefault('ENABLE_RAPIDAPI_ODDS_BRIDGE', 'true')
    os.environ.setdefault('RAPIDAPI_ODDS_MATCH_LIMIT', str(max(1, len(matches))))
    os.environ.setdefault('RAPIDAPI_ODDS_MAX_HTTP_REQUESTS_PER_RUN', os.getenv('SECONDARY_ODDS_RESCUE_MAX_REQUESTS') or '8')
    os.environ.setdefault('ODDS_FEED_RAPIDAPI_PER_RUN_MAX', os.getenv('SECONDARY_ODDS_RESCUE_ODDSFEED_MAX_REQUESTS') or '3')
    os.environ.setdefault('SPORTSBOOK_API_RAPIDAPI_PER_RUN_MAX', os.getenv('SECONDARY_ODDS_RESCUE_SPORTSBOOK_MAX_REQUESTS') or '2')
    os.environ.setdefault('ODDS_API1_RAPIDAPI_PER_RUN_MAX', os.getenv('SECONDARY_ODDS_RESCUE_ODDSAPI1_MAX_REQUESTS') or '2')
    os.environ.setdefault('SPORTAPI7_RAPIDAPI_ODDS_PER_RUN_MAX', os.getenv('SECONDARY_ODDS_RESCUE_SPORTAPI7_MAX_REQUESTS') or '1')
    os.environ.setdefault('FREE_FOOTBALL_RAPIDAPI_ODDS_PER_RUN_MAX', os.getenv('SECONDARY_ODDS_RESCUE_FREE_FOOTBALL_MAX_REQUESTS') or '1')
    bridge = RapidApiOddsBridgeProvider(runner.settings)
    return await bridge.fetch_offers(matches)


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    if not _truthy(os.getenv('SECONDARY_ODDS_RESCUE_ENABLED'), True):
        return {'status': 'disabled'}
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}
    if getattr(PredictionRunner, '_harizon_secondary_odds_rescue_patch', False):
        _INSTALLED = True
        return {'status': 'already_patched'}

    original_fetch_provider = PredictionRunner._fetch_provider

    async def _fetch_provider_patched(self, provider, method_name, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = await original_fetch_provider(self, provider, method_name, *args, **kwargs)
        if str(method_name) != 'fetch_offers':
            return result
        provider_name = _provider_name(self, provider)
        base_offers, base_stats, base_preview = result if isinstance(result, tuple) and len(result) == 3 else ({}, {}, {})
        trigger, trigger_reason = _should_trigger(provider_name, base_offers)
        if not trigger:
            # Keep the install report, but record the latest non-trigger reason for diagnostics.
            _write_report({
                'created_at_utc': datetime.now(UTC).isoformat(),
                'installed': True,
                'enabled': True,
                'executed': False,
                'provider_name': provider_name,
                'method_name': str(method_name),
                'base_offers': _count_offers(base_offers),
                'trigger_reason': trigger_reason,
                'trigger_mode': str(os.getenv('SECONDARY_ODDS_RESCUE_TRIGGER_PROVIDER') or os.getenv('SECONDARY_ODDS_RESCUE_TRIGGER') or 'primary_odds_non_empty'),
            })
            return result
        if getattr(self, '_secondary_odds_rescue_done', False):
            return result
        lock = getattr(self, '_secondary_odds_rescue_lock', None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(self, '_secondary_odds_rescue_lock', lock)
        async with lock:
            if getattr(self, '_secondary_odds_rescue_done', False):
                return result
            setattr(self, '_secondary_odds_rescue_done', True)
            matches = _select_matches(args[0] if args else [])
            payload: dict[str, Any] = {
                'created_at_utc': datetime.now(UTC).isoformat(),
                'installed': True,
                'enabled': True,
                'executed': True,
                'trigger_provider': provider_name,
                'trigger_reason': trigger_reason,
                'base_offers': _count_offers(base_offers),
                'target_matches': len(matches),
                'target_match_sample': [
                    {
                        'match_key': getattr(m, 'match_key', ''),
                        'league': getattr(m, 'league_name', ''),
                        'home': getattr(m, 'home_team', ''),
                        'away': getattr(m, 'away_team', ''),
                        'commence_time': getattr(getattr(m, 'commence_time', None), 'isoformat', lambda: '')(),
                    }
                    for m in matches[:12]
                ],
            }
            if not matches:
                payload.update({'skipped': True, 'reason': 'no_near_window_soccer_matches'})
                _write_report(payload)
                return result
            try:
                extra_offers, extra_stats, extra_preview = await _fetch_rapidapi_secondary(self, matches)
            except Exception as exc:
                extra_offers, extra_stats, extra_preview = {}, {'enabled': True, 'runtime_error': f'{type(exc).__name__}: {exc}', 'requests': 0, 'offers_parsed': 0}, {}
            merged_offers = _merge_offer_maps(base_offers, extra_offers)
            extra_offer_count = _count_offers(extra_offers)
            base_stats = dict(base_stats or {})
            base_preview = dict(base_preview or {})
            base_stats['secondary_odds_rescue'] = extra_stats
            base_stats['secondary_odds_rescue_requests'] = _to_int(extra_stats.get('requests')) if isinstance(extra_stats, dict) else 0
            base_stats['secondary_odds_rescue_offers'] = extra_offer_count
            base_stats['secondary_odds_rescue_matches'] = len(extra_offers) if isinstance(extra_offers, dict) else 0
            base_stats['offers'] = _to_int(base_stats.get('offers')) + extra_offer_count
            base_stats['offers_parsed'] = _to_int(base_stats.get('offers_parsed')) + extra_offer_count
            base_stats['matched'] = _to_int(base_stats.get('matched')) + (len(extra_offers) if isinstance(extra_offers, dict) else 0)
            base_preview['secondary_odds_rescue'] = extra_preview
            payload.update({
                'skipped': False,
                'requests': base_stats['secondary_odds_rescue_requests'],
                'response_errors': _to_int(extra_stats.get('response_errors')) if isinstance(extra_stats, dict) else 0,
                'offers_added': extra_offer_count,
                'matches_with_offers_added': len(extra_offers) if isinstance(extra_offers, dict) else 0,
                'source_counts': _source_counts(extra_offers),
                'providers': extra_stats.get('providers') if isinstance(extra_stats, dict) else {},
                'last_error': extra_stats.get('last_error') if isinstance(extra_stats, dict) else None,
                'last_url': extra_stats.get('last_url') if isinstance(extra_stats, dict) else None,
                'http_statuses': extra_stats.get('http_statuses') if isinstance(extra_stats, dict) else [],
                'sample_offers': _sample_offers(extra_offers),
            })
            _write_report(payload)
            return merged_offers, base_stats, base_preview

    PredictionRunner._fetch_provider = _fetch_provider_patched
    PredictionRunner._harizon_secondary_odds_rescue_patch = True
    _INSTALLED = True
    _write_report({
        'created_at_utc': datetime.now(UTC).isoformat(),
        'installed': True,
        'enabled': True,
        'executed': False,
        'trigger_mode': os.getenv('SECONDARY_ODDS_RESCUE_TRIGGER_PROVIDER') or os.getenv('SECONDARY_ODDS_RESCUE_TRIGGER') or 'primary_odds_non_empty',
    })
    return {'status': 'installed', 'trigger_mode': os.getenv('SECONDARY_ODDS_RESCUE_TRIGGER_PROVIDER') or os.getenv('SECONDARY_ODDS_RESCUE_TRIGGER') or 'primary_odds_non_empty'}
