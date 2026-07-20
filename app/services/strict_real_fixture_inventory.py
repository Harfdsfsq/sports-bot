"""Rebuild the daily cohort from real, provider-addressable football fixtures.

Generated evidence aliases sometimes contain only a semantic key and coverage metadata.
Those rows are useful as evidence, but they cannot be sent to an API without both teams
and an exact kickoff. This module selects up to 300 unique real fixtures from the broad
discovery/alias pool before strict coverage planning starts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".data" / "exports" / "latest-strict-real-fixture-inventory.json"


def _verified_count(row: dict[str, Any], key: str) -> int:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    value = metadata.get(key)
    if isinstance(value, dict):
        return len([item for item in value if str(item).strip()])
    if isinstance(value, (list, tuple, set)):
        return len({str(item).strip().lower() for item in value if str(item).strip()})
    return 0


def _provider_hints(row: dict[str, Any]) -> int:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    values: set[str] = set()
    for bucket in (
        row.get("source_ids"),
        row.get("provider_source_ids"),
        metadata.get("provider_source_ids"),
    ):
        if isinstance(bucket, dict):
            values.update(str(key).lower() for key, value in bucket.items() if value)
    return len(
        values
        & {
            "odds_api_io",
            "bzzoiro",
            "sstats",
            "sportlogic",
            "football_data",
            "thesportsdb",
            "espn",
            "openligadb",
        }
    )


def _real_fixture(row: dict[str, Any], expander: Any) -> bool:
    home = expander.team_value(row, "home")
    away = expander.team_value(row, "away")
    kickoff = expander.parse_dt(
        row.get("kickoff_utc")
        or row.get("commence_time")
        or row.get("start_time")
        or row.get("kickoff")
        or row.get("event_date")
    )
    return bool(home and away and kickoff is not None and expander.norm(home) != expander.norm(away))


def _rank(row: dict[str, Any], expander: Any) -> tuple[Any, ...]:
    odds = _verified_count(row, "verified_odds_sources")
    contexts = _verified_count(row, "verified_context_sources")
    books = _verified_count(row, "verified_bookmakers")
    strict = int(odds >= 2 and contexts >= 2 and books >= 2)
    kickoff = expander.parse_dt(
        row.get("kickoff_utc")
        or row.get("commence_time")
        or row.get("start_time")
        or row.get("kickoff")
        or row.get("event_date")
    )
    timestamp = kickoff.timestamp() if kickoff else 10**15
    return (
        strict,
        min(odds, 3) + min(contexts, 3) + min(books, 4),
        int(odds >= 1) + int(contexts >= 1),
        _provider_hints(row),
        -timestamp,
        expander.score_row(row),
        expander.row_key(row),
    )


def rebuild() -> dict[str, Any]:
    from scripts import expand_day_inventory_to_target as expander

    day = expander.target_date()
    days = expander.horizon_days()
    target = expander.env_int(
        "DAY_INVENTORY_TARGET_SIZE",
        expander.env_int("DAY_INVENTORY_MAX_MATCHES", 300, 1),
        1,
    )
    merged: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    rejected_identity_only = 0
    parse_errors: list[str] = []

    for path in expander.candidate_paths(day):
        payload = expander.load_json(path, None)
        if payload is None:
            if path.exists():
                parse_errors.append(str(path))
            continue
        accepted = 0
        for raw in expander.rows_from_payload(payload):
            if not isinstance(raw, dict) or not expander.in_horizon(raw, day, days):
                continue
            if not _real_fixture(raw, expander):
                rejected_identity_only += 1
                continue
            key = expander.row_key(raw)
            if not key:
                continue
            row = dict(raw)
            row.setdefault("semantic_match_key", key)
            row["real_fixture_identity_verified"] = True
            row["real_fixture_inventory_rebuilt_at_utc"] = datetime.now(UTC).isoformat()
            merged[key] = (
                expander.merge_row(merged[key], row) if key in merged else row
            )
            accepted += 1
        if accepted:
            source_counts[str(path)] = accepted

    ranked = sorted(merged.values(), key=lambda row: _rank(row, expander), reverse=True)
    selected = ranked[:target]
    now = datetime.now(UTC).isoformat()
    payload = {
        "date_local": day,
        "created_at_utc": now,
        "updated_at_utc": now,
        "build_status": "strict_real_fixture_inventory",
        "inventory_horizon_days": days,
        "target_matches": target,
        "counts": {
            "matches_total": len(selected),
            "real_fixture_candidates": len(ranked),
            "identity_only_rows_rejected": rejected_identity_only,
            "target_shortfall": max(0, target - len(selected)),
        },
        "matches": selected,
        "publication_contract_relaxed": False,
    }
    changed_paths = expander.write_aliases(payload, day)
    highwater_paths = expander.write_highwater(payload, day)
    report = {
        "status": "ok_target_met" if len(selected) >= target else "partial_real_fixtures",
        "created_at_utc": now,
        "date_local": day,
        "horizon_days": days,
        "target": target,
        "real_fixture_candidates": len(ranked),
        "selected_real_fixtures": len(selected),
        "target_shortfall": max(0, target - len(selected)),
        "identity_only_rows_rejected": rejected_identity_only,
        "source_counts": source_counts,
        "parse_errors": parse_errors[:30],
        "changed_paths": changed_paths,
        "highwater_paths": highwater_paths,
        "publication_contract_relaxed": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix(OUT.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(OUT)
    return report


__all__ = ["rebuild"]
