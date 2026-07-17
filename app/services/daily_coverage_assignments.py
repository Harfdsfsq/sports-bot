from __future__ import annotations

import os
from typing import Any

from app.services.daily_coverage_common import as_float, as_int


def _senior_club(row: dict[str, Any]) -> bool:
    text = " ".join(
        str(row.get(key) or "").lower()
        for key in ("league_name", "home_team", "away_team")
    )
    return not any(
        token in text
        for token in (
            "women",
            "u17",
            "u18",
            "u19",
            "u20",
            "u21",
            "u23",
            "youth",
            "reserve",
            "friendly",
        )
    )


def build_assignments(
    rows: list[dict[str, Any]], run_index: int
) -> dict[str, dict[str, list[str]]]:
    out = {
        "odds_api_io": {"offers": []},
        "sstats_pari": {"offers": []},
        "sportlogic": {"offers": [], "context": []},
        "sstats": {"context": []},
        "bzzoiro": {"offers": [], "context": []},
        "clubelo": {"context": []},
        "football_data": {"context": []},
        "espn": {"context": []},
        "openligadb": {"context": []},
        "thesportsdb": {"context": []},
    }
    for row in rows:
        key = row["match_key"]
        odds = set(row["odds_sources"])
        contexts = set(row["context_sources"])
        hours = as_float(row.get("hours_to_kickoff"), 99.0)
        if len(odds) < 2 or hours <= 4:
            out["odds_api_io"]["offers"].append(key)
        for provider in ("sstats_pari", "sportlogic", "bzzoiro"):
            needs_gap = len(odds) < 2 and provider not in odds
            needs_fresh = hours <= 4 and provider in {"sstats_pari", "sportlogic"}
            if needs_gap or needs_fresh:
                out[provider]["offers"].append(key)
        for provider in (
            "sstats",
            "sportlogic",
            "bzzoiro",
            "football_data",
            "espn",
            "openligadb",
            "thesportsdb",
        ):
            needs_gap = len(contexts) < 2 and provider not in contexts
            needs_fresh = hours <= 4 and provider == "sstats"
            if needs_gap or needs_fresh:
                out[provider]["context"].append(key)
        if _senior_club(row) and (
            (len(contexts) < 2 and "clubelo" not in contexts) or hours <= 4
        ):
            out["clubelo"]["context"].append(key)
    pari_limit = (150, 110, 80)[min(run_index, 3) - 1]
    sportlogic_limit = max(
        80, min(300, as_int(os.getenv("SPORTLOGIC_MATCH_LIMIT"), 180))
    )
    bzz_limit = max(8, as_int(os.getenv("BZZOIRO_RUNTIME_DETAIL_MATCH_LIMIT"), 24))
    out["sstats_pari"]["offers"] = out["sstats_pari"]["offers"][:pari_limit]
    for role in ("offers", "context"):
        out["sportlogic"][role] = out["sportlogic"][role][:sportlogic_limit]
        out["bzzoiro"][role] = out["bzzoiro"][role][:bzz_limit]
    return out
