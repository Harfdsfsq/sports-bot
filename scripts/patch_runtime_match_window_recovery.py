from __future__ import annotations

"""Recover no-match runtime windows without weakening publication guards.

When daily inventory still has ready matches but the runtime filter returns zero
matches (usually all near-window matches are inside the configured lead-time), the
main runner fetches no odds/context and controlled fallback has nothing current to
evaluate.  This patch only widens the *data collection / diagnostic* window for
that run.  Final publication remains protected by controlled fallback publish
window, final cron recheck, line movement, price integrity, quality and duplicate
guards.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-runtime-match-window-recovery.json')
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
        payload = {
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'status': 'not_needed' if filtered else 'empty_after_filter',
            'input_matches': len(matches or []),
            'initial_filtered': len(filtered or []),
            'filtering': dict(info or {}),
            'recovered_matches': 0,
            'publication_contract_relaxed': False,
        }
        if filtered or not matches:
            _write(payload)
            return filtered, info

        max_hours = max(1, _as_int(os.getenv('HARIZON_RUNTIME_MATCH_RECOVERY_WINDOW_HOURS'), 12))
        max_matches = max(1, _as_int(os.getenv('HARIZON_RUNTIME_MATCH_RECOVERY_MAX_MATCHES'), 40))
        min_lead = max(0, _as_int(os.getenv('HARIZON_RUNTIME_MATCH_RECOVERY_MIN_LEAD_MINUTES'), 0))
        horizon = now_utc + timedelta(hours=max_hours)
        earliest = now_utc + timedelta(minutes=min_lead)
        recovered: list[Any] = []
        skipped_started = 0
        skipped_outside = 0
        skipped_invalid = 0
        for match in matches:
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
            recovered.append(match)
        recovered.sort(key=lambda m: _ensure_utc(getattr(m, 'commence_time', None)) or horizon)
        recovered = recovered[:max_matches]
        if recovered:
            new_info = dict(info or {})
            new_info.update({
                'runtime_match_window_recovery_applied': True,
                'runtime_match_window_recovery_reason': 'initial_filter_returned_zero',
                'runtime_match_window_recovery_window_hours': max_hours,
                'runtime_match_window_recovery_min_lead_minutes': min_lead,
                'runtime_match_window_recovery_total_after': len(recovered),
                'total_after': len(recovered),
                'publication_contract_relaxed': False,
            })
            payload.update({
                'status': 'recovered',
                'recovered_matches': len(recovered),
                'window_hours': max_hours,
                'min_lead_minutes': min_lead,
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

        payload.update({'status': 'not_recovered', 'skipped_started': skipped_started, 'skipped_outside_recovery_window': skipped_outside, 'skipped_invalid': skipped_invalid})
        _write(payload)
        return filtered, info

    cls._filter_matches = wrapped
    cls._harizon_runtime_match_window_recovery = True
    _INSTALLED = True
    _write({'created_at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'installed', 'publication_contract_relaxed': False})
    return {'status': 'installed'}


if __name__ == '__main__':
    print(json.dumps(install(), ensure_ascii=False, indent=2))
