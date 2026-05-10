from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / '.data' / 'exports' / 'latest-secondary-odds-rescue.json'
_INSTALLED = False


def _i(v: Any, d: int = 0) -> int:
    try:
        if v in (None, ''):
            return d
        return int(float(str(v)))
    except Exception:
        return d


def _b(v: Any, d: bool = False) -> bool:
    s = str(v if v is not None else '').strip().lower()
    return d if not s else s in {'1', 'true', 'yes', 'on', 'force'}


def _count(m: Any) -> int:
    return sum(len(v) for v in m.values() if isinstance(v, list)) if isinstance(m, dict) else 0


def _sources(m: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(m, dict):
        return out
    for offers in m.values():
        if not isinstance(offers, list):
            continue
        for offer in offers:
            src = str(getattr(offer, 'source', '') or (offer.get('source') if isinstance(offer, dict) else '') or 'unknown')
            out[src] = out.get(src, 0) + 1
    return out


def _merge(a: Any, b: Any) -> dict[str, list[Any]]:
    merged: dict[str, list[Any]] = defaultdict(list)
    for data in (a, b):
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list):
                    merged[str(k)].extend(v)
    return {k: v for k, v in merged.items() if v}


def _write(p: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(p, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _pick(matches: Any) -> list[Any]:
    if not isinstance(matches, list):
        return []
    now = datetime.now(UTC)
    limit = max(1, _i(os.getenv('SECONDARY_ODDS_RESCUE_MATCH_LIMIT') or os.getenv('RAPIDAPI_ODDS_MATCH_LIMIT'), 18))
    hours_max = max(1, _i(os.getenv('SECONDARY_ODDS_RESCUE_WINDOW_HOURS'), 8))
    rows: list[tuple[float, Any]] = []
    for m in matches:
        if str(getattr(m, 'sport_key', '')).lower() != 'soccer':
            continue
        try:
            h = (getattr(m, 'commence_time').astimezone(UTC) - now).total_seconds() / 3600.0
        except Exception:
            continue
        if -0.25 <= h <= hours_max:
            rows.append((abs(h), m))
    rows.sort(key=lambda x: (x[0], getattr(x[1], 'league_name', ''), getattr(x[1], 'home_team', '')))
    return [m for _, m in rows[:limit]]


def _sample(m: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(m, dict):
        return rows
    for key, offers in m.items():
        if not isinstance(offers, list):
            continue
        for offer in offers:
            try:
                row = asdict(offer)
            except Exception:
                row = dict(offer) if isinstance(offer, dict) else {}
            row['match_key'] = key
            rows.append(row)
            if len(rows) >= 5:
                return rows
    return rows


async def _bridge(settings: Any, matches: list[Any]):
    try:
        from app.providers import rapidapi_odds_bridge_schema_patch
        rapidapi_odds_bridge_schema_patch.install()
    except Exception:
        pass
    from app.providers.rapidapi_odds_bridge import RapidApiOddsBridgeProvider
    os.environ.setdefault('ENABLE_RAPIDAPI_ODDS_BRIDGE', 'true')
    os.environ.setdefault('RAPIDAPI_ODDS_MATCH_LIMIT', str(max(1, len(matches))))
    os.environ.setdefault('RAPIDAPI_ODDS_MAX_HTTP_REQUESTS_PER_RUN', os.getenv('SECONDARY_ODDS_RESCUE_MAX_REQUESTS') or '8')
    os.environ.setdefault('ODDS_FEED_RAPIDAPI_PER_RUN_MAX', os.getenv('SECONDARY_ODDS_RESCUE_ODDSFEED_MAX_REQUESTS') or '4')
    bridge = RapidApiOddsBridgeProvider(settings)
    if _b(os.getenv('SECONDARY_ODDS_RESCUE_FORCE_RAPIDAPI_REFRESH'), True):
        try:
            bridge._cache_path().unlink(missing_ok=True)
        except Exception:
            pass
    return await bridge.fetch_offers(matches)


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    if not _b(os.getenv('SECONDARY_ODDS_RESCUE_ENABLED'), True):
        return {'status': 'disabled'}
    try:
        from app.providers.odds_api_io import OddsApiIoProvider
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}
    if getattr(OddsApiIoProvider.fetch_offers, '_harizon_direct_secondary_wrapper', False):
        _INSTALLED = True
        return {'status': 'already_wrapped'}
    original = OddsApiIoProvider.fetch_offers

    async def wrapped(self, matches):
        base_offers, base_stats, base_preview = await original(self, matches)
        base_n = _count(base_offers)
        min_n = max(1, _i(os.getenv('SECONDARY_ODDS_RESCUE_MIN_PRIMARY_OFFERS'), 80))
        report = {'created_at_utc': datetime.now(UTC).isoformat(), 'direct_odds_api_io_wrapper': True, 'base_offers': base_n, 'min_primary_offers': min_n, 'base_source_counts': _sources(base_offers)}
        if base_n < min_n:
            report.update({'executed': False, 'trigger_reason': f'base_offers_below_min:{base_n}/{min_n}'})
            _write(report)
            return base_offers, base_stats, base_preview
        target = _pick(matches)
        report.update({'executed': True, 'target_matches': len(target), 'trigger_reason': f'odds_api_io:{base_n}>={min_n}'})
        if not target:
            report.update({'skipped': True, 'reason': 'no_target_matches'})
            _write(report)
            return base_offers, base_stats, base_preview
        try:
            extra, st, pv = await _bridge(self.settings, target)
        except Exception as exc:
            extra, st, pv = {}, {'runtime_error': f'{type(exc).__name__}: {exc}', 'requests': 0, 'offers_parsed': 0}, {}
        extra_n = _count(extra)
        merged = _merge(base_offers, extra)
        stats = dict(base_stats or {})
        stats['secondary_odds_rescue'] = st
        stats['secondary_odds_rescue_requests'] = _i(st.get('requests')) if isinstance(st, dict) else 0
        stats['secondary_odds_rescue_offers'] = extra_n
        stats['secondary_odds_rescue_matches'] = len(extra) if isinstance(extra, dict) else 0
        stats['offers_parsed'] = _i(stats.get('offers_parsed')) + extra_n
        preview = dict(base_preview or {})
        preview['secondary_odds_rescue'] = pv
        report.update({'requests': stats['secondary_odds_rescue_requests'], 'events_fetched': _i(st.get('events_fetched')) if isinstance(st, dict) else 0, 'events_matched': _i(st.get('events_matched')) if isinstance(st, dict) else 0, 'offers_added': extra_n, 'matches_with_offers_added': len(extra) if isinstance(extra, dict) else 0, 'source_counts': _sources(extra), 'providers': st.get('providers') if isinstance(st, dict) else {}, 'last_error': st.get('last_error') if isinstance(st, dict) else None, 'last_url': st.get('last_url') if isinstance(st, dict) else None, 'http_statuses': st.get('http_statuses') if isinstance(st, dict) else [], 'sample_offers': _sample(extra)})
        _write(report)
        return merged, stats, preview

    wrapped._harizon_direct_secondary_wrapper = True
    OddsApiIoProvider.fetch_offers = wrapped
    _INSTALLED = True
    _write({'created_at_utc': datetime.now(UTC).isoformat(), 'direct_odds_api_io_wrapper': True, 'executed': False, 'status': 'installed'})
    return {'status': 'installed'}
