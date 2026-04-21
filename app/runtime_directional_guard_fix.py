from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
_PATCH_APPLIED = False


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(value)
    except Exception:
        return default


def _parse_dt(value: Any):
    try:
        text = str(value or '').strip()
        if not text:
            return None
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _normalize_team(text: str) -> str:
    value = str(text or '').strip().lower()
    value = value.replace('ё', 'е')
    value = re.sub(r'\([^)]*\)', ' ', value)
    value = re.sub(r'[^a-zа-я0-9 ]+', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def _selected_side(item: Any) -> str | None:
    family = str(getattr(item, 'family', '') or '').lower()
    if family not in {'h2h', 'dnb', 'spreads'}:
        return None
    side = str(getattr(item, 'team_side', '') or '').lower().strip()
    if side in {'home', 'away'}:
        return side

    selection = str(getattr(item, 'selection', '') or '').strip()
    selection_norm = _normalize_team(selection)
    home_norm = _normalize_team(str(getattr(item, 'home_team', '') or ''))
    away_norm = _normalize_team(str(getattr(item, 'away_team', '') or ''))

    if selection_norm and home_norm and (selection_norm == home_norm or selection_norm.startswith(home_norm) or home_norm in selection_norm):
        return 'home'
    if selection_norm and away_norm and (selection_norm == away_norm or selection_norm.startswith(away_norm) or away_norm in selection_norm):
        return 'away'

    low = selection.lower()
    if low in {'п1', '1', 'home'}:
        return 'home'
    if low in {'п2', '2', 'away'}:
        return 'away'
    return None


def _xg_conflict(item: Any) -> bool:
    family = str(getattr(item, 'family', '') or '').lower()
    if family not in {'h2h', 'dnb', 'spreads'}:
        return False
    side = _selected_side(item)
    if side not in {'home', 'away'}:
        return False
    eh = getattr(item, 'expected_home', None)
    ea = getattr(item, 'expected_away', None)
    if eh is None or ea is None:
        return False
    diff = float(eh) - float(ea)
    min_diff = 0.28 if family == 'dnb' else 0.32
    single_source = int(getattr(item, 'sources_count', 0) or 0) <= 1
    high_odds = float(getattr(item, 'odds', 0.0) or 0.0) >= 3.40
    shrink_pp = abs(_to_float(getattr(item, 'model_probability', 0.0)) - _to_float(getattr(item, 'adjusted_probability', 0.0))) * 100.0
    heavy_shrink = shrink_pp >= 7.0
    risky = single_source or high_odds or heavy_shrink
    if not risky:
        return False
    return (side == 'away' and diff >= min_diff) or (side == 'home' and diff <= -min_diff)


def _prune_day_pool_module() -> None:
    try:
        import app.runtime_bot_fix as rbf
    except Exception:
        return
    if getattr(rbf, '_directional_prune_patch_applied', False):
        return
    original_load = getattr(rbf, '_load_day_predictions', None)
    original_merge = getattr(rbf, '_merge_day_predictions', None)
    if not callable(original_load):
        return

    def _prune_payload(payload: dict[str, Any]) -> dict[str, Any]:
        items = [dict(item) for item in (payload.get('items') or []) if isinstance(item, dict)]
        now = datetime.now(UTC)
        keep = []
        for item in items:
            dt = _parse_dt(item.get('commence_time') or item.get('kickoff_at') or item.get('kickoff_time'))
            if dt is not None and dt <= now - timedelta(minutes=5):
                continue
            keep.append(item)
        payload = dict(payload)
        payload['items'] = keep
        payload['count'] = len(keep)
        return payload

    def patched_load(settings):
        payload = original_load(settings)
        return _prune_payload(payload)

    def patched_merge(settings, rows, *, source):
        current_before = original_load(settings)
        previous_items = [dict(item) for item in (current_before.get('items') or []) if isinstance(item, dict)]
        payload = original_merge(settings, rows, source=source) if callable(original_merge) else original_load(settings)
        payload = _prune_payload(payload)
        try:
            saver = getattr(rbf, '_save_day_predictions', None)
            if callable(saver):
                payload = saver(settings, payload, previous_items=previous_items)
        except Exception:
            pass
        return payload

    rbf._load_day_predictions = patched_load
    if callable(original_merge):
        rbf._merge_day_predictions = patched_merge
    rbf._directional_prune_patch_applied = True


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    try:
        from app.services.model import CandidateFactory
        from app.services.telegram import TelegramPublisher
    except Exception:
        return

    if not getattr(CandidateFactory, '_runtime_directional_guard_fix_applied', False):
        original_filter = CandidateFactory._filter_and_rank

        def patched_filter(self, candidates, rejections):
            result = original_filter(self, candidates, rejections)
            filtered = []
            for item in result:
                if _xg_conflict(item):
                    rejections['postfilter_directional_xg_conflict_guard'] += 1
                    continue
                filtered.append(item)
            return filtered

        CandidateFactory._filter_and_rank = patched_filter
        CandidateFactory._runtime_directional_guard_fix_applied = True

    if not getattr(TelegramPublisher, '_runtime_directional_guard_fix_applied', False):
        original_expl = TelegramPublisher._build_explanation

        def patched_expl(self, bet, selection_text):
            text = original_expl(self, bet, selection_text)
            if _xg_conflict(bet):
                if 'что не противоречит выбранному сценарию.' in text:
                    text = text.replace(
                        'что не противоречит выбранному сценарию.',
                        'но по xG преимущество на другой стороне, поэтому ставка опирается не на профиль моментов, а на остальные сигналы модели.'
                    )
                else:
                    text += '\n\nПо xG матч скорее за противоположную сторону, поэтому это более рискованный сигнал и он держится на остальных факторах модели.'
            return text

        TelegramPublisher._build_explanation = patched_expl
        TelegramPublisher._runtime_directional_guard_fix_applied = True

    _prune_day_pool_module()
    _PATCH_APPLIED = True


_apply()
