from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / '.data' / 'exports' / 'latest-final-candidate-runtime-chain.json'


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def install() -> dict[str, Any]:
    payload: dict[str, Any] = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'status': 'starting',
    }
    try:
        from app.services.model import CandidateFactory

        # Install line-universe wrapper around the current build_candidates.
        try:
            import app.services.core_line_bookmaker_universe_patch as line_patch
            setattr(line_patch, '_INSTALLED', False)
            if hasattr(CandidateFactory, '_harizon_core_line_bookmaker_universe'):
                try:
                    delattr(CandidateFactory, '_harizon_core_line_bookmaker_universe')
                except Exception:
                    pass
            payload['line_universe'] = line_patch.install()
        except Exception as exc:
            payload['line_universe'] = f'failed:{type(exc).__name__}: {exc}'

        # Install market-sanity wrapper after line universe, before final value wrapper.
        try:
            import app.services.model_input_market_sanity_patch as sanity_patch
            setattr(sanity_patch, '_INSTALLED', False)
            current = getattr(CandidateFactory, 'build_candidates', None)
            if hasattr(current, '_harizon_model_input_market_sanity'):
                try:
                    delattr(current, '_harizon_model_input_market_sanity')
                except Exception:
                    pass
            payload['model_input_market_sanity'] = sanity_patch.install()
        except Exception as exc:
            payload['model_input_market_sanity'] = f'failed:{type(exc).__name__}: {exc}'

        # Final value wrapper outermost.
        try:
            import app.services.candidate_value_runtime_patch as value_patch
            setattr(value_patch, '_INSTALLED', False)
            try:
                setattr(CandidateFactory, '_harizon_candidate_value_patch', False)
            except Exception:
                pass
            payload['candidate_value'] = value_patch.install()
        except Exception as exc:
            payload['candidate_value'] = f'failed:{type(exc).__name__}: {exc}'

        current_after = getattr(CandidateFactory, 'build_candidates', None)
        payload['build_after'] = getattr(current_after, '__name__', str(current_after))
        payload['status'] = 'installed'
    except Exception as exc:
        payload['status'] = 'error'
        payload['error'] = f'{type(exc).__name__}: {exc}'
    _write(payload)
    return payload
