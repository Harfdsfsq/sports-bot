from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.schemas import Match, Offer


def dedupe_matches(matches: list[Match]) -> list[Match]:
    best: dict[str, Match] = {}
    for match in matches:
        existing = best.get(match.match_key)
        if existing is None:
            best[match.match_key] = match
            continue

        existing_has_meta = len(getattr(existing, 'metadata', {}) or {})
        current_has_meta = len(getattr(match, 'metadata', {}) or {})
        if current_has_meta > existing_has_meta:
            best[match.match_key] = match
            continue

        if match.source == 'bookies_api' and existing.source != 'bookies_api':
            best[match.match_key] = match

    return list(best.values())


def merge_offers(
    settings: Any,
    *offer_maps: dict[str, list[Offer]],
) -> dict[str, list[Offer]]:
    target_books = {
        name.strip().lower()
        for name in getattr(settings, 'target_bookmakers', [])
        if str(name).strip()
    }
    consensus_books = {
        name.strip().lower()
        for name in getattr(settings, 'consensus_bookmakers', [])
        if str(name).strip()
    }
    allowed_books = target_books | consensus_books

    merged: dict[str, list[Offer]] = defaultdict(list)
    seen: set[tuple[str, str, str, str, float | None, float]] = set()

    for offer_map in offer_maps:
        for match_key, offers in (offer_map or {}).items():
            for offer in offers or []:
                bookmaker_key = (offer.bookmaker or '').strip().lower()
                if allowed_books and bookmaker_key and bookmaker_key not in allowed_books:
                    continue

                dedupe_key = (
                    match_key,
                    offer.source,
                    offer.bookmaker,
                    offer.family,
                    offer.point,
                    round(float(offer.price), 6),
                )
                if dedupe_key in seen:
                    continue

                seen.add(dedupe_key)
                merged[match_key].append(offer)

    return dict(merged)
