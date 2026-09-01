from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PATHS = (
    Path('.data/exports/latest-reference-price-snapshot.json'),
    Path('.data/exports/latest-fonbet-reference-prices.json'),
    Path('artifacts/run-bot/latest-reference-price-snapshot.json'),
    Path('artifacts/run-bot/latest-fonbet-reference-prices.json'),
)


def _load(path: Path) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return None


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ('rows', 'offers', 'prices', 'items', 'data'):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def _date(value: Any) -> str:
    text = str(value or '').strip()
    m = re.search(r'(20\d{2}-\d{2}-\d{2})', text)
    if m:
        return m.group(1)
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc).date().isoformat()
    except Exception:
        return ''


def _point(value: Any) -> str:
    try:
        f = float(str(value).replace(',', '.'))
        return str(int(f)) if f.is_integer() else f'{f:g}'
    except Exception:
        return ''


def _side(row: dict[str, Any]) -> str:
    text = _norm(' '.join(str(row.get(k) or '') for k in ('selection', 'selection_key', 'name', 'outcome', 'label')))
    if 'under' in text or 'меньше' in text or text in {'tm', 'тм'}:
        return 'under'
    if 'over' in text or 'больше' in text or text in {'tb', 'тб'}:
        return 'over'
    return text


def _sig(row: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    home = _norm(row.get('home_team') or row.get('home') or row.get('home_name'))
    away = _norm(row.get('away_team') or row.get('away') or row.get('away_name'))
    day = _date(row.get('commence_time') or row.get('kickoff_utc') or row.get('start_time') or row.get('date') or row.get('match_key'))
    family = _norm(row.get('family') or row.get('market_family') or row.get('market'))
    if 'total' in family or 'тотал' in family:
        family = 'totals'
    return home, away, day, family, _side(row), _point(row.get('point') or row.get('line') or row.get('handicap'))


def _price(row: dict[str, Any]) -> float:
    for key in ('price', 'odds', 'decimal_odds', 'selected_odds'):
        try:
            value = row.get(key)
            if value not in (None, ''):
                f = float(str(value).replace(',', '.'))
                if f > 1.0:
                    return f
        except Exception:
            pass
    return 0.0


def reference_prices(candidate: dict[str, Any]) -> list[tuple[float, str]]:
    target = _sig(candidate)
    out: list[tuple[float, str]] = []
    for path in PATHS:
        for row in _rows(_load(path)):
            if _sig(row) == target:
                p = _price(row)
                if p > 1.0:
                    out.append((p, str(row.get('bookmaker') or row.get('source') or path.name)))
    return out


def install(base: Any) -> None:
    if getattr(base, '_harizon_reference_price_guard_patch', False):
        return
    original = getattr(base, 'hard_reject_reasons', None)
    if not callable(original):
        return

    def patched(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
        reasons = list(original(candidate, metrics, sent_index) or [])
        refs = reference_prices(candidate)
        required = str(os.getenv('REFERENCE_PRICE_REQUIRED') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
        if not refs:
            if required:
                reasons.append('reference_price_missing')
            return reasons
        selected = float(metrics.get('odds') or candidate.get('odds') or 0.0)
        best = max(price for price, _ in refs)
        if selected <= 1.0:
            return reasons
        max_dev = float(os.getenv('REFERENCE_PRICE_MAX_SELECTED_DEV_PCT') or '8.0')
        if (selected - best) / best * 100.0 > max_dev:
            reasons.append(f'reference_price_below_selected:{best:.2f}/{selected:.2f}')
        metrics['reference_price_guard'] = {'references': refs[:5], 'best_reference_price': round(best, 4), 'selected_price': round(selected, 4)}
        return reasons

    base.hard_reject_reasons = patched
    base._harizon_reference_price_guard_patch = True


__all__ = ['install']
