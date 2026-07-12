from __future__ import annotations

from pathlib import Path
from typing import Any


def _fresh_bankroll(base: Any) -> dict[str, Any]:
    for path in (
        '.data/exports/latest-bankroll-report-block.json',
        'artifacts/run-bot/exports/latest-bankroll-report-block.json',
        '.data/exports/latest-bankroll.json',
        'artifacts/run-bot/exports/latest-bankroll.json',
    ):
        payload = base.load_json(path, {})
        if not isinstance(payload, dict) or not payload:
            continue
        bankroll = payload.get('bankroll') if isinstance(payload.get('bankroll'), dict) else payload
        current = base.as_float(bankroll.get('current_balance'), 0.0)
        if current > 0:
            out = dict(bankroll)
            out.setdefault('starting_balance', bankroll.get('starting_balance') or 1000.0)
            out.setdefault('open_exposure', bankroll.get('open_exposure') or 0.0)
            out['bankroll_source_file'] = path
            return out
    return {}


def install(base: Any) -> None:
    old = getattr(base, 'load_json', None)
    if not callable(old) or getattr(base, '_current_bankroll_source_installed', False):
        return

    def wrapped(path: str | Path, default: Any) -> Any:
        payload = old(path, default)
        path_text = str(path)
        if path_text.endswith('.logs/debug-last-run.json') and isinstance(payload, dict):
            fresh = _fresh_bankroll(base)
            if fresh:
                clone = dict(payload)
                clone['bankroll'] = fresh
                clone.setdefault('diagnostics', {})
                if isinstance(clone['diagnostics'], dict):
                    clone['diagnostics']['current_bankroll_source_patch'] = {
                        'enabled': True,
                        'source_file': fresh.get('bankroll_source_file'),
                        'current_balance': fresh.get('current_balance'),
                        'open_exposure': fresh.get('open_exposure'),
                    }
                return clone
        return payload

    base.load_json = wrapped
    base._current_bankroll_source_installed = True
