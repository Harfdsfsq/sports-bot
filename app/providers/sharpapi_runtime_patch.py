from __future__ import annotations

"""Runtime integration and market-integrity patch for SharpAPI odds.

Installed from the startup hook chain. It keeps two safety guarantees:
1. odds_api_io cannot publish match totals parsed from corners/cards/HT/team/player markets;
2. SharpAPI can be enabled as an independent secondary odds source without
   rewriting the large runner module.
"""

import builtins
import os
import re
from typing import Any

PATCH_MARKER = "_harizon_sharpapi_runtime_patch_v1"
ODDS_PATCH_MARKER = "_harizon_total_market_integrity_patch_v1"
RUNNER_PATCH_MARKER = "_harizon_sharpapi_runner_patch_v1"
IMPORT_HOOK_MARKER = "_harizon_sharpapi_import_hook_v1"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _sharpapi_enabled() -> bool:
    return _truthy(os.getenv("ENABLE_SHARPAPI") or os.getenv("SHARPAPI_ENABLED"))


def _clean_market_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _is_clean_match_total_market(market_name: Any) -> bool:
    text = _clean_market_text(market_name)
    if not text:
        return False
    tokens = set(text.split())
    blocked = {
        "corner", "corners", "card", "cards", "booking", "bookings", "yellow", "red",
        "player", "shot", "shots", "offsides", "throw", "throwins", "foul", "fouls",
        "half", "halftime", "ht", "1st", "2nd", "period", "quarter", "team", "home", "away",
    }
    if tokens & blocked:
        return False
    allowed_exact = {
        "totals",
        "total",
        "total goals",
        "goals over under",
        "over under",
        "match goals",
        "asian total",
        "asian totals",
    }
    return text in allowed_exact


def _offer_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _unsafe_offer_reason(offer: Any) -> str:
    family = str(getattr(offer, "family", "") or "").strip().lower()
    if family != "totals":
        return ""
    market_name = getattr(offer, "market_name", "")
    if not _is_clean_match_total_market(market_name):
        return f"unsafe_total_market_name:{market_name}"
    selection = str(getattr(offer, "selection", "") or "").strip().lower()
    point = _offer_float(getattr(offer, "point", None), default=-999.0)
    price = _offer_float(getattr(offer, "price", None), default=0.0)
    if abs(point - 1.5) < 1e-9 and selection.startswith("over"):
        max_price = _offer_float(os.getenv("MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS"), default=1.65)
        if price > max_price:
            return f"over15_price_outlier:{price:.3f}>{max_price:.3f}"
    return ""


def _patch_odds_api_io() -> bool:
    try:
        from app.providers.odds_api_io import OddsApiIoProvider
    except Exception:
        return False
    if getattr(OddsApiIoProvider, ODDS_PATCH_MARKER, False):
        return False
    original = getattr(OddsApiIoProvider, "_parse_event_odds", None)
    if not callable(original):
        return False

    def parse_event_odds_patched(self: Any, payload: dict[str, Any], match: Any) -> list[Any]:
        offers = list(original(self, payload, match) or [])
        safe: list[Any] = []
        rejected: list[dict[str, Any]] = []
        for offer in offers:
            reason = _unsafe_offer_reason(offer)
            if reason:
                rejected.append({
                    "source": getattr(offer, "source", ""),
                    "bookmaker": getattr(offer, "bookmaker", ""),
                    "family": getattr(offer, "family", ""),
                    "selection": getattr(offer, "selection", ""),
                    "point": getattr(offer, "point", None),
                    "price": getattr(offer, "price", None),
                    "market_name": getattr(offer, "market_name", ""),
                    "reason": reason,
                })
                continue
            try:
                metadata = getattr(offer, "metadata", None)
                if isinstance(metadata, dict):
                    metadata["market_integrity"] = "safe_match_market"
                    metadata["raw_market_name"] = getattr(offer, "market_name", "")
            except Exception:
                pass
            safe.append(offer)
        if rejected:
            try:
                payload.setdefault("_harizon_market_integrity_rejections", []).extend(rejected[:50])
            except Exception:
                pass
        return safe

    OddsApiIoProvider._parse_event_odds = parse_event_odds_patched
    setattr(OddsApiIoProvider, ODDS_PATCH_MARKER, True)
    return True


def _patch_runner() -> bool:
    if not _sharpapi_enabled():
        return False
    try:
        from app.services.runner import PredictionRunner
        from app.providers.sharpapi import SharpApiOddsProvider
    except Exception:
        return False
    if getattr(PredictionRunner, RUNNER_PATCH_MARKER, False):
        return False

    original_provider_enabled = PredictionRunner._provider_enabled
    original_safe_provider = PredictionRunner._safe_provider

    def provider_enabled_patched(self: Any, provider_name: str, default: bool = True) -> bool:
        if str(provider_name or "").strip().lower() == "bookies_api" and _sharpapi_enabled():
            return True
        return original_provider_enabled(self, provider_name, default)

    def safe_provider_patched(self: Any, module_name: str, class_name: str) -> Any | None:
        if str(module_name or "").endswith("bookies_api") and _sharpapi_enabled():
            try:
                instance = SharpApiOddsProvider(self.settings)
                self._mark_provider_status(
                    "sharpapi",
                    enabled=True,
                    loaded=True,
                    class_name="SharpApiOddsProvider",
                    slot="bookies_api",
                    api_key_present=bool(getattr(instance, "api_key", "")),
                )
                self._mark_provider_status(
                    "bookies_api",
                    enabled=False,
                    loaded=False,
                    reason="replaced_by_sharpapi_runtime_patch",
                )
                return instance
            except Exception as exc:
                self._mark_provider_status(
                    "sharpapi",
                    enabled=True,
                    loaded=False,
                    class_name="SharpApiOddsProvider",
                    error=f"{type(exc).__name__}: {exc}",
                )
                return None
        return original_safe_provider(self, module_name, class_name)

    PredictionRunner._provider_enabled = provider_enabled_patched
    PredictionRunner._safe_provider = safe_provider_patched
    setattr(PredictionRunner, RUNNER_PATCH_MARKER, True)
    return True


def _install_import_hook() -> bool:
    if getattr(builtins, IMPORT_HOOK_MARKER, False):
        _patch_odds_api_io()
        _patch_runner()
        return False
    original_import = builtins.__import__

    def import_patched(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        module = original_import(name, globals, locals, fromlist, level)
        try:
            if name == "app.providers.odds_api_io" or str(name).startswith("app.providers.odds_api_io"):
                _patch_odds_api_io()
            if name == "app.services.runner" or str(name).startswith("app.services.runner"):
                _patch_runner()
        except Exception:
            pass
        return module

    builtins.__import__ = import_patched
    setattr(builtins, IMPORT_HOOK_MARKER, True)
    _patch_odds_api_io()
    _patch_runner()
    return True


def install() -> bool:
    installed = _install_import_hook()
    odds = _patch_odds_api_io()
    runner = _patch_runner()
    return bool(installed or odds or runner)
