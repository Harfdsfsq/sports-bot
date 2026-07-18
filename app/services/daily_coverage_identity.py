from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.services.daily_coverage_common import app_timezone
from app.utils import canonicalize_team_name, parse_datetime

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def team_pair(home: Any, away: Any) -> tuple[str, str] | None:
    first = canonicalize_team_name(str(home or ""))
    second = canonicalize_team_name(str(away or ""))
    if not first or not second:
        return None
    return tuple(sorted((first, second)))


def identity_from_key(value: Any) -> tuple[str, tuple[str, str]] | None:
    parts = [part.strip() for part in str(value or "").split("|")]
    if len(parts) >= 3 and _DATE_RE.fullmatch(parts[0]):
        pair = team_pair(parts[1], parts[2])
        return (parts[0], pair) if pair else None
    if len(parts) >= 4 and _DATE_RE.fullmatch(parts[-1]):
        pair = team_pair(parts[1], parts[2])
        return (parts[-1], pair) if pair else None
    return None


def row_identities(row: dict[str, Any], kickoff: datetime | None = None) -> set[tuple[str, tuple[str, str]]]:
    identities: set[tuple[str, tuple[str, str]]] = set()
    for key_name in (
        "match_key",
        "canonical_match_id",
        "semantic_match_key",
        "day_inventory_match_key",
    ):
        identity = identity_from_key(row.get(key_name))
        if identity:
            identities.add(identity)
    pair = team_pair(row.get("home_team"), row.get("away_team"))
    if pair is None:
        return identities
    value = kickoff or row.get("commence_time") or row.get("kickoff_utc") or row.get("start_time")
    try:
        parsed = parse_datetime(value) if value is not None else None
    except Exception:
        parsed = None
    if parsed is not None:
        identities.add((parsed.astimezone(UTC).date().isoformat(), pair))
        identities.add((parsed.astimezone(app_timezone()).date().isoformat(), pair))
    return identities


def build_ledger_identity_index(ledger_matches: dict[str, Any]) -> dict[tuple[str, tuple[str, str]], list[dict[str, Any]]]:
    index: dict[tuple[str, tuple[str, str]], list[dict[str, Any]]] = {}
    for key, value in ledger_matches.items():
        if not isinstance(value, dict):
            continue
        identity = identity_from_key(key)
        if identity is not None:
            index.setdefault(identity, []).append(value)
    return index


def merge_ledger_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    merged: dict[str, Any] = {}
    odds_sources: list[str] = []
    context_sources: list[str] = []
    for row in rows:
        odds_sources.extend(str(value) for value in row.get("odds_sources") or [] if value)
        context_sources.extend(str(value) for value in row.get("context_sources") or [] if value)
        for key, value in row.items():
            if key not in {"odds_sources", "context_sources"} and value not in (None, "", [], {}):
                merged[key] = value
    merged["odds_sources"] = odds_sources
    merged["context_sources"] = context_sources
    return merged


def lookup_ledger_row(
    row: dict[str, Any],
    key: str,
    kickoff: datetime | None,
    ledger_matches: dict[str, Any],
    identity_index: dict[tuple[str, tuple[str, str]], list[dict[str, Any]]],
) -> dict[str, Any]:
    exact = ledger_matches.get(key)
    candidates: list[dict[str, Any]] = [exact] if isinstance(exact, dict) else []
    for identity in row_identities(row, kickoff):
        candidates.extend(identity_index.get(identity, []))
    deduped: list[dict[str, Any]] = []
    seen: set[int] = set()
    for candidate in candidates:
        marker = id(candidate)
        if marker not in seen:
            seen.add(marker)
            deduped.append(candidate)
    return merge_ledger_rows(deduped)


__all__ = [
    "build_ledger_identity_index",
    "identity_from_key",
    "lookup_ledger_row",
    "merge_ledger_rows",
    "row_identities",
    "team_pair",
]
