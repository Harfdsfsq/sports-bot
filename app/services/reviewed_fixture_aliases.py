"""Reviewed full-club spellings scoped to competition, country, squad and kickoff.

No global team/history key is rewritten. Shared dates or event IDs alone never
establish a cross-provider identity.
"""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from app.utils import canonicalize_league_name, canonicalize_team_name


@lru_cache(maxsize=1)
def rules():
    return json.loads((Path(__file__).resolve().parents[2] / "config/reviewed_fixture_aliases.json").read_text())["rules"]


def fixture_view(match):
    row = match if isinstance(match, dict) else {key: getattr(match, key, None) for key in (
        "home_team", "away_team", "commence_time", "league_name", "metadata", "sport_key")}
    metadata = row.get("metadata") or {}
    fixture = metadata.get("sstats_fixture") or {}
    ids = metadata.get("provider_source_ids") or metadata.get("day_inventory_source_ids") or {}
    country = None
    if isinstance(fixture, dict) and fixture.get("id") and str(fixture["id"]) == str(ids.get("sstats")):
        league = (fixture.get("season") or {}).get("league") or {}
        country = (league.get("country") or {}).get("code")
    return {"home": row.get("home_team"), "away": row.get("away_team"),
            "commence_time": row.get("commence_time"), "league": row.get("league_name"),
            "country": country, "sport": row.get("sport_key")}


def scoped_fixture_alias_match(match, event):
    from app.services.fixture_identity import squad_identity

    a = fixture_view(match)
    b = event
    if a["sport"] != "soccer" or b.get("sport", "soccer") != "soccer":
        return False
    times = []
    for value in (a["commence_time"], b.get("commence_time")):
        try:
            at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return False
        if at is None or at.tzinfo is None:
            return False
        times.append(at)
    if times[0] != times[1]:
        return False
    for rule in rules():
        if times[0].year not in rule["years"]:
            continue
        names = {canonicalize_league_name(n) for n in rule["league_names"]}
        qualified = {canonicalize_league_name(n) for n in rule["country_qualified_leagues"]}
        leagues = [canonicalize_league_name(view.get("league") or "") for view in (a, b)]
        if any(league not in names for league in leagues):
            continue
        if any(view.get("country") not in (None, rule["country"])
               or (league not in qualified and view.get("country") != rule["country"])
               for view, league in zip((a, b), leagues, strict=True)):
            continue
        groups = [{canonicalize_team_name(n) for n in group} for group in rule["team_groups"]]
        changed, valid = False, True
        for side in ("home", "away"):
            left, right = a.get(side), b.get(side)
            if not left or not right or squad_identity(left) != squad_identity(right):
                valid = False
                break
            left, right = canonicalize_team_name(left), canonicalize_team_name(right)
            if left == right and left:
                continue
            if not any(left in group and right in group for group in groups):
                valid = False
                break
            changed = True
        if valid and changed:
            return True
    return False
