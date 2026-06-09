from __future__ import annotations

"""Shared publication threshold helpers for HARIZON.

The project rules define two public-quality tiers:

* A-tier: 2+ independent odds sources and 2+ context sources.
* B-tier: 1+ odds source and 1+ context source, but still with the normal
  value, line-movement, xG/context and final Telegram guards.

Several legacy runtime scripts used hard-coded ``max(2, ...)`` floors.  That
made B-tier impossible even when the workflow/env requested it.  This module is
small on purpose so application code and standalone scripts can share the same
contract without importing the full settings object.
"""

import os
from typing import Any

_FALSE = {"0", "false", "no", "off", "none", "null"}
_TRUE = {"1", "true", "yes", "on", "force", "b", "b_tier", "btier", "hybrid", "auto"}
_STRICT_A_MODES = {"a", "a_tier", "atier", "strict", "strict_a", "a_only", "tier_a_only"}


def truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    if raw in _FALSE:
        return False
    return raw in _TRUE


def as_int(value: Any, default: int) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip().replace(",", ".")))
    except Exception:
        return default


def b_tier_enabled(settings: Any | None = None) -> bool:
    raw_mode = (
        os.getenv("PUBLISH_COVERAGE_TIER_MODE")
        or os.getenv("HARIZON_PUBLICATION_TIER_MODE")
        or str(getattr(settings, "publish_coverage_tier_mode", "") or "")
    ).strip().lower()
    if raw_mode in _STRICT_A_MODES:
        return False
    explicit = os.getenv("PUBLISH_ALLOW_B_TIER") or os.getenv("CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B")
    if explicit is not None and str(explicit).strip() != "":
        return truthy(explicit, True)
    return True


def publish_floor(settings: Any | None = None) -> int:
    return 1 if b_tier_enabled(settings) else 2


def publish_min_odds_sources(settings: Any | None = None, default: int | None = None) -> int:
    fallback = publish_floor(settings) if default is None else int(default)
    setting_value = getattr(settings, "min_sources_publish", None) if settings is not None else None
    raw = os.getenv("PUBLISH_MIN_ODDS_SOURCES") or os.getenv("TELEGRAM_MIN_ODDS_SOURCES") or os.getenv("MIN_SOURCES_PUBLISH")
    value = as_int(raw if raw not in (None, "") else setting_value, fallback)
    return max(publish_floor(settings), value)


def publish_min_context_sources(settings: Any | None = None, default: int | None = None) -> int:
    fallback = publish_floor(settings) if default is None else int(default)
    setting_value = getattr(settings, "min_context_sources_publish", None) if settings is not None else None
    raw = os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH")
    value = as_int(raw if raw not in (None, "") else setting_value, fallback)
    return max(publish_floor(settings), value)


def publish_min_books(settings: Any | None = None, default: int | None = None) -> int:
    fallback = publish_floor(settings) if default is None else int(default)
    setting_value = getattr(settings, "min_books_publish", None) if settings is not None else None
    raw = os.getenv("PUBLISH_MIN_BOOKS") or os.getenv("MIN_BOOKS_PUBLISH")
    value = as_int(raw if raw not in (None, "") else setting_value, fallback)
    return max(publish_floor(settings), value)


def controlled_fallback_min_odds_sources(settings: Any | None = None) -> int:
    raw = os.getenv("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_INDEPENDENT_SOURCES")
    return max(publish_floor(settings), as_int(raw, publish_min_odds_sources(settings)))


def controlled_fallback_min_context_sources(settings: Any | None = None) -> int:
    raw = os.getenv("CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES")
    return max(publish_floor(settings), as_int(raw, publish_min_context_sources(settings)))
