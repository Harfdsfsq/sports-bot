from __future__ import annotations

"""Keep the public 300-match inventory on rows with verified API evidence first.

Only evidence stored by real providers is counted. Provider ids are ranking hints,
never evidence. Until 300 rows are strict-ready, uncovered rows rotate through the
runtime cohort so every discovered fixture gets API attempts across successive runs.
Publication, xG, value, movement and price-integrity guards are not changed.
"""

import atexit
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.daily_coverage_common import DAY_DIR, atomic_write, evidence_path, independent_sources, load, row_key, row_kickoff, target_date
from app.services.daily_coverage_identity import identity_from_key, row_identities

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".data" / "exports" / "latest-strict-coverage-inventory-sync.json"
POOL = DAY_DIR / "coverage_candidate_pool"
TARGET = 300
_INSTALLED = False
_RUNNING = False


def _rows(payload: Any) -> list[dict[str, Any]]:
    value = payload.get("matches") if isinstance(payload, dict) else None
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _identity(row: dict[str, Any], day: str):
    values = sorted(row_identities(row, row_kickoff(row)))
    for identity in values:
        if identity[0] == day:
            return identity
    return values[0] if values else identity_from_key(row_key(row))


def _candidate_rows(day: str) -> list[dict[str, Any]]:
    paths = [DAY_DIR / f"{day}.json", DAY_DIR / "current.json", DAY_DIR / "latest.json", DAY_DIR / "today.json", DAY_DIR / "largest.json", DAY_DIR / "highwater.json", DAY_DIR / f"{day}-highwater.json", POOL / f"{day}.json"]
    cache = ROOT / ".data" / "cache" / "day_inventory"
    paths += [cache / f"{day}.json", cache / "current.json", cache / "latest.json", cache / "today.json", cache / "largest.json", cache / "highwater.json"]
    merged: dict[Any, dict[str, Any]] = {}
    for path in paths:
        payload = load(path, {})
        payload_day = str(payload.get("date_local") or payload.get("target_date") or day)[:10] if isinstance(payload, dict) else ""
        if payload_day and payload_day != day:
            continue
        for row in _rows(payload):
            key = _identity(row, day)
            if key is None or key[0] != day:
                continue
            current = merged.get(key)
            if current is None or len(str(row)) > len(str(current)):
                merged[key] = row
    return list(merged.values())


def _evidence_index(rows: dict[str, Any]):
    out: dict[Any, list[dict[str, Any]]] = {}
    for key, value in rows.items():
        identity = identity_from_key(key)
        if identity is not None and isinstance(value, dict):
            out.setdefault(identity, []).append(value)
    return out


def _match_evidence(row: dict[str, Any], rows: dict[str, Any], index: dict[Any, list[dict[str, Any]]]):
    found: list[dict[str, Any]] = []
    exact = rows.get(row_key(row))
    if isinstance(exact, dict):
        found.append(exact)
    for identity in row_identities(row, row_kickoff(row)):
        found += index.get(identity, [])
    return list({id(item): item for item in found}.values())


def _sources(found: list[dict[str, Any]], role: str) -> list[str]:
    values: list[str] = []
    for match in found:
        bucket = match.get(role)
        if isinstance(bucket, dict):
            values += [str(source) for source, entry in bucket.items() if isinstance(entry, dict)]
    return independent_sources(values, role=role)


def _books(found: list[dict[str, Any]]) -> list[str]:
    result: set[str] = set()
    for match in found:
        for entry in (match.get("odds") or {}).values() if isinstance(match.get("odds"), dict) else []:
            for offer in entry.get("data") or [] if isinstance(entry, dict) else []:
                if isinstance(offer, dict):
                    book = str(offer.get("bookmaker") or "").strip().lower()
                    if book and book not in {"unknown", "none", "null"}:
                        result.add(book)
    return sorted(result)


def _provider_hints(row: dict[str, Any]) -> int:
    values: set[str] = set()
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for bucket in (row.get("source_ids"), row.get("provider_source_ids"), metadata.get("provider_source_ids")):
        if isinstance(bucket, dict):
            values |= {str(key).lower() for key, value in bucket.items() if value}
    return len(values & {"odds_api_io", "bzzoiro", "sstats", "sportlogic", "allsportsapi"})


def _enrich(row: dict[str, Any], found: list[dict[str, Any]]):
    item = dict(row)
    odds, contexts, books = _sources(found, "odds"), _sources(found, "context"), _books(found)
    item.update(odds_sources=odds, context_sources=contexts, books=books, odds_sources_count=len(odds), context_sources_count=len(contexts), books_count=len(books))
    coverage = dict(item.get("coverage") or {})
    coverage.update(odds=bool(odds), context=bool(contexts), odds_sources=odds, context_sources=contexts, ready_for_model=bool(odds and contexts), strict_coverage_ready=len(odds) >= 2 and len(contexts) >= 2 and len(books) >= 2, daily_coverage_evidence_synced=bool(found))
    coverage.pop("ready_for_publish", None)
    item["coverage"] = coverage
    metadata = dict(item.get("metadata") or {})
    metadata.update(independent_odds_sources_count=len(odds), odds_sources_count=len(odds), context_sources_count=len(contexts), confirmation_sources_count=len(contexts), price_confirmation_sources_count=len(books), price_sources_count=len(books), books_count=len(books), verified_odds_sources=odds, verified_context_sources=contexts, verified_bookmakers=books)
    item["metadata"] = metadata
    strict = len(odds) >= 2 and len(contexts) >= 2 and len(books) >= 2
    kickoff = row_kickoff(item)
    score = (int(strict), min(len(odds), 3) + min(len(contexts), 3) + min(len(books), 4), int(len(odds) >= 2), int(len(contexts) >= 2), int(len(books) >= 2), _provider_hints(item), -(kickoff.timestamp() if kickoff else 10**15), row_key(item))
    return score, item


