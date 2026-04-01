from __future__ import annotations

from collections import defaultdict

from app.config import Settings
from app.schemas import Match, Offer
from app.utils import normalize_bookmaker_name


def dedupe_matches(matches: list[Match]) -> list[Match]:
    best: dict[str, Match] = {}
    for match in matches:
        current = best.get(match.match_key)
        if current is None:
            best[match.match_key] = match
            continue
        if current.tier != "top" and match.tier == "top":
            best[match.match_key] = match
            continue
        if match.commence_time < current.commence_time:
            best[match.match_key] = match
    return sorted(best.values(), key=lambda item: item.commence_time)


def merge_offers(settings: Settings, *maps: dict[str, list[Offer]]) -> dict[str, list[Offer]]:
    merged: dict[str, list[Offer]] = defaultdict(list)
    for mapping in maps:
        for match_key, offers in mapping.items():
            merged[match_key].extend(offers)

    result: dict[str, list[Offer]] = {}
    for match_key, offers in merged.items():
        chosen: dict[tuple[str, str, str, str, str, str], Offer] = {}
        for offer in offers:
            book = normalize_bookmaker_name(offer.bookmaker)
            selection = str(offer.selection or "").strip().lower()
            point = "" if offer.point is None else f"{float(offer.point):.2f}"
            team_side = str(offer.team_side or "")
            subtype = str(offer.market_subtype or "")
            key = (book, offer.family, selection, point, team_side, subtype)
            current = chosen.get(key)
            if current is None:
                chosen[key] = offer
                continue
            current_weight = settings.source_weight(current.source)
            new_weight = settings.source_weight(offer.source)
            if new_weight > current_weight + 1e-9:
                chosen[key] = offer
                continue
            if abs(new_weight - current_weight) <= 1e-9 and offer.price > current.price:
                chosen[key] = offer
        result[match_key] = list(chosen.values())
    return result
