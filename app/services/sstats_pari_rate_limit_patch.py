from __future__ import annotations

"""Keep SStats Pari full-cohort detail requests inside the documented minute cap."""

import asyncio
import os
import time
from collections import deque
from typing import Any

_INSTALLED = False
_ORIGINAL_GET_JSON = None


async def _acquire(provider: Any, stats: dict[str, Any]) -> None:
    try:
        limit = max(1, int(float(os.getenv("SSTATS_PARI_RATE_LIMIT_PER_MINUTE") or 140)))
    except Exception:
        limit = 140
    try:
        period = max(1.0, float(os.getenv("SSTATS_PARI_RATE_LIMIT_WINDOW_SECONDS") or 60.0))
    except Exception:
        period = 60.0

    lock = getattr(provider, "_harizon_pari_rate_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(provider, "_harizon_pari_rate_lock", lock)
    timestamps = getattr(provider, "_harizon_pari_rate_timestamps", None)
    if timestamps is None:
        timestamps = deque()
        setattr(provider, "_harizon_pari_rate_timestamps", timestamps)

    async with lock:
        while True:
            now = time.monotonic()
            while timestamps and now - timestamps[0] >= period:
                timestamps.popleft()
            if len(timestamps) < limit:
                timestamps.append(now)
                stats["rate_limit_per_minute"] = limit
                stats["rate_limit_window_seconds"] = period
                return
            wait = max(0.01, period - (now - timestamps[0]) + 0.01)
            stats["rate_limit_waits"] = int(stats.get("rate_limit_waits") or 0) + 1
            stats["rate_limit_wait_seconds"] = round(float(stats.get("rate_limit_wait_seconds") or 0.0) + wait, 3)
            await asyncio.sleep(wait)


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_GET_JSON
    if _INSTALLED:
        return {"status": "already_installed"}

    from app.providers.sstats_pari_odds import SStatsPariOddsProvider

    current = SStatsPariOddsProvider._get_json
    if getattr(current, "_harizon_sstats_pari_rate_limit", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_GET_JSON = current

    async def _get_json(self: Any, client: Any, path: str, params: dict[str, Any], stats: dict[str, Any]) -> Any:
        assert callable(_ORIGINAL_GET_JSON)
        # The list endpoint is a single cheap request. Pace detail calls so a
        # 300-match target is completed in controlled windows instead of bursting.
        if str(path).startswith("/Pari/match/"):
            await _acquire(self, stats)
        return await _ORIGINAL_GET_JSON(self, client, path, params, stats)

    _get_json._harizon_sstats_pari_rate_limit = True
    SStatsPariOddsProvider._get_json = _get_json
    _INSTALLED = True
    return {
        "status": "installed",
        "rate_limit_per_minute": int(float(os.getenv("SSTATS_PARI_RATE_LIMIT_PER_MINUTE") or 140)),
        "detail_requests_only": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
