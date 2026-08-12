from __future__ import annotations

"""Unified provider match identity and crosswalk runtime.

This patch makes provider rows attach through the canonical MatchIdentity scorer
instead of ad-hoc string keys.  It preserves how every API named and addressed the
fixture, so later odds/context enrichment can query the correct provider event id
for the same canonical match.
"""

from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from app.schemas import Match
from app.services.match_identity import MatchIdentity, best_identity_match, identity_from_match

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".data" / "exports" / "latest-unified-provider-match-identity.json"
_INSTALLED = False
_ORIGINAL_MERGE = None


def _provider_record(match: Match, provider: str | None = None) -> dict[str, Any]:
    meta = dict(getattr(match, "metadata", {}) or {})
    provider_name = str(provider or match.source or "").strip()
    source_ids = dict(meta.get("provider_source_ids") or {})
    provider_event_id = str(source_ids.get(provider_name) or match.source_event_id or "").strip()
    return {
        "provider": provider_name,
        "provider_event_id": provider_event_id,
        "home_team": str(match.home_team or ""),
        "away_team": str(match.away_team or ""),
        "league_name": str(match.league_name or ""),
        "commence_time": match.commence_time.astimezone(UTC).isoformat(),
        "match_key": str(match.match_key),
        "loose_key": str(match.loose_key),
        "query": {
            "event_id": provider_event_id,
            "home_team": str(match.home_team or ""),
            "away_team": str(match.away_team or ""),
            "date_utc": match.commence_time.astimezone(UTC).date().isoformat(),
        },
    }


def _identity_for(match: Match, provider: str | None = None) -> MatchIdentity:
    identity = identity_from_match(match, provider=provider or match.source)
    meta = dict(getattr(match, "metadata", {}) or {})
    provider_name = str(provider or match.source or "").strip()
    source_ids = dict(meta.get("provider_source_ids") or {})
    provider_event_id = str(source_ids.get(provider_name) or match.source_event_id or "").strip()
    return MatchIdentity(
        provider=provider_name,
        provider_event_id=provider_event_id,
        sport_key=identity.sport_key,
        home=identity.home,
        away=identity.away,
        league=identity.league,
        start_utc=identity.start_utc,
        raw=dict(identity.raw or {}),
    )


def _merge_metadata(existing: Match, incoming: Match, provider: str, score: Any | None = None) -> dict[str, Any]:
    meta = dict(existing.metadata or {})
    incoming_meta = dict(incoming.metadata or {})
    for key, value in incoming_meta.items():
        if key not in meta or meta[key] in (None, "", [], {}, ()):
            meta[key] = value

    source_ids = dict(meta.get("provider_source_ids") or {})
    source_ids.update(incoming_meta.get("provider_source_ids") or {})
    if incoming.source_event_id:
        source_ids[provider] = str(incoming.source_event_id)
    meta["provider_source_ids"] = {str(k): str(v) for k, v in source_ids.items() if str(k).strip() and str(v).strip()}

    existing_records = meta.get("provider_records") if isinstance(meta.get("provider_records"), list) else []
    incoming_records = incoming_meta.get("provider_records") if isinstance(incoming_meta.get("provider_records"), list) else []
    records: list[dict[str, Any]] = [r for r in [*existing_records, *incoming_records] if isinstance(r, dict)]
    records.extend([_provider_record(existing), _provider_record(incoming, provider)])
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        marker = (
            str(record.get("provider") or ""),
            str(record.get("provider_event_id") or ""),
            str(record.get("match_key") or ""),
        )
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(record)
    meta["provider_records"] = unique
    meta["provider_crosswalk"] = {str(r.get("provider")): r for r in unique if str(r.get("provider") or "").strip()}
    meta["provider_query_map"] = {
        str(r.get("provider")): dict(r.get("query") or {})
        for r in unique
        if str(r.get("provider") or "").strip()
    }
    sources_seen = {str(x).strip() for x in str(meta.get("sources_seen") or existing.source).split(",") if str(x).strip()}
    sources_seen.update(str(k) for k in meta["provider_source_ids"].keys())
    sources_seen.add(provider)
    meta["sources_seen"] = ",".join(sorted(sources_seen))
    meta["core_inventory"] = True
    meta["canonical_identity_version"] = "match_identity_v2"
    if score is not None:
        meta["last_identity_match"] = score.asdict() if hasattr(score, "asdict") else dict(score)
    return meta


