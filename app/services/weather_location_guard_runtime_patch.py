from __future__ import annotations

"""Prevent weather providers from spending quota on team-name guesses.

Weather is useful for totals only when the query is grounded in a venue/city/country.
The old fallback used the home team name when venue/city was missing, which produced
requests such as q=Ferroviaria SP or q=Patuxent Football Athletics.  Those can return
random cities and should not be counted as weather confirmation.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PATCH_MARKER = "_harizon_weather_location_guard_v1"
UTC = timezone.utc
STATUS_PATH = Path(".data/exports/latest-weather-location-guard.json")


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _write_status(payload: dict[str, Any]) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"created_at_utc": datetime.now(UTC).isoformat(), **payload}
        STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", " ").split())


def _dig(payload: Any, *path: str) -> Any:
    cur = payload
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def install() -> bool:
    if not _truthy("WEATHER_REQUIRE_GROUNDED_LOCATION", True):
        _write_status({"status": "disabled", "reason": "WEATHER_REQUIRE_GROUNDED_LOCATION=false"})
        return False
    try:
        from app.providers import weather_common as module
    except Exception as exc:
        _write_status({"status": "error", "stage": "import", "error": f"{type(exc).__name__}: {exc}"})
        return False
    cls = getattr(module, "WeatherContextEnricher", None)
    if cls is None:
        _write_status({"status": "error", "reason": "WeatherContextEnricher_missing"})
        return False
    if getattr(cls, PATCH_MARKER, False):
        _write_status({"status": "already_installed", "patch_marker": PATCH_MARKER})
        return True

    def location_from_fixture_guarded(self: Any, match: Any, fixture_row: dict[str, Any]) -> dict[str, str] | None:
        fixture = fixture_row.get("fixture") if isinstance(fixture_row, dict) else {}
        venue = fixture.get("venue") if isinstance(fixture, dict) else {}
        league = fixture_row.get("league") if isinstance(fixture_row, dict) else {}
        if not isinstance(venue, dict):
            venue = {}
        if not isinstance(league, dict):
            league = {}

        city = str(
            venue.get("city")
            or fixture_row.get("venue_city")
            or fixture_row.get("city")
            or _dig(fixture_row, "metadata", "venue", "city")
            or ""
        ).strip()
        venue_name = str(
            venue.get("name")
            or fixture_row.get("venue_name")
            or fixture_row.get("stadium")
            or _dig(fixture_row, "metadata", "venue", "name")
            or ""
        ).strip()
        country = str(
            league.get("country")
            or fixture_row.get("country")
            or _dig(fixture_row, "metadata", "country")
            or ""
        ).strip()

        query = ""
        if city and country:
            query = f"{city}, {country}"
        elif city:
            query = city
        elif venue_name and country:
            query = f"{venue_name}, {country}"
        elif venue_name:
            query = venue_name
        else:
            # Deliberately do not use home/away team as location fallback.
            return None

        home = _norm(getattr(match, "home_team", ""))
        away = _norm(getattr(match, "away_team", ""))
        q_norm = _norm(query)
        if q_norm and (q_norm == home or q_norm == away):
            return None
        return {"query": query.strip(), "city": city, "venue": venue_name, "country": country}

    cls._location_from_fixture = location_from_fixture_guarded
    setattr(cls, PATCH_MARKER, True)
    _write_status({"status": "installed", "patch_marker": PATCH_MARKER})
    return True
