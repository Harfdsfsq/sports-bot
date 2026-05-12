from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / '.data' / 'exports' / 'latest-core-odds-merge-safety.json'
_INSTALLED = False


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _allowed_families() -> set[str]:
    raw = os.getenv('CORE_ODDS_MERGE_ALLOWED_FAMILIES', 'totals,spreads')
    return {x.strip() for x in raw.split(',') if x.strip()}


def _offer_key(offer: Any) -> tuple[Any, ...]:
    return (
        getattr(offer, 'source', ''),
        getattr(offer, 'bookmaker', ''),
        getattr(offer, 'family', ''),
        getattr(offer, 'selection', ''),
        getattr(offer, 'point', None),
        getattr(offer, 'team_side', None),
    )


def _safe_offers(offers: list[Any]) -> list[Any]:
    allowed = _allowed_families()
    min_price = _as_float(os.getenv('CORE_ODDS_MERGE_MIN_PRICE', '1.56'), 1.56)
    min_total_line = _as_float(os.getenv('CORE_ODDS_MERGE_MIN_TOTAL_LINE', '1.5'), 1.5)
    max_per_event = max(1, _as_int(os.getenv('CORE_ODDS_MERGE_MAX_OFFERS_PER_EVENT', '90'), 90))
    best: dict[tuple[Any, ...], Any] = {}
    for offer in offers or []:
        family = str(getattr(offer, 'family', '') or '')
        if allowed and family not in allowed:
            continue
        price = _as_float(getattr(offer, 'price', None), 0.0)
        if price < min_price or price > 80.0:
            continue
        if family == 'totals':
            point = getattr(offer, 'point', None)
            if point is None or _as_float(point, 0.0) < min_total_line:
                continue
        key = _offer_key(offer)
        prev = best.get(key)
        if prev is None or _as_float(getattr(offer, 'price', None), 0.0) > _as_float(getattr(prev, 'price', None), 0.0):
            best[key] = offer
    # Keep a compact useful set: best prices first, then stable market key.
    return sorted(best.values(), key=lambda o: (-_as_float(getattr(o, 'price', None), 0.0), str(_offer_key(o))))[:max_per_event]


def _install_parse_filter(module: Any, report: dict[str, Any]) -> None:
    original = getattr(module, 'parse_any', None)
    if not callable(original) or getattr(original, '_harizon_core_odds_safety', False):
        report['parse_filter'] = 'already_wrapped_or_missing'
        return

    def parse_any_safe(payload: Any, match: Any, source: str, event_id: str | None = None):
        raw = list(original(payload, match, source, event_id) or [])
        safe = _safe_offers(raw)
        try:
            stats = getattr(parse_any_safe, '_last_stats', [])
            stats.append({'source': source, 'event_id': event_id, 'raw': len(raw), 'safe': len(safe)})
            del stats[:-40]
            setattr(parse_any_safe, '_last_stats', stats)
        except Exception:
            pass
        return safe

    parse_any_safe._harizon_core_odds_safety = True  # type: ignore[attr-defined]
    module.parse_any = parse_any_safe
    report['parse_filter'] = 'wrapped'


def _install_score_event_match_compat(module: Any, report: dict[str, Any]) -> None:
    try:
        from app.utils import score_event_match as real_score_event_match
    except Exception as exc:
        report['score_event_match_compat'] = f'import_failed:{type(exc).__name__}'
        return
    current = getattr(module, 'score_event_match', None)
    if getattr(current, '_harizon_positional_compat', False):
        report['score_event_match_compat'] = 'already_wrapped'
        return

    def score_event_match_compat(*args: Any, **kwargs: Any):
        if args and len(args) >= 9:
            return real_score_event_match(
                sport=args[0],
                match_home=args[1],
                match_away=args[2],
                match_start=args[3],
                match_league=args[4],
                event_home=args[5],
                event_away=args[6],
                event_start=args[7],
                event_league=args[8],
                **kwargs,
            )
        return real_score_event_match(*args, **kwargs)

    score_event_match_compat._harizon_positional_compat = True  # type: ignore[attr-defined]
    module.score_event_match = score_event_match_compat
    report['score_event_match_compat'] = 'wrapped'


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    _INSTALLED = True
    # Protect SStats context phase: odds rescue is supplemental and must not spend
    # the entire SStats request pool before context extraction.
    os.environ['SSTATS_ODDS_RESCUE_LIMIT_PER_RUN'] = os.getenv('SSTATS_ODDS_RESCUE_LIMIT_PER_RUN_SAFE', '24')
    os.environ.setdefault('CORE_ODDS_PATCH_MATCH_LIMIT', '90')
    os.environ.setdefault('CORE_ODDS_MERGE_ALLOWED_FAMILIES', 'totals,spreads')
    os.environ.setdefault('CORE_ODDS_MERGE_MIN_PRICE', '1.56')
    os.environ.setdefault('CORE_ODDS_MERGE_MIN_TOTAL_LINE', '1.5')
    os.environ.setdefault('CORE_ODDS_MERGE_MAX_OFFERS_PER_EVENT', '90')
    report: dict[str, Any] = {'status': 'starting', 'created_at_utc': datetime.now(UTC).isoformat()}
    try:
        from app.services import sstats_bzzoiro_odds_merge_patch as module
        _install_parse_filter(module, report)
        _install_score_event_match_compat(module, report)
        report['status'] = 'installed'
    except Exception as exc:
        report['status'] = 'error'
        report['error'] = f'{type(exc).__name__}: {exc}'
    _write(report)
    return report
