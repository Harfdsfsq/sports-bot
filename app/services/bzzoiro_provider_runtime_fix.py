from __future__ import annotations

import os
from typing import Any

_INSTALLED = False


def _ensure_stats(stats: Any) -> dict[str, Any]:
    return stats if isinstance(stats, dict) else {}


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def install() -> dict[str, Any]:
    """Apply compatibility fixes for Bzzoiro provider runtime.

    The runner imports Bzzoiro through ``app.providers.bzzoiro``. Recent runtime
    logs showed two separate Bzzoiro paths: the useful ``/api/predictions/`` path
    and an obsolete compatibility probe that could hit version/date-like paths
    such as ``/api/2026-03-25``. When ``BZZOIRO_FORCE_PREDICTIONS_V2`` is enabled,
    this patch swaps the public provider symbol to the cleaned predictions-v2
    implementation, then adds compatibility shims expected by older diagnostic
    layers.

    Important: ``_fetch_bzzoiro_contexts()`` remains a no-op. Real Bzzoiro data
    is produced by ``fetch_context()``. Calling ``fetch_context()`` from the nested
    alias would duplicate requests and can recurse through generic enrichment
    layers.
    """
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    forced_v2 = _truthy(os.getenv('BZZOIRO_FORCE_PREDICTIONS_V2'), True)
    try:
        import app.providers.bzzoiro as bzzoiro_module
        from app.providers.bzzoiro import BzzoiroContextProvider as CurrentProvider
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}

    BzzoiroContextProvider = CurrentProvider
    if forced_v2:
        try:
            from app.providers.bzzoiro_predictions_v2 import BzzoiroContextProvider as PredictionsV2Provider
            bzzoiro_module.BzzoiroContextProvider = PredictionsV2Provider
            BzzoiroContextProvider = PredictionsV2Provider
        except Exception as exc:
            return {'status': 'v2_import_error', 'error': f'{type(exc).__name__}: {exc}'}

    if not getattr(BzzoiroContextProvider, '_harizon_url_alias_patch', False):
        original_init = BzzoiroContextProvider.__init__

        def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            original_init(self, *args, **kwargs)
            api_root = str(getattr(self, 'api_root_url', '') or '').rstrip('/')
            base_url = str(getattr(self, 'base_url', '') or api_root or 'https://sports.bzzoiro.com/api').rstrip('/')
            self.base_url = base_url
            self.url = api_root or base_url
            self.endpoint_url = api_root or base_url

        BzzoiroContextProvider.__init__ = patched_init
        BzzoiroContextProvider._harizon_url_alias_patch = True

    if not hasattr(BzzoiroContextProvider, '_fetch_rows'):
        async def _fetch_rows(self, client, path='/predictions/', *args, **kwargs):  # type: ignore[no-untyped-def]
            """Compatibility wrapper for old diagnostics.

            Old provider has ``_fetch_paginated_rows``; predictions-v2 provider has
            ``_get_json`` and ``_rows``. Support both without constructing unknown
            date/version endpoints.
            """
            headers = kwargs.pop('headers', None) or {}
            params = kwargs.pop('params', None) or {}
            stats = _ensure_stats(kwargs.pop('stats', None))
            if args:
                if isinstance(args[0], dict) and not params:
                    params = args[0]
                if len(args) > 1 and isinstance(args[1], dict) and not stats:
                    stats = args[1]
            path_text = str(path or '/predictions/')
            for prefix in (str(getattr(self, 'api_root_url', '') or '').rstrip('/'), str(getattr(self, 'base_url', '') or '').rstrip('/')):
                if prefix and path_text.startswith(prefix):
                    path_text = path_text.replace(prefix, '', 1) or '/predictions/'
            if not path_text.startswith('/'):
                path_text = '/' + path_text
            if path_text in {'/', '/events'}:
                path_text = '/predictions/'
            fetch_paginated = getattr(self, '_fetch_paginated_rows', None)
            if callable(fetch_paginated):
                return await fetch_paginated(client, path_text, headers=headers, params=params, stats=stats)
            get_json = getattr(self, '_get_json', None)
            rows_fn = getattr(self, '_rows', None)
            if callable(get_json):
                payload = await get_json(client, path_text, headers, params, stats)
                if callable(rows_fn):
                    return rows_fn(payload)
                if isinstance(payload, list):
                    return [row for row in payload if isinstance(row, dict)]
                if isinstance(payload, dict):
                    for key in ('results', 'data', 'predictions', 'items'):
                        value = payload.get(key)
                        if isinstance(value, list):
                            return [row for row in value if isinstance(row, dict)]
            return []

        BzzoiroContextProvider._fetch_rows = _fetch_rows
        BzzoiroContextProvider._harizon_fetch_rows_alias_patch = True

    if not hasattr(BzzoiroContextProvider, '_build_team_form_contexts'):
        def _build_team_form_contexts(self, matches=None, rows=None, preview=None):  # type: ignore[no-untyped-def]
            """SStats-compatible no-op for generic enrichment layers."""
            if isinstance(preview, dict):
                preview.setdefault('bzzoiro_team_form_compat', 'no_op_current_event_provider')
            return {}

        BzzoiroContextProvider._build_team_form_contexts = _build_team_form_contexts
        BzzoiroContextProvider._harizon_team_form_noop_patch = True

    if not hasattr(BzzoiroContextProvider, '_fetch_bzzoiro_contexts'):
        async def _fetch_bzzoiro_contexts(self, client=None, matches=None, *args, **kwargs):  # type: ignore[no-untyped-def]
            return {}, {
                'enabled': bool(getattr(self, 'api_key', None)),
                'compat_noop': True,
                'reason': 'real_bzzoiro_contexts_are_built_by_fetch_context',
                'requests': 0,
                'contexts_built': 0,
                'events_fetched': 0,
                'response_errors': 0,
            }, []

        BzzoiroContextProvider._fetch_bzzoiro_contexts = _fetch_bzzoiro_contexts
        BzzoiroContextProvider._harizon_nested_context_alias_patch = True

    _INSTALLED = True
    return {
        'status': 'installed',
        'forced_predictions_v2': forced_v2,
        'provider_class_module': getattr(BzzoiroContextProvider, '__module__', ''),
        'patches': [
            'bzzoiro_symbol_predictions_v2' if forced_v2 else 'bzzoiro_symbol_current',
            'bzzoiro_url_alias',
            'bzzoiro_fetch_rows_alias',
            'bzzoiro_team_form_noop',
            'bzzoiro_nested_context_noop',
        ],
    }
