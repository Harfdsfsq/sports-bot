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


def _legacy_assignments(
    rows: list[dict[str, Any]],
    run_index: int,
) -> dict[str, dict[str, list[str]]]:
    del run_index
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
        hours = as_float(row.get("hours_to_kickoff"), 99.0)
        if row.get("provider_assignment_eligible") is False or hours < -0.25:
            continue
        key = row["match_key"]
        odds = set(row["odds_sources"])
        contexts = set(row["context_sources"])
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

    pari_limit = max(1, as_int(os.getenv("SSTATS_PARI_DETAIL_MATCH_LIMIT"), 300))
    sportlogic_limit = max(1, as_int(os.getenv("SPORTLOGIC_MATCH_LIMIT"), 300))
    bzz_limit = max(
        1,
        as_int(
            os.getenv("BZZOIRO_RUNTIME_DETAIL_MATCH_LIMIT")
            or os.getenv("BZZOIRO_V2_MATCH_LIMIT"),
            300,
        ),
    )
    out["sstats_pari"]["offers"] = out["sstats_pari"]["offers"][:pari_limit]
    for role in ("offers", "context"):
        out["sportlogic"][role] = out["sportlogic"][role][:sportlogic_limit]
        out["bzzoiro"][role] = out["bzzoiro"][role][:bzz_limit]
    return out


def build_assignments(
    rows: list[dict[str, Any]],
    run_index: int,
) -> dict[str, dict[str, list[str]]]:
    try:
        from app.services.focused_alpha_assignments_v2 import (
            build_focused_assignments,
            enabled,
        )

        if enabled():
            return build_focused_assignments(rows, run_index)
    except Exception:
        pass
    return _legacy_assignments(rows, run_index)


__all__ = ["build_assignments"]