def merge_matches_unified(matches_by_provider: dict[str, list[Match]], settings: Any) -> tuple[list[Match], dict[str, Any]]:
    canonical: dict[str, Match] = {}
    identity_by_key: dict[str, MatchIdentity] = {}
    crosswalk: dict[str, Any] = {
        "algorithm": "match_identity_v2",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "provider_rows": {k: len(v or []) for k, v in dict(matches_by_provider or {}).items()},
        "matched_rows": [],
        "unmatched_rows": [],
        "rejected_rows": [],
        "canonical_provider_map": {},
    }
    quality_counts: Counter[str] = Counter()
    ordered = ["odds_api_io", "bzzoiro", "sstats", "sportlogic"]
    ordered.extend(k for k in matches_by_provider.keys() if k not in ordered)

    for provider in ordered:
        for match in matches_by_provider.get(provider, []) or []:
            incoming_identity = _identity_for(match, provider)
            existing_identities = list(identity_by_key.values())
            best_identity, score = best_identity_match(incoming_identity, existing_identities)
            if best_identity is None:
                canonical_key = match.match_key
                suffix = 2
                while canonical_key in canonical:
                    canonical_key = f"{match.match_key}#provider-{provider}-{suffix}"
                    suffix += 1
                meta = _merge_metadata(match, match, provider)
                canonical[canonical_key] = Match(**{**asdict(match), "metadata": meta})
                identity_by_key[canonical_key] = _identity_for(canonical[canonical_key], provider)
                crosswalk["unmatched_rows"].append({
                    "provider": provider,
                    "provider_match_key": match.match_key,
                    "canonical_match_key": canonical_key,
                    "home": match.home_team,
                    "away": match.away_team,
                    "league": match.league_name,
                    "score": score.asdict(),
                    "reason": "new_canonical" if "no_candidates" in score.reasons else "no_safe_identity_match",
                })
                if score.quality == "reject" and "no_candidates" not in score.reasons:
                    crosswalk["rejected_rows"].append({"provider": provider, "provider_match_key": match.match_key, "score": score.asdict()})
                continue

            canonical_key = next((key for key, ident in identity_by_key.items() if ident is best_identity), "")
            if not canonical_key:
                canonical_key = next((key for key, ident in identity_by_key.items() if ident == best_identity), "")
            if not canonical_key or canonical_key not in canonical:
                canonical_key = match.match_key
                canonical[canonical_key] = match
                identity_by_key[canonical_key] = incoming_identity
                continue

            existing = canonical[canonical_key]
            meta = _merge_metadata(existing, match, provider, score)
            canonical[canonical_key] = Match(**{**asdict(existing), "metadata": meta})
            identity_by_key[canonical_key] = _identity_for(canonical[canonical_key], existing.source)
            quality_counts[score.quality] += 1
            crosswalk["matched_rows"].append({
                "provider": provider,
                "provider_match_key": match.match_key,
                "canonical_match_key": canonical_key,
                "score": score.score,
                "quality": score.quality,
                "reasons": list(score.reasons),
                "provider_event_id": str(match.source_event_id or ""),
            })

    for key, match in canonical.items():
        meta = dict(match.metadata or {})
        crosswalk["canonical_provider_map"][key] = meta.get("provider_crosswalk") or {}
    crosswalk["quality_counts"] = dict(quality_counts)
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(crosswalk, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    except Exception:
        pass
    return list(canonical.values()), crosswalk


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_MERGE
    if _INSTALLED:
        return {"installed": True, "status": "already_installed"}
    try:
        from scripts import build_day_inventory_core as core
    except Exception as exc:
        return {"installed": False, "error": f"import:{type(exc).__name__}: {exc}"}
    _ORIGINAL_MERGE = getattr(core, "merge_matches", None)
    core.merge_matches = merge_matches_unified
    _INSTALLED = True
    return {"installed": True, "patched": "scripts.build_day_inventory_core.merge_matches", "artifact": str(OUT)}


__all__ = ["install", "merge_matches_unified"]
