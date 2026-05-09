from __future__ import annotations

from typing import Any

_INSTALLED = False


def _ensure_stats(stats: Any) -> dict[str, Any]:
    return stats if isinstance(stats, dict) else {}


def install() -> dict[str, Any]:
    """Apply compatibility fixes for Bzzoiro provider runtime.

    Runtime diagnostics/patch layers historically expected Bzzoiro providers to
    expose `.url`, `_fetch_rows()`, `_fetch_bzzoiro_contexts()` and sometimes the
    SStats-specific `_build_team_form_contexts()` helper. The current Bzzoiro
    provider uses `.base_url` and `_fetch_paginated_rows()` and already builds its
    own current-event prediction contexts. Missing compatibility methods caused
    AttributeError after successful Bzzoiro HTTP responses, so the provider was
    reported as `нет данных` despite returning events/contexts.
    """
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}

    if not getattr(BzzoiroContextProvider, '_harizon_url_alias_patch', False):
        original_init = BzzoiroContextProvider.__init__

        def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            original_init(self, *args, **kwargs)
            base_url = str(getattr(self, 'base_url', '') or 'https://sports.bzzoiro.com/api').rstrip('/')
            self.base_url = base_url
            self.url = base_url
            self.endpoint_url = base_url

        BzzoiroContextProvider.__init__ = patched_init
        BzzoiroContextProvider._harizon_url_alias_patch = True

    if not hasattr(BzzoiroContextProvider, '_fetch_rows'):
        async def _fetch_rows(self, client, path='/events/', *args, **kwargs):  # type: ignore[no-untyped-def]
            """Compatibility wrapper around `_fetch_paginated_rows()`.

            Accepts both the old call style:
              _fetch_rows(client, path, headers=..., params=..., stats=...)
            and looser diagnostic calls that pass url/path/params positionally.
            """
            headers = kwargs.pop('headers', None) or {}
            params = kwargs.pop('params', None) or {}
            stats = _ensure_stats(kwargs.pop('stats', None))
            if args:
                if isinstance(args[0], dict) and not params:
                    params = args[0]
                if len(args) > 1 and isinstance(args[1], dict) and not stats:
                    stats = args[1]
            path_text = str(path or '/events/')
            if path_text.startswith(str(getattr(self, 'base_url', '') or '')):
                path_text = path_text.replace(str(getattr(self, 'base_url', '') or '').rstrip('/'), '', 1) or '/events/'
            if not path_text.startswith('/'):
                path_text = '/' + path_text
            fetch_paginated = getattr(self, '_fetch_paginated_rows')
            return await fetch_paginated(client, path_text, headers=headers, params=params, stats=stats)

        BzzoiroContextProvider._fetch_rows = _fetch_rows
        BzzoiroContextProvider._harizon_fetch_rows_alias_patch = True

    if not hasattr(BzzoiroContextProvider, '_build_team_form_contexts'):
        def _build_team_form_contexts(self, matches=None, rows=None, preview=None):  # type: ignore[no-untyped-def]
            """SStats-compatible no-op.

            Bzzoiro is not a historical team-form provider; it already builds
            current-event prediction contexts in `fetch_context()`. Returning an
            empty mapping lets generic enrichment/diagnostic layers proceed
            without treating Bzzoiro as failed.
            """
            if isinstance(preview, dict):
                preview.setdefault('bzzoiro_team_form_compat', 'no_op_current_event_provider')
            return {}

        BzzoiroContextProvider._build_team_form_contexts = _build_team_form_contexts
        BzzoiroContextProvider._harizon_team_form_noop_patch = True

    if not hasattr(BzzoiroContextProvider, '_fetch_bzzoiro_contexts'):
        async def _fetch_bzzoiro_contexts(self, client=None, matches=None, *args, **kwargs):  # type: ignore[no-untyped-def]
            """SStats nested-provider compatibility wrapper.

            Some generic enrichment layers call `_fetch_bzzoiro_contexts()` on
            whichever context provider is active. For the real Bzzoiro provider,
            the correct implementation is simply `fetch_context(matches)`. The
            unused client argument is accepted for compatibility with the SStats
            embedded Bzzoiro helper signature.
            """
            target_matches = matches
            if target_matches is None and args:
                for item in args:
                    if isinstance(item, list):
                        target_matches = item
                        break
            if target_matches is None:
                target_matches = []
            contexts, stats, preview = await self.fetch_context(target_matches)
            if isinstance(preview, dict):
                preview.setdefault('bzzoiro_nested_context_compat', True)
                preview_payload = preview.get('matched_examples') or preview.get('sample_predictions') or preview
            else:
                preview_payload = preview
            return contexts, stats, preview_payload if isinstance(preview_payload, list) else [preview_payload]

        BzzoiroContextProvider._fetch_bzzoiro_contexts = _fetch_bzzoiro_contexts
        BzzoiroContextProvider._harizon_nested_context_alias_patch = True

    _INSTALLED = True
    return {
        'status': 'installed',
        'patches': [
            'bzzoiro_url_alias',
            'bzzoiro_fetch_rows_alias',
            'bzzoiro_team_form_noop',
            'bzzoiro_nested_context_alias',
        ],
    }
