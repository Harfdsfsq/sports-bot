from __future__ import annotations

"""Final runtime guard for duplicate direct Bzzoiro context fetch.

Older runs disabled the direct Bzzoiro provider to avoid duplicate slow v1
prediction fetches.  That is now too aggressive: the windowed v2 enrichment and
context-gap finalizers are installed on the same ``fetch_context`` method, so a
blank no-op here can suppress the very Bzzoiro v2 pass that should create the
second core context/odds source.

The guard now defaults to pass-through.  It can still force a clean no-op for a
standalone emergency by setting ``BZZOIRO_DIRECT_CONTEXT_FETCH_ENABLED=false``
or ``BZZOIRO_DIRECT_CONTEXT_FETCH_MODE=noop``.
"""

import os
from typing import Any

_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _mode() -> str:
    raw = str(os.getenv('BZZOIRO_DIRECT_CONTEXT_FETCH_MODE') or '').strip().lower()
    if raw:
        return raw
    legacy = os.getenv('BZZOIRO_DIRECT_CONTEXT_FETCH_ENABLED')
    if legacy is not None and str(legacy).strip() != '':
        return 'pass' if _truthy(legacy, False) else 'noop'
    return 'pass'


def _stats(self: Any, matches: Any, *, passthrough_available: bool = False) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    target_matches = len(matches or []) if isinstance(matches, list) else 0
    stats = {
        'provider_version': getattr(self, 'VERSION', 'bzzoiro-direct-final-guard-v2'),
        'api_version': 'v2_events_context_gap',
        'enabled': bool(getattr(self, 'api_key', None)),
        'api_key_present': bool(getattr(self, 'api_key', None)),
        'direct_context_fetch_skipped': True,
        'skip_reason': 'forced_noop_by_BZZOIRO_DIRECT_CONTEXT_FETCH_MODE',
        'passthrough_available': passthrough_available,
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
        'target_matches': target_matches,
        'sample_predictions': [],
        'matched_examples': [],
    }
    return {}, stats, preview


def _patch_class(cls: type, label: str) -> str:
    if getattr(cls, '_harizon_bzzoiro_direct_final_guard_v2', False):
        return f'{label}:already_patched'
    original = getattr(cls, 'fetch_context', None)

    async def fetch_context_final_guard(self, matches):  # type: ignore[no-untyped-def]
        mode = _mode()
        if mode in {'noop', 'skip', 'disabled', 'false', '0', 'off'}:
            return _stats(self, matches, passthrough_available=callable(original))
        if callable(original):
            return await original(self, matches)
        return _stats(self, matches, passthrough_available=False)

    cls.fetch_context = fetch_context_final_guard
    cls._harizon_bzzoiro_direct_final_guard_v2 = True
    return f'{label}:patched_passthrough_default'


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
    return {'status': 'installed', 'patched': patched, 'mode': _mode(), 'passthrough_default': True}
