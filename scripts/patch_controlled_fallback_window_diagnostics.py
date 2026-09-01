from __future__ import annotations

"""Add explicit line-recheck/window diagnostics and safe last-chance handling.

This keeps the lifecycle safety rule: if there is another regular run before
kickoff and no previous line recheck, do not publish. It only records details and
allows the candidate to be treated as final-check eligible when there is no next
regular run before kickoff.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-controlled-fallback-window-diagnostics.json')
_INSTALLED = False


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    except Exception:
        pass


def install(guarded_module: Any | None = None) -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    if guarded_module is None:
        try:
            import scripts.publish_controlled_fallback_guarded_v18 as guarded_module  # type: ignore[no-redef]
        except Exception as exc:
            _write({'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}', 'created_at_utc': datetime.now(timezone.utc).isoformat()})
            return {'status': 'import_error'}
    fn = getattr(guarded_module, '_final_cron_recheck_reasons', None)
    if not callable(fn) or getattr(guarded_module, '_harizon_window_diagnostics_patch', False):
        return {'status': 'missing_or_already_patched'}

    def wrapped(candidate: dict[str, Any]) -> list[str]:
        reasons = list(fn(candidate) or [])
        event = None
        events = getattr(guarded_module, '_GUARD_EVENTS', [])
        if isinstance(events, list) and events:
            event = events[-1]
        payload = {
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'status': 'blocked' if reasons else 'passed_or_not_required',
            'match_key': candidate.get('match_key'),
            'home_team': candidate.get('home_team'),
            'away_team': candidate.get('away_team'),
            'family': candidate.get('family'),
            'selection': candidate.get('selection'),
            'point': candidate.get('point'),
            'reasons': reasons,
            'guard_event': event if isinstance(event, dict) else {},
            'safety_note': 'next-run candidates are stored/awaiting; no publication without prior recheck or final no-next-run window',
        }
        _write(payload)
        return reasons

    guarded_module._final_cron_recheck_reasons = wrapped
    guarded_module._harizon_window_diagnostics_patch = True
    _INSTALLED = True
    _write({'created_at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'installed'})
    return {'status': 'installed'}


if __name__ == '__main__':
    print(json.dumps(install(), ensure_ascii=False, indent=2))
