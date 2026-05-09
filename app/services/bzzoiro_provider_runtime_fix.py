from __future__ import annotations

from typing import Any

_INSTALLED = False


def install() -> dict[str, Any]:
    """Apply small compatibility fixes for Bzzoiro provider runtime.

    Some runtime diagnostics/patch layers expect providers to expose `.url`,
    while BzzoiroContextProvider uses `.base_url`. Missing alias caused
    AttributeError after successful Bzzoiro HTTP responses, so the provider was
    reported as `нет данных` despite returning events.
    """
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}
    if getattr(BzzoiroContextProvider, '_harizon_url_alias_patch', False):
        _INSTALLED = True
        return {'status': 'already_patched'}
    original_init = BzzoiroContextProvider.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        original_init(self, *args, **kwargs)
        base_url = str(getattr(self, 'base_url', '') or 'https://sports.bzzoiro.com/api').rstrip('/')
        self.base_url = base_url
        self.url = base_url
        self.endpoint_url = base_url

    BzzoiroContextProvider.__init__ = patched_init
    BzzoiroContextProvider._harizon_url_alias_patch = True
    _INSTALLED = True
    return {'status': 'installed', 'patch': 'bzzoiro_url_alias'}
