from __future__ import annotations

"""Runtime integration for RapidAPI OddsFeed bridge.

PredictionRunner already has a secondary odds slot named bookies_api. The old
Bookies API is not currently a verified reliable source, so when
ENABLE_RAPIDAPI_ODDS_BRIDGE=true we reuse that slot for RapidApiOddsBridgeProvider.
This keeps the large runner module unchanged and surfaces stats under
source_stats.bookies_api while provider_status also records rapidapi_odds_bridge.
"""

import builtins
import os
from typing import Any

PATCH_MARKER = "_harizon_rapidapi_bridge_runner_patch_v1"
IMPORT_HOOK_MARKER = "_harizon_rapidapi_bridge_import_hook_v1"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _enabled() -> bool:
    return _truthy(os.getenv("ENABLE_RAPIDAPI_ODDS_BRIDGE")) or _truthy(os.getenv("RAPIDAPI_ODDS_BRIDGE_ENABLED"))


def _patch_runner() -> bool:
    if not _enabled():
        return False
    try:
        from app.services.runner import PredictionRunner
        from app.providers.rapidapi_odds_bridge import RapidApiOddsBridgeProvider
    except Exception:
        return False
    if getattr(PredictionRunner, PATCH_MARKER, False):
        return False

    original_provider_enabled = PredictionRunner._provider_enabled
    original_safe_provider = PredictionRunner._safe_provider

    def provider_enabled_patched(self: Any, provider_name: str, default: bool = True) -> bool:
        if str(provider_name or "").strip().lower() == "bookies_api" and _enabled():
            return True
        return original_provider_enabled(self, provider_name, default)

    def safe_provider_patched(self: Any, module_name: str, class_name: str) -> Any | None:
        if str(module_name or "").endswith("bookies_api") and _enabled():
            try:
                instance = RapidApiOddsBridgeProvider(self.settings)
                self._mark_provider_status(
                    "rapidapi_odds_bridge",
                    enabled=True,
                    loaded=True,
                    class_name="RapidApiOddsBridgeProvider",
                    slot="bookies_api",
                    allowed_providers=os.getenv("RAPIDAPI_ODDS_BRIDGE_ALLOWED_PROVIDERS") or "odds_feed",
                    odds_feed_key_present=bool(os.getenv("ODDS_FEED_RAPIDAPI_KEY") or os.getenv("RAPIDAPI_KEY")),
                )
                self._mark_provider_status(
                    "bookies_api",
                    enabled=True,
                    loaded=True,
                    class_name="RapidApiOddsBridgeProvider",
                    replacement="rapidapi_odds_bridge",
                )
                return instance
            except Exception as exc:
                self._mark_provider_status(
                    "rapidapi_odds_bridge",
                    enabled=True,
                    loaded=False,
                    class_name="RapidApiOddsBridgeProvider",
                    error=f"{type(exc).__name__}: {exc}",
                )
                return None
        return original_safe_provider(self, module_name, class_name)

    PredictionRunner._provider_enabled = provider_enabled_patched
    PredictionRunner._safe_provider = safe_provider_patched
    setattr(PredictionRunner, PATCH_MARKER, True)
    return True


def _install_import_hook() -> bool:
    if getattr(builtins, IMPORT_HOOK_MARKER, False):
        _patch_runner()
        return False
    original_import = builtins.__import__

    def import_patched(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        module = original_import(name, globals, locals, fromlist, level)
        try:
            if name == "app.services.runner" or str(name).startswith("app.services.runner"):
                _patch_runner()
        except Exception:
            pass
        return module

    builtins.__import__ = import_patched
    setattr(builtins, IMPORT_HOOK_MARKER, True)
    _patch_runner()
    return True


def install() -> bool:
    changed = _install_import_hook()
    changed = _patch_runner() or changed
    return changed
