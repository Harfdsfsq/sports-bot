from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
_MARK = "_harizon_sportlogic_docs_runtime_patch_v1"


def _date_variants(days: int = 2) -> list[dict[str, Any]]:
    today = datetime.now(UTC).date()
    end = today + timedelta(days=days)
    return [
        {"date_from": today.isoformat(), "date_to": end.isoformat(), "status": "scheduled", "per_page": 100},
        {"date_from": today.isoformat(), "date_to": end.isoformat(), "status": "live", "per_page": 100},
        {"date_from": today.isoformat(), "date_to": end.isoformat(), "per_page": 100},
        {"date_from": today.isoformat(), "status": "scheduled", "per_page": 100},
    ]


def _future_rows(provider: Any, rows: list[dict[str, Any]], days: int = 2) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    upper = now + timedelta(days=days)
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            dt = provider._fixture_datetime(row)
            if dt is None:
                continue
            dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
            if now - timedelta(hours=2) <= dt <= upper:
                out.append(row)
        except Exception:
            continue
    return out


def _patch_extract_list(cls: Any) -> None:
    original = cls._extract_list

    def extract_list(payload: Any) -> list[dict[str, Any]]:
        rows = original(payload)
        if rows:
            return rows
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                for key in ("data", "items", "results", "odds"):
                    value = data.get(key)
                    if isinstance(value, list):
                        return [row for row in value if isinstance(row, dict)]
            meta = payload.get("meta") or payload.get("pagination")
            if isinstance(meta, dict) and isinstance(payload.get("data"), list):
                return [row for row in payload["data"] if isinstance(row, dict)]
        return []

    cls._extract_list = staticmethod(extract_list)


def _patch_fixture_loading(cls: Any) -> None:
    original_load = cls._load_fixtures_for_matches
    original_fetch_matches = cls.fetch_matches

    async def load_fixtures_for_matches(self: Any, matches: list[Any]):
        fixtures, stats, preview = await original_load(self, matches)
        future = _future_rows(self, fixtures, int(getattr(self.settings, "run_days_ahead", 2) or 2))
        if future:
            self._fixture_cache = future
            stats["sportlogic_docs_future_rows"] = len(future)
            return future, stats, preview
        import httpx
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for params in _date_variants(int(getattr(self.settings, "run_days_ahead", 2) or 2)):
                payload = await self._get_json(client, "/games", params, stats, preview)
                rows = self._extract_list(payload)
                future = _future_rows(self, rows, int(getattr(self.settings, "run_days_ahead", 2) or 2))
                stats.setdefault("sportlogic_docs_attempts", []).append({"path": "/games", "params": params, "rows": len(rows), "future": len(future)})
                if future:
                    self._fixture_cache = future
                    stats["fixtures_fetched"] = len(future)
                    stats["sportlogic_docs_future_rows"] = len(future)
                    preview["sample_fixtures"] = future[:3]
                    return future, stats, preview
        return fixtures, stats, preview

    async def fetch_matches(self: Any):
        matches, stats, preview = await original_fetch_matches(self)
        if matches:
            return matches, stats, preview
        fixtures, fixture_stats, fixture_preview = await load_fixtures_for_matches(self, [])
        stats.update({f"fallback_{k}": v for k, v in fixture_stats.items() if k not in stats})
        built = []
        seen = set()
        for row in fixtures:
            match = self._row_to_match(row)
            if match is None or match.match_key in seen:
                continue
            seen.add(match.match_key)
            built.append(match)
        stats["matches_built"] = len(built)
        preview["sample_fixtures"] = fixture_preview.get("sample_fixtures", [])[:3]
        preview["sample_matches"] = [{"home": m.home_team, "away": m.away_team, "league": m.league_name, "start": m.commence_time.isoformat()} for m in built[:8]]
        return built[: self.match_limit], stats, preview

    cls._load_fixtures_for_matches = load_fixtures_for_matches
    cls.fetch_matches = fetch_matches


def install() -> bool:
    from app.providers.sportlogic_provider import SportLogicProvider
    if getattr(SportLogicProvider, _MARK, False):
        return False
    _patch_extract_list(SportLogicProvider)
    _patch_fixture_loading(SportLogicProvider)
    setattr(SportLogicProvider, _MARK, True)
    return True
