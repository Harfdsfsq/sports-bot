"""Reviewed college league routing and provider-ID-scoped team aliases."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.utils import canonicalize_league_name, canonicalize_team_name

CONFERENCES = {
    "america east", "metro", "missouri valley", "summit league", "sun belt",
    "big ten", "american", "ohio valley", "atlantic coast", "big east",
    "horizon league", "atlantic sun", "west coast", "conference usa",
}


def ncaa_league_slug(name):
    key = canonicalize_league_name(name)
    if "ncaa" in key:
        return "usa.ncaa.w.1" if any(t in key.split() for t in ("women", "womens", "female")) else "usa.ncaa.m.1"
    if not key.startswith("usa "):
        return None
    conference = key.removeprefix("usa ").removesuffix(" playoffs").removesuffix(" conference")
    return "usa.ncaa.m.1" if conference in CONFERENCES else None


@lru_cache(maxsize=1)
def team_aliases():
    return json.loads((Path(__file__).parents[2] / "config/espn_team_aliases.json").read_text())


def espn_event_identity(event, slug):
    competition = (event.get("competitions") or [{}])[0]
    result = {"commence_time": event.get("date") or competition.get("date")}
    for competitor in competition.get("competitors") or []:
        side = competitor.get("homeAway")
        if side not in {"home", "away"}:
            continue
        team = competitor.get("team") or {}
        names = [team.get(k) for k in ("displayName", "shortDisplayName", "name")]
        reviewed = team_aliases().get(slug, {}).get(str(team.get("id")), {})
        # An ID is meaningful only in this league and with its witnessed name.
        if reviewed and canonicalize_team_name(team.get("displayName")) == canonicalize_team_name(reviewed["provider_name"]):
            names.extend(reviewed["aliases"])
        result[side + "_aliases"] = names
    return result
