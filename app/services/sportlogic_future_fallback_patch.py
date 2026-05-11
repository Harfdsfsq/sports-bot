from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
_MARK = "_harizon_sportlogic_future_fallback_patch_v1"


def _dt(value: Any):
    try:
        from app.utils import parse_datetime
        parsed = parse_datetime(value)
        if parsed is None:
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except Exception:
        return None


def _row_start(provider: Any, row: dict[str, Any]):
    for key in ("date", "event_date", "start", "starts_at", "start_time", "kickoff", "commence_time"):
        value = row.get(key)
        parsed = _dt(value)
        if parsed is not None:
            return parsed
    try:
        return provider._fixture_datetime(row)
    except Exception:
        return None


def _future_rows(provider: Any, rows: list[dict[str, Any]], days_ahead: int = 2) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    upper = now + timedelta(days=days_ahead)
    out = []
    for row in rows:
        start = _row_start(provider, row)
        if start is not None and now - timedelta(hours=2) <= start <= upper:
            out.append(row)
    return out


def install() -> bool:
    from app.providers.sportlogic_provider import SportLogicProvider

    if getattr(SportLogicProvider, _MARK, False):
        return False
    original = SportLogicProvider._load_fixtures_for_matches

    async def patched(self, matches):
        fixtures, stats, preview = await original(self, matches)
        if _future_rows(self, fixtures, 2):
            return fixtures, stats, preview
        if not matches:
            return fixtures, stats, preview
        broad_stats = self._stats("fixtures_broad_fallback")
        broad_preview = {"sample_fixtures": [], "errors": []}
        broad: list[dict[str, Any]] = []
        import httpx
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            payload = await self._get_json(client, "/games", {"per_page": 100}, broad_stats, broad_preview)
            broad = self._extract_list(payload)
        future = _future_rows(self, broad, int(getattr(self.settings, "run_days_ahead", 2) or 2))
        if future:
            self._fixture_cache = future
            stats["broad_fallback_used"] = True
            stats["broad_fallback_rows"] = len(broad)
            stats["broad_future_rows"] = len(future)
            preview["sample_fixtures"] = future[:3]
            return future, stats, preview
        stats["broad_fallback_used"] = True
        stats["broad_fallback_rows"] = len(broad)
        stats["broad_future_rows"] = 0
        return fixtures, stats, preview

    SportLogicProvider._load_fixtures_for_matches = patched
    setattr(SportLogicProvider, _MARK, True)
    return True
