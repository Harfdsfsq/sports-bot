from __future__ import annotations

import os
from typing import Any

from app.utils import canonicalize_team_name, team_similarity

_INSTALLED = False
_ORIGINAL_FIND = None

_NOISE = {
    "fc",
    "cf",
    "sc",
    "ac",
    "club",
    "football",
    "city",
    "united",
    "athletic",
    "sporting",
    "de",
    "the",
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in canonicalize_team_name(value).split()
        if token and token not in _NOISE and len(token) >= 3
    }


def _find(self: Any, team_name: str, rows: list[dict[str, str]]) -> dict[str, str] | None:
    if not rows:
        return None
    target = canonicalize_team_name(team_name)
    target_tokens = _tokens(team_name)
    aliases = {
        canonicalize_team_name(alias)
        for alias in getattr(__import__("app.providers.clubelo", fromlist=["CLUBELO_TEAM_ALIASES"]), "CLUBELO_TEAM_ALIASES", {}).get(target, ())
    }
    best_row: dict[str, str] | None = None
    best_score = 0.0
    second_score = 0.0
    for row in rows:
        club = str(row.get("Club") or row.get("club") or "").strip()
        if not club:
            continue
        canonical_club = canonicalize_team_name(club)
        if canonical_club == target or canonical_club in aliases:
            return row
        club_tokens = _tokens(club)
        shared = target_tokens & club_tokens
        score = float(team_similarity(target, club))
        no_shared_floor = 0.975
        if not shared and score < no_shared_floor:
            continue
        if shared:
            coverage = len(shared) / max(1, min(len(target_tokens), len(club_tokens)))
            score = max(score, 0.72 + 0.22 * coverage)
        if score > best_score:
            second_score = best_score
            best_score = score
            best_row = row
        elif score > second_score:
            second_score = score
    try:
        threshold = float(os.getenv("CLUBELO_TEAM_MATCH_THRESHOLD_STRICT") or 0.86)
    except ValueError:
        threshold = 0.86
    margin = 0.025
    if best_row is None or best_score < threshold:
        return None
    if second_score >= threshold and best_score - second_score < margin:
        return None
    return best_row


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_FIND
    if _INSTALLED:
        return {"status": "already_installed"}
    from app.providers.clubelo import ClubEloContextProvider

    current = ClubEloContextProvider._find_rating_row
    if getattr(current, "_harizon_clubelo_strict_match", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_FIND = current
    _find._harizon_clubelo_strict_match = True
    ClubEloContextProvider._find_rating_row = _find
    _INSTALLED = True
    return {
        "status": "installed",
        "threshold": 0.86,
        "requires_distinctive_token_or_near_exact": True,
        "ambiguity_margin": 0.025,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
