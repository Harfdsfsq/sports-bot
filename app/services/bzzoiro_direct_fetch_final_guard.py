from __future__ import annotations

"""Final runtime guard for duplicate direct Bzzoiro context fetch.

Bzzoiro predictions are already consumed through the SStats-integrated generic
layer. A later payload-mining wrapper can otherwise call the old direct
``app.providers.bzzoiro.BzzoiroContextProvider.fetch_context`` and overwrite the
run report with ``bzzoiro err=1 ctx=0`` after a timeout. This guard is installed
after payload mining and replaces the direct fetch with a clean no-op by default.

Set ``BZZOIRO_DIRECT_CONTEXT_FETCH_ENABLED=true`` only for standalone debugging.
"""

import os
from typing import Any

_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _stats(self: Any, matches: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    target_matches = len(matches or []) if isinstance(matches, list) else 0
    stats = {
        'provider_version': getattr(self, 'VERSION', 'bzzoiro-direct-final-guard'),
        'api_version': 'v2_predictions',
        'enabled': bool(getattr(self, 'api_key', None)),
        'api_key_present': bool(getattr(self, 'api_key', None)),
        'direct_context_fetch_skipped': True,
        'skip_reason': 'duplicate_direct_bzzoiro_fetch_disabled_after_payload_mining',
        'delegated_to_sstats_nested_bzzoiro': True,
        'requests': 0,
        'response_errors': 0,
        'retry_attempts': 0,
        'events_fetched': 0,
        'predictions_fetched': 0,
        'rows_fetched': 0,
        'rows_seen': 0,
        'contexts_built': 0,
        'matched_exact': 0,
        'matched_loose': 0,
        'matched_fuzzy': 0,
        'exact_matches': 0,
        'fuzzy_matches': 0,
        'target_matches': target_matches,
        'http_statuses': [],
        'payload_shapes': [],
        'last_error': None,
        'last_url': None,
        'budget_exhausted': False,
    }
    preview = {
        'direct_context_fetch_skipped': True,
        'delegated_to_sstats_nested_bzzoiro': True,
        'target_matches': target_matches,
        'sample_predictions': [],
        'matched_examples': [],
    }
    return {}, stats, preview


def _patch_class(cls: type, label: str) -> str:
    if getattr(cls, '_harizon_bzzoiro_direct_final_guard', False):
        return f'{label}:already_patched'
    original = getattr(cls, 'fetch_context', None)

    async def fetch_context_final_guard(self, matches):  # type: ignore[no-untyped-def]
        if _truthy(os.getenv('BZZOIRO_DIRECT_CONTEXT_FETCH_ENABLED'), False) and callable(original):
            return await original(self, matches)
        return _stats(self, matches)

    cls.fetch_context = fetch_context_final_guard
    cls._harizon_bzzoiro_direct_final_guard = True
    return f'{label}:patched'


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    patched: list[str] = []
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider as OldProvider
        patched.append(_patch_class(OldProvider, 'app.providers.bzzoiro'))
    except Exception as exc:
        patched.append(f'app.providers.bzzoiro:error:{type(exc).__name__}:{exc}')
    try:
        from app.providers.bzzoiro_predictions_v2 import BzzoiroContextProvider as V2Provider
        patched.append(_patch_class(V2Provider, 'app.providers.bzzoiro_predictions_v2'))
    except Exception as exc:
        patched.append(f'app.providers.bzzoiro_predictions_v2:error:{type(exc).__name__}:{exc}')
    _INSTALLED = True
    return {'status': 'installed', 'patched': patched, 'direct_enabled': _truthy(os.getenv('BZZOIRO_DIRECT_CONTEXT_FETCH_ENABLED'), False)}
