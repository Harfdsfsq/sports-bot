from __future__ import annotations

"""Widen Bzzoiro v2 event-list dates to include the active runtime day.

Recent run artifacts showed the Bzzoiro v2 context target pass fetching only the
next-day `/events/` page while fallback candidates were on the current runtime
day.  That produced v2 ctx/odds zero and left fallback candidates with proxy
1.00:1.00 xG.  This patch keeps the provider matching/detail logic unchanged.

The expanded date window is useful when the target set spans current and next day,
but a wide `/events/` request may occasionally time out.  On timeout/empty-result
it now retries a narrow window before returning zero events.
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


def _span(start: date, end: date) -> int:
    try:
        return max(0, int((end - start).days))
    except Exception:
        return 0


def _record_attempt(stats: dict[str, Any], label: str, start: date, end: date, rows: int) -> None:
    try:
        stats.setdefault("runtime_day_window_patch_attempts", []).append({
            "label": label,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "span_days": _span(start, end),
            "rows": int(rows),
            "last_error": stats.get("last_error"),
            "response_errors": stats.get("response_errors"),
            "requests": stats.get("requests"),
        })
    except Exception:
        pass


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
        if _span(effective_start, effective_end) > max_span_days:
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
                "narrow_retry_enabled": _truthy("BZZOIRO_V2_NARROW_RETRY_ON_TIMEOUT", True),
            }

        rows = await original(self, client, headers, effective_start.isoformat(), effective_end.isoformat(), stats)
        _record_attempt(stats, "expanded", effective_start, effective_end, len(rows or []))
        if rows or not _truthy("BZZOIRO_V2_NARROW_RETRY_ON_TIMEOUT", True):
            return rows

        last_error = str(stats.get("last_error") or "").lower()
        response_errors = int(float(str(stats.get("response_errors") or 0)))
        should_retry = bool(last_error) or response_errors > 0 or (effective_start != start or effective_end != end)
        if not should_retry:
            return rows

        variants: list[tuple[str, date, date]] = []
        if start != effective_start or end != effective_end:
            variants.append(("original_window", start, end))
        runtime_end = runtime_day + timedelta(days=1)
        if (runtime_day, runtime_end) not in {(a, b) for _n, a, b in variants}:
            variants.append(("runtime_day_one_day", runtime_day, runtime_end))
        # If the original provider asked a single day encoded as equal dates, also
        # try date_to+1 because the API behaves better with a half-open day span.
        if start == end:
            variants.append(("original_plus_one_day", start, start + timedelta(days=1)))

        for label, retry_start, retry_end in variants[:3]:
            retry_rows = await original(self, client, headers, retry_start.isoformat(), retry_end.isoformat(), stats)
            _record_attempt(stats, label, retry_start, retry_end, len(retry_rows or []))
            if retry_rows:
                stats["runtime_day_window_patch_narrow_retry_used"] = label
                return retry_rows
        return rows

    cls._fetch_events = patched_fetch_events
    cls._harizon_runtime_day_window_patched = True
