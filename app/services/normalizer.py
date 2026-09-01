from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Any

from app.schemas import Match, Offer


def _provider_record(match: Match) -> dict[str, Any]:
    return {
        "provider": str(match.source or ""),
        "provider_event_id": str(match.source_event_id or ""),
        "home_team": str(match.home_team or ""),
        "away_team": str(match.away_team or ""),
        "league_name": str(match.league_name or ""),
        "commence_time": match.commence_time.isoformat(),
    }


def _merge_duplicate_matches(existing: Match, current: Match) -> Match:
    """Keep one canonical row without discarding provider evidence.

    The previous implementation replaced the whole Match when the incoming row
    had more metadata. That made later matching impossible to audit and dropped
    provider event IDs from the discarded row. Keep the established primary row
    while merging all provenance into metadata.
    """
    existing_metadata = dict(getattr(existing, "metadata", {}) or {})
    current_metadata = dict(getattr(current, "metadata", {}) or {})

    # Preserve the previous preference for a richer row and the Bookies bootstrap
    # source, but never lose metadata from either provider.
    preferred = current if len(current_metadata) > len(existing_metadata) else existing
    if current.source == "bookies_api" and existing.source != "bookies_api":
        preferred = current

    metadata: dict[str, Any] = dict(existing_metadata)
    for key, value in current_metadata.items():
        if key not in metadata or metadata[key] in (None, "", [], {}, ()):
            metadata[key] = value

    records: list[dict[str, Any]] = []
    for candidate in (
        existing_metadata.get("provider_records"),
        current_metadata.get("provider_records"),
    ):
        if isinstance(candidate, list):
            records.extend(item for item in candidate if isinstance(item, dict))
    records.extend([_provider_record(existing), _provider_record(current)])
    unique_records: list[dict[str, Any]] = []
    seen_records: set[tuple[str, str]] = set()
    for record in records:
        key = (str(record.get("provider") or ""), str(record.get("provider_event_id") or ""))
        if key in seen_records:
            continue
        seen_records.add(key)
        unique_records.append(record)

    source_ids: dict[str, str] = {}
    for record in unique_records:
        provider = str(record.get("provider") or "").strip()
        event_id = str(record.get("provider_event_id") or "").strip()
        if provider and event_id:
            source_ids[provider] = event_id
    metadata["provider_records"] = unique_records
    metadata["provider_source_ids"] = source_ids
    metadata["sources_seen"] = sorted({
        str(record.get("provider") or "").strip()
        for record in unique_records
        if str(record.get("provider") or "").strip()
    })
    return replace(preferred, metadata=metadata)


def dedupe_matches(matches: list[Match]) -> list[Match]:
    best: dict[str, Match] = {}
    for match in matches:
        existing = best.get(match.match_key)
        if existing is None:
            best[match.match_key] = match
            continue
        best[match.match_key] = _merge_duplicate_matches(existing, match)

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