def _select(ranked: list[tuple[tuple[Any, ...], dict[str, Any]]]) -> tuple[list[dict[str, Any]], int]:
    strict = [pair for pair in ranked if pair[0][0] == 1]
    partial = [pair for pair in ranked if pair[0][0] == 0]
    selected = [row for _score, row in strict[:TARGET]]
    need = TARGET - len(selected)
    offset = 0
    if need > 0 and partial:
        try:
            offset = int(str(os.getenv("GITHUB_RUN_ID") or os.getenv("GITHUB_RUN_NUMBER") or "0")) % len(partial)
        except Exception:
            offset = 0
        rotated = partial[offset:] + partial[:offset]
        selected += [row for _score, row in rotated[:need]]
    return selected[:TARGET], offset


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "matches_total": len(rows),
        "matches_with_1plus_odds_sources": sum(row.get("odds_sources_count", 0) >= 1 for row in rows),
        "matches_with_1plus_context_sources": sum(row.get("context_sources_count", 0) >= 1 for row in rows),
        "matches_with_2plus_odds_sources": sum(row.get("odds_sources_count", 0) >= 2 for row in rows),
        "matches_with_2plus_context_sources": sum(row.get("context_sources_count", 0) >= 2 for row in rows),
        "matches_with_2plus_bookmakers": sum(row.get("books_count", 0) >= 2 for row in rows),
        "matches_ready_for_model": sum(row.get("odds_sources_count", 0) >= 1 and row.get("context_sources_count", 0) >= 1 for row in rows),
        "matches_a_tier_coverage_ready": sum(row.get("odds_sources_count", 0) >= 2 and row.get("context_sources_count", 0) >= 2 and row.get("books_count", 0) >= 2 for row in rows),
    }


def sync() -> dict[str, Any]:
    global _RUNNING
    if _RUNNING:
        return {"status": "skipped_reentrant"}
    _RUNNING = True
    try:
        day = target_date()
        candidates = _candidate_rows(day)
        evidence = load(evidence_path(day), {})
        evidence_rows = evidence.get("matches") if isinstance(evidence, dict) and isinstance(evidence.get("matches"), dict) else {}
        index = _evidence_index(evidence_rows)
        ranked = [_enrich(row, _match_evidence(row, evidence_rows, index)) for row in candidates]
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        selected, rotation_offset = _select(ranked)
        counts = _counts(selected)
        now = datetime.now(UTC).isoformat()
        payload = {"date_local": day, "created_at_utc": now, "updated_at_utc": now, "build_status": "strict_coverage_ranked", "target_matches": TARGET, "counts": counts, "matches": selected, "publication_contract_relaxed": False}
        POOL.mkdir(parents=True, exist_ok=True)
        atomic_write(POOL / f"{day}.json", {"date_local": day, "updated_at_utc": now, "matches": [row for _score, row in ranked], "publication_contract_relaxed": False})
        for path in (DAY_DIR / f"{day}.json", DAY_DIR / "current.json", DAY_DIR / "latest.json", DAY_DIR / "today.json"):
            atomic_write(path, payload)
        cohort = {"date_local": day, "created_at_utc": now, "updated_at_utc": now, "target_matches": TARGET, "frozen": counts["matches_a_tier_coverage_ready"] >= TARGET, "policy": "adaptive_verified_coverage_until_300_then_freeze", "rotation_offset": rotation_offset, "matches": selected, "publication_contract_relaxed": False}
        atomic_write(DAY_DIR / f"daily-coverage-cohort-{day}.json", cohort)
        atomic_write(ROOT / ".data" / "exports" / "latest-daily-coverage-cohort.json", cohort)
        report = {"status": "ok", "date_local": day, "created_at_utc": now, "candidate_pool_matches": len(candidates), "selected_matches": len(selected), "evidence_matches": len(evidence_rows), "rotation_offset": rotation_offset, "counts": counts, "strict_shortfall": max(0, TARGET - counts["matches_a_tier_coverage_ready"]), "publication_contract_relaxed": False}
        atomic_write(OUT, report)
        return report
    finally:
        _RUNNING = False


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    initial = sync()
    atexit.register(sync)
    return {"status": "installed", "initial_sync": initial, "atexit_sync": True, "publication_contract_relaxed": False}


__all__ = ["install", "sync"]
