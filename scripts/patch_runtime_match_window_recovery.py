from __future__ import annotations

"""Recover thin/empty runtime match windows without weakening publication guards.

If the normal runtime filter returns zero or only a very small slice while day
inventory says many near-window matches are ready, provider fetching sees too few
matches and CandidateFactory/fallback can only evaluate stale or one-off rows.
This patch widens only the *data collection / diagnostic* window for the run.
Final publication remains protected by controlled fallback publish window, final
cron recheck, line movement, price integrity, quality and duplicate guards.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-runtime-match-window-recovery.json')
_INV = Path('.data/exports/latest-day-inventory-coverage-truth.json')
_INSTALLED = False


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw in (None, ''):
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value))) if value not in (None, '') else default
    except Exception:
        return default


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    except Exception:
        pass


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def _ensure_utc(dt: Any) -> datetime | None:
    try:
        if hasattr(dt, 'tzinfo'):
            return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc)
        text = str(dt or '').strip()
        if not text:
            return None
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except Exception:
        return None


def _match_key(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get('match_key') or obj.get('canonical_match_id') or '').strip().lower()
    return str(getattr(obj, 'match_key', '') or '').strip().lower()


def _row_ready(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get('ready') is True or row.get('model_ready') is True or row.get('ready_for_model') is True:
        return True
    missing = row.get('missing') if isinstance(row.get('missing'), list) else []
    if missing:
        return False
    odds = _as_int(row.get('odds_sources_count') or row.get('line_sources_count') or row.get('price_confirmations'))
    ctx = _as_int(row.get('context_sources_count') or row.get('confirmation_sources_count'))
    return odds > 0 and ctx > 0


def _inventory_ready_keys(now_utc: datetime, hours: int) -> set[str]:
    payload = _load_json(_INV, {})
    rows = payload.get('rows') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return set()
    horizon = now_utc + timedelta(hours=max(1, hours))
    out: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not _row_ready(row):
            continue
        kickoff = _ensure_utc(row.get('kickoff_utc') or row.get('commence_time') or row.get('start_time') or row.get('event_date'))
        if kickoff is not None and not (now_utc < kickoff <= horizon):
            continue
        key = _match_key(row)
        if key:
            out.add(key)
    return out


def install(runner_module: Any | None = None) -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    if not _truthy('HARIZON_RUNTIME_MATCH_WINDOW_RECOVERY_ENABLED', True):
        return {'status': 'disabled'}
    if runner_module is None:
        import app.services.runner as runner_module  # type: ignore[no-redef]
    cls = getattr(runner_module, 'PredictionRunner', None)
    if cls is None or getattr(cls, '_harizon_runtime_match_window_recovery', False):
        return {'status': 'missing_or_already_patched'}
    original = cls._filter_matches

    def wrapped(self: Any, matches: list[Any], now_utc: datetime):
        filtered, info = original(self, matches, now_utc)
        max_hours = max(1, _as_int(os.getenv('HARIZON_RUNTIME_MATCH_RECOVERY_WINDOW_HOURS'), 12))
        max_matches = max(1, _as_int(os.getenv('HARIZON_RUNTIME_MATCH_RECOVERY_MAX_MATCHES'), 40))
        min_lead = max(0, _as_int(os.getenv('HARIZON_RUNTIME_MATCH_RECOVERY_MIN_LEAD_MINUTES'), 0))
        thin_min = max(0, _as_int(os.getenv('HARIZON_RUNTIME_MATCH_RECOVERY_MIN_FILTERED_MATCHES'), 18))
        inv_keys = _inventory_ready_keys(now_utc, max_hours) if _truthy('HARIZON_RUNTIME_MATCH_RECOVERY_USE_DAY_INVENTORY', True) else set()
        payload = {
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'status': 'not_needed',
            'input_matches': len(matches or []),
            'initial_filtered': len(filtered or []),
            'filtering': dict(info or {}),
            'inventory_ready_keys_near_window': len(inv_keys),
            'recovered_matches': 0,
            'augmentation_mode': 'none',
            'publication_contract_relaxed': False,
        }
        if not matches:
            _write(payload)
            return filtered, info

        need_recovery = not filtered
        thin_window = bool(filtered) and len(filtered) < thin_min and len(inv_keys) >= max(thin_min, len(filtered) + 8)
        if not need_recovery and not thin_window:
            _write(payload)
            return filtered, info

        horizon = now_utc + timedelta(hours=max_hours)
        earliest = now_utc + timedelta(minutes=min_lead)
        selected_by_key: dict[str, Any] = {_match_key(m): m for m in (filtered or []) if _match_key(m)}
        recovered: list[Any] = list(filtered or [])
        skipped_started = 0
        skipped_outside = 0
        skipped_invalid = 0
        added_from_inventory_ready = 0
        added_by_window = 0
        for match in matches:
            key = _match_key(match)
            if key and key in selected_by_key:
                continue
            kickoff = _ensure_utc(getattr(match, 'commence_time', None))
            if kickoff is None:
                skipped_invalid += 1
                continue
            if kickoff <= now_utc:
                skipped_started += 1
                continue
            if kickoff < earliest or kickoff > horizon:
                skipped_outside += 1
                continue
            if inv_keys and key in inv_keys:
                added_from_inventory_ready += 1
            elif need_recovery:
                added_by_window += 1
            elif thin_window:
                # Thin-window augmentation is intentionally stricter: prefer rows
                # proven ready in day inventory; do not blindly pull all events.
                continue
            recovered.append(match)
            if key:
                selected_by_key[key] = match
            if len(recovered) >= max_matches:
                break
        recovered.sort(key=lambda m: _ensure_utc(getattr(m, 'commence_time', None)) or horizon)
        recovered = recovered[:max_matches]
        if len(recovered) > len(filtered or []):
            new_info = dict(info or {})
            new_info.update({
                'runtime_match_window_recovery_applied': True,
                'runtime_match_window_recovery_reason': 'initial_filter_returned_zero' if need_recovery else 'thin_runtime_window_vs_day_inventory',
                'runtime_match_window_recovery_window_hours': max_hours,
                'runtime_match_window_recovery_min_lead_minutes': min_lead,
                'runtime_match_window_recovery_total_after': len(recovered),
                'runtime_match_window_recovery_added': len(recovered) - len(filtered or []),
                'total_after': len(recovered),
                'publication_contract_relaxed': False,
            })
            payload.update({
                'status': 'recovered' if need_recovery else 'augmented_thin_window',
                'recovered_matches': len(recovered) - len(filtered or []),
                'total_after': len(recovered),
                'window_hours': max_hours,
                'min_lead_minutes': min_lead,
                'thin_min_filtered_matches': thin_min,
                'augmentation_mode': 'day_inventory_ready' if thin_window else 'empty_window',
                'added_from_inventory_ready': added_from_inventory_ready,
                'added_by_window': added_by_window,
                'sample': [
                    {
                        'match_key': getattr(m, 'match_key', None),
                        'home_team': getattr(m, 'home_team', None),
                        'away_team': getattr(m, 'away_team', None),
                        'league_name': getattr(m, 'league_name', None),
                        'commence_time': (_ensure_utc(getattr(m, 'commence_time', None)) or now_utc).isoformat(),
                    }
                    for m in recovered[:12]
                ],
            })
            _write(payload)
            return recovered, new_info

        payload.update({'status': 'not_recovered' if need_recovery else 'thin_window_no_inventory_match', 'skipped_started': skipped_started, 'skipped_outside_recovery_window': skipped_outside, 'skipped_invalid': skipped_invalid})
        _write(payload)
        return filtered, info

    cls._filter_matches = wrapped
    cls._harizon_runtime_match_window_recovery = True
    _INSTALLED = True
    _write({'created_at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'installed', 'publication_contract_relaxed': False})
    return {'status': 'installed'}


if __name__ == '__main__':
    print(json.dumps(install(), ensure_ascii=False, indent=2))
