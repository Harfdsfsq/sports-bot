from __future__ import annotations

"""Cap controlled-fallback top-bundle size by remaining daily slots.

Hard daily limit is checked per candidate before ranking, but top-bundle selection
can otherwise publish several candidates while every candidate still sees the
same pre-send daily count.  Example: existing 4/5 and max bundle 3 produced a
3-pick Telegram message, ending at 7/5.  This patch keeps the per-candidate
quality/value guards intact and limits the selected bundle to the remaining
semantic daily slots.
"""

import os
from typing import Any


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off", "none", "null"}:
        return False
    return raw in {"1", "true", "yes", "on", "force"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _remaining_daily_slots(v18: Any) -> tuple[int | None, dict[str, Any]]:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_DAILY_LIMIT_ENABLED"), True):
        return None, {"enabled": False}
    limit = _as_int(
        os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED")
        or os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_B_TIER")
        or 0,
        0,
    )
    if limit <= 0:
        return None, {"enabled": False, "limit": limit}
    try:
        info = v18._daily_existing_fallback_count()
    except Exception:
        info = {"count": 0}
    count = _as_int(info.get("count"), 0) if isinstance(info, dict) else 0
    remaining = max(0, limit - count)
    return remaining, {"enabled": True, "limit": limit, "count": count, "remaining": remaining, "info": info}


def install(v18: Any) -> None:
    base = getattr(v18, "base", None)
    original = getattr(base, "select_top_picks", None) if base is not None else None
    if not callable(original):
        return
    if getattr(base, "_daily_slot_bundle_cap_patch_installed", False):
        return

    def select_top_picks_daily_cap(viable: list[Any], bankroll: dict[str, Any]) -> list[Any]:
        selected = list(original(viable, bankroll) or [])
        remaining, info = _remaining_daily_slots(v18)
        if remaining is None:
            return selected
        capped = selected[:remaining]
        if len(capped) != len(selected):
            event = {
                "guard": "controlled_fallback_daily_slot_bundle_cap",
                "selected_before_cap": len(selected),
                "selected_after_cap": len(capped),
                "daily_slot_info": info,
                "reasons": [f"daily_bundle_cap_remaining_slots:{remaining}"],
            }
            try:
                v18._GUARD_EVENTS.append(event)
            except Exception:
                pass
        return capped

    base.select_top_picks = select_top_picks_daily_cap
    base._daily_slot_bundle_cap_patch_installed = True
