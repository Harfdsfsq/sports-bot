from __future__ import annotations

"""Widen Bzzoiro v2 event-list dates to include the active runtime day.

Recent run artifacts showed the Bzzoiro v2 context target pass fetching only the
next-day `/events/` page while fallback candidates were on the current runtime
day.  That produced v2 ctx/odds zero and left fallback candidates with proxy
1.00:1.00 xG.  This patch keeps the provider matching/detail logic unchanged and
only expands the event-list date window by a small bounded buffer.
"""

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _int_env(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(float(str(raw).strip())) if raw not in (None, "") else default
    except Exception:
        return default


def _date(value: Any) -> date | None:
    try:
        if value in (None, ""):
            return None
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def install() -> None:
    if not _truthy("BZZOIRO_V2_RUNTIME_DAY_WINDOW_PATCH", True):
        return
    try:
        from app.providers import bzzoiro_v2
    except Exception:
        return
    cls = getattr(bzzoiro_v2, "BzzoiroContextProvider", None)
    if cls is None or getattr(cls, "_harizon_runtime_day_window_patched", False):
        return
    original = getattr(cls, "_fetch_events", None)
    if not callable(original):
        return

    async def patched_fetch_events(self, client, headers, date_from: str, date_to: str, stats: dict[str, Any]):
        start = _date(date_from)
        end = _date(date_to)
        if start is None or end is None:
            return await original(self, client, headers, date_from, date_to, stats)

        runtime_day = datetime.now(UTC).date()
        pad_days = max(0, min(_int_env("BZZOIRO_V2_DATE_WINDOW_PAD_DAYS", 1), 2))
        max_span_days = max(2, _int_env("BZZOIRO_V2_DATE_WINDOW_MAX_SPAN_DAYS", 5))
        effective_start = min(start, runtime_day) - timedelta(days=pad_days)
        effective_end = max(end, runtime_day) + timedelta(days=pad_days)
        if (effective_end - effective_start).days > max_span_days:
            effective_start = min(start, runtime_day)
            effective_end = max(end, runtime_day)

        if isinstance(stats, dict):
            stats["runtime_day_window_patch"] = {
                "enabled": True,
                "original_date_from": date_from,
                "original_date_to": date_to,
                "effective_date_from": effective_start.isoformat(),
                "effective_date_to": effective_end.isoformat(),
                "runtime_day_utc": runtime_day.isoformat(),
                "pad_days": pad_days,
                "max_span_days": max_span_days,
            }
        return await original(self, client, headers, effective_start.isoformat(), effective_end.isoformat(), stats)

    cls._fetch_events = patched_fetch_events
    cls._harizon_runtime_day_window_patched = True
