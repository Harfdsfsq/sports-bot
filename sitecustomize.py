"""Runtime hotfixes for sports-bot.

This version avoids importing app modules eagerly. Instead it patches the
normalizer lazily when `app.utils` or `app.providers.api_football` are imported.
That makes it resilient even if importing app.utils during startup would fail.
"""

from __future__ import annotations

import builtins
import re
import sys
from typing import Any


def _normalize_probability_percent_patched(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    had_percent_sign = False

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        lowered = text.lower()
        if lowered in {"n/a", "na", "none", "null", "-", "--", "unknown"}:
            return None
        if "%" in text:
            had_percent_sign = True
        text = text.replace("%", "").replace(",", ".").strip()
        match = re.search(r"[-+]?\d*\.?\d+", text)
        if not match:
            return None
        try:
            number = float(match.group(0))
        except ValueError:
            return None
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

    if had_percent_sign or number > 1.0:
        number /= 100.0

    if number < 0.0:
        number = 0.0
    elif number > 1.0:
        number = 1.0
    return number


def _apply_patches() -> None:
    utils_mod = sys.modules.get("app.utils")
    if utils_mod is not None:
        try:
            utils_mod.normalize_probability_percent = _normalize_probability_percent_patched
        except Exception:
            pass

    provider_mod = sys.modules.get("app.providers.api_football")
    if provider_mod is not None:
        try:
            provider_mod.normalize_probability_percent = _normalize_probability_percent_patched
        except Exception:
            pass


_original_import = builtins.__import__


def _patched_import(name, globals=None, locals=None, fromlist=(), level=0):
    module = _original_import(name, globals, locals, fromlist, level)
    if name == "app.utils" or name.startswith("app.utils.") or name == "app.providers.api_football":
        _apply_patches()
    elif name.startswith("app.providers") and "api_football" in name:
        _apply_patches()
    return module


builtins.__import__ = _patched_import
_apply_patches()
