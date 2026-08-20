from __future__ import annotations

"""Shared publication threshold helpers for HARIZON.

The rules define a strict A-tier and a lighter B-tier:

* A-tier: 2 odds/line sources, 2 bookmaker/price confirmations, 2 contexts;
* B-tier: 1 odds/line source, 2 bookmaker/price confirmations, 1 context.

Value, quality, price-integrity and line-movement guards still apply to both.

Important: these helpers must never raise a configured value above what the
workflow env asked for. The A-tier quorum (2 odds / 2 context sources) is
enforced by ``scripts/patch_publication_safety_contract.py`` and by the
``PUBLISH_TIER_A_*`` variables, not by silently clamping the shared floor.
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
    return 1


def publish_min_odds_sources(settings: Any | None = None, default: int | None = None) -> int:
    fallback = publish_floor(settings) if default is None else int(default)
    setting_value = getattr(settings, "min_sources_publish", None) if settings is not None else None
    raw = os.getenv("PUBLISH_MIN_ODDS_SOURCES") or os.getenv("TELEGRAM_MIN_ODDS_SOURCES") or os.getenv("MIN_SOURCES_PUBLISH")
    value = as_int(raw if raw not in (None, "") else setting_value, fallback)
    return max(publish_floor(settings), value)


def publish_min_context_sources(settings: Any | None = None, default: int | None = None) -> int:
    """Minimum independent context sources required for publication.

    B-tier (RULES.txt 8.1) needs one real context, so a configured value of 1
    must be honoured. Only strict A-only mode keeps the hard floor of 2.
    """
    strict_a_only = not b_tier_enabled(settings)
    floor = 2 if strict_a_only else publish_floor(settings)
    fallback = (2 if strict_a_only else 1) if default is None else int(default)
    setting_value = getattr(settings, "min_context_sources_publish", None) if settings is not None else None
    raw = os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH")
    value = as_int(raw if raw not in (None, "") else setting_value, fallback)
    return max(floor, value)


def publish_tier_a_min_context_sources(settings: Any | None = None) -> int:
    """A-tier context quorum. Always at least 2, per RULES.txt 8.2."""
    raw = os.getenv("PUBLISH_TIER_A_MIN_CONTEXT_SOURCES")
    return max(2, as_int(raw, 2))


def publish_tier_a_min_odds_sources(settings: Any | None = None) -> int:
    """A-tier line quorum. Always at least 2, per RULES.txt 8.2."""
    raw = os.getenv("PUBLISH_TIER_A_MIN_ODDS_SOURCES")
    return max(2, as_int(raw, 2))


def publish_min_books(settings: Any | None = None, default: int | None = None) -> int:
    floor = 1 if b_tier_enabled(settings) else 2
    fallback = floor if default is None else int(default)
    setting_value = getattr(settings, "min_books_publish", None) if settings is not None else None
    raw = os.getenv("PUBLISH_MIN_BOOKS") or os.getenv("MIN_BOOKS_PUBLISH")
    value = as_int(raw if raw not in (None, "") else setting_value, fallback)
    return max(floor, value)


def controlled_fallback_min_odds_sources(settings: Any | None = None) -> int:
    raw = os.getenv("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_INDEPENDENT_SOURCES")
    return max(publish_floor(settings), as_int(raw, publish_min_odds_sources(settings)))


def controlled_fallback_min_context_sources(settings: Any | None = None) -> int:
    raw = os.getenv("CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES")
    return max(publish_floor(settings), as_int(raw, publish_min_context_sources(settings)))
