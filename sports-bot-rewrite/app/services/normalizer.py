from __future__ import annotations

from app.schemas import Match


def dedupe_matches(matches: list[Match]) -> list[Match]:
    best: dict[str, Match] = {}
    for match in matches:
        existing = best.get(match.match_key)
        if existing is None:
            best[match.match_key] = match
            continue
        if match.source == 'the_odds_api' and existing.source != 'the_odds_api':
            best[match.match_key] = match
    return list(best.values())
