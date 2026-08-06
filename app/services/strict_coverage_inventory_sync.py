from __future__ import annotations

"""Select and rotate the daily 300 by verified independent API evidence.

Fixture ids and aliases are targeting hints only. Until 300 rows are strict-ready,
high-potential uncovered rows stay in the cohort while a rotating exploration slice
lets every discovered fixture receive API attempts. Publication, xG, value, movement
and price-integrity guards are unchanged.
"""

import atexit
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.daily_coverage_common import (
    DAY_DIR,
    atomic_write,
    evidence_path,
    independent_sources,
    load,
    row_key,
    row_kickoff,
    target_date,
)
from app.services.daily_coverage_identity import identity_from_key, row_identities

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".data" / "exports" / "latest-strict-coverage-inventory-sync.json"
POOL = DAY_DIR / "coverage_candidate_pool"
TARGET = 300
_INSTALLED = False
_RUNNING = False

# These are independent football/team context APIs suitable for strict coverage.
# Weather/news may enrich a model later, but cannot satisfy the 2-context contract.
CORE_CONTEXT_SOURCES = {
    "sstats",
    "bzzoiro",
    "clubelo",
    "sportlogic",
    "football_data",
    "thesportsdb",
    "api_football",
    "espn",
    "openligadb",
    "openfootball",
    "futrixmetrics",
}

# Compatibility patches historically raised SportLogic above its documented daily
# budget and reduced SStats/Bzzoiro to shortlist-only operation. RuntimePreflight
# reapplies this table after every native installer and before Settings is created.
FINAL_ENV = {
    "RUNBOT_DISCOVERY_FIRST_FORCE_FULL_REFRESH": "true",
    "RUNBOT_DISCOVERY_FIRST_FULL_REFRESH_INTERVAL_MINUTES": "15",
    "RUNBOT_INCREMENTAL_DEEP_ENRICHMENT_ENABLED": "true",
    "RUNBOT_INCREMENTAL_BZZOIRO_GAP_ENRICHMENT_ENABLED": "true",
    "DAY_INVENTORY_TARGET_SIZE": "300",
    "DAY_INVENTORY_MAX_MATCHES": "300",
    "DAY_INVENTORY_FORCE_TOP_300": "true",
    "DAY_INVENTORY_FORCE_FULL_300": "true",
    "DAY_INVENTORY_FORCE_ALIAS_SHRINK": "true",
    "MAX_MATCHES_FOR_ODDS_FETCH": "300",
    "ANALYSIS_MATCH_CAP_PER_RUN": "300",
    "CONTEXT_ENRICHMENT_MATCH_LIMIT": "300",
    "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
    "ODDS_API_IO_MAX_REQUESTS_PER_RUN": "200",
    "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": "200",
    "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": "100",
    "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": "100",
    "ODDS_API_IO_FETCH_FULL_DAY_INVENTORY": "true",
    "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": "150",
    "SSTATS_MAX_REQUESTS_PER_RUN": "150",
    "SSTATS_CONTEXT_MATCH_LIMIT": "300",
    "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "150",
    "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": "300",
    "SSTATS_ODDS_RESCUE_LIMIT_PER_RUN": "300",
    "SSTATS_PARI_DETAIL_MATCH_LIMIT": "300",
    "SSTATS_CURRENT_ODDS_AS_LINE_SOURCE": "false",
    "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": "200",
    "BZZOIRO_MAX_REQUESTS_PER_RUN": "200",
    "BZZOIRO_REQUESTS_MAX_PER_RUN": "200",
    "BZZOIRO_REQUEST_BUDGET_GRANTED": "200",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "300",
    "BZZOIRO_ODDS_MATCH_LIMIT": "300",
    "BZZOIRO_V2_MATCH_LIMIT": "300",
    "BZZOIRO_V2_MAX_HTTP_REQUESTS_PER_RUN": "200",
    "BZZOIRO_V2_FETCH_EVENT_ODDS": "true",
    "BZZOIRO_V2_FETCH_ODDS_COMPARISON": "true",
    "BZZOIRO_ODDS_COMPARISON_AS_SECONDARY_OFFERS": "true",
    "BZZOIRO_ODDS_MATCH_COUNTS_AS_EVENT_CONTEXT": "false",
    "ALLSPORTSAPI_ONLY_IF_PRIMARY_ODDS_EMPTY": "false",
    "ALLSPORTSAPI_MATCH_LIMIT": "300",
    "ALLSPORTSAPI_PER_RUN_MAX": "96",
    "ALLSPORTSAPI_MAX_HTTP_REQUESTS_PER_RUN": "96",
    "SPORTLOGIC_PER_RUN_MAX": "30",
    "SPORTLOGIC_MAX_REQUESTS_PER_RUN": "30",
    "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "30",
    "SPORTLOGIC_REQUESTS_MAX_PER_RUN": "30",
    "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "30",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "30",
    "PUBLISH_MIN_BOOKS": "2",
    "MIN_BOOKS_PUBLISH": "2",
    "PUBLISH_MIN_ODDS_SOURCES": "1",
    "PUBLISH_MIN_CONTEXT_SOURCES": "2",
    "PUBLISH_TIER_A_MIN_BOOKS": "2",
    "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2",
    "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "PUBLISH_TIER_B_MIN_BOOKS": "2",
    "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "1",
    "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "false",
}


def _reassert_final_env() -> bool:
    for key, value in FINAL_ENV.items():
        os.environ[key] = value
    try:
        from app.services import runtime_preflight

        runtime_preflight.AUTONOMOUS_ACCUMULATION_POLICY.update(FINAL_ENV)
        return True
    except Exception:
        return False


def _rows(payload: Any) -> list[dict[str, Any]]:
    value = payload.get("matches") if isinstance(payload, dict) else None
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _identity(row: dict[str, Any], day: str):
    identities = sorted(row_identities(row, row_kickoff(row)))
    for identity in identities:
        if identity[0] == day:
            return identity
    return identities[0] if identities else identity_from_key(row_key(row))


def _candidate_paths(day: str) -> list[Path]:
    paths = [POOL / f"{day}.json"]
    for base in (DAY_DIR, ROOT / ".data" / "cache" / "day_inventory"):
        paths.extend(
            base / name
            for name in (
                f"{day}.json",
                "current.json",
                "latest.json",
                "today.json",
                "largest.json",
                "highwater.json",
                "best-day-inventory-highwater.json",
                f"{day}-highwater.json",
            )
        )
        try:
            paths.extend(sorted(base.glob("*highwater*.json")))
        except Exception:
            pass
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        marker = str(path)
        if marker not in seen:
            seen.add(marker)
            out.append(path)
    return out


def _candidate_rows(day: str) -> list[dict[str, Any]]:
    merged: dict[Any, dict[str, Any]] = {}
    for path in _candidate_paths(day):
        payload = load(path, {})
        payload_day = (
            str(payload.get("date_local") or payload.get("target_date") or day)[:10]
            if isinstance(payload, dict)
            else ""
        )
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


def _match_evidence(
    row: dict[str, Any],
    rows: dict[str, Any],
    index: dict[Any, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    exact = rows.get(row_key(row))
    if isinstance(exact, dict):
        found.append(exact)
    for identity in row_identities(row, row_kickoff(row)):
        found.extend(index.get(identity, []))
    return list({id(item): item for item in found}.values())


def _sources(found: list[dict[str, Any]], role: str) -> list[str]:
    values: list[str] = []
    for match in found:
        bucket = match.get(role)
        if isinstance(bucket, dict):
            values.extend(
                str(source)
                for source, entry in bucket.items()
                if isinstance(entry, dict)
            )
    sources = independent_sources(values, role=role)
    if role == "context":
        sources = [source for source in sources if source in CORE_CONTEXT_SOURCES]
    return sources


def _books(found: list[dict[str, Any]]) -> list[str]:
    result: set[str] = set()
    for match in found:
        odds = match.get("odds")
        if not isinstance(odds, dict):
            continue
        for entry in odds.values():
            data = entry.get("data") if isinstance(entry, dict) else None
            for offer in data if isinstance(data, list) else []:
                if not isinstance(offer, dict):
                    continue
                book = str(offer.get("bookmaker") or "").strip().lower()
                if book and book not in {"unknown", "none", "null"}:
                    result.add(book)
    return sorted(result)


def _provider_hints(row: dict[str, Any]) -> int:
    values: set[str] = set()
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for bucket in (
        row.get("source_ids"),
        row.get("provider_source_ids"),
        metadata.get("provider_source_ids"),
    ):
        if isinstance(bucket, dict):
            values.update(str(key).lower() for key, value in bucket.items() if value)
    return len(values & {"odds_api_io", "bzzoiro", "sstats", "sportlogic", "allsportsapi"})


def _enrich(row: dict[str, Any], found: list[dict[str, Any]]):
    item = dict(row)
    odds = _sources(found, "odds")
    contexts = _sources(found, "context")
    books = _books(found)
    item.update(
        odds_sources=odds,
        context_sources=contexts,
        books=books,
        odds_sources_count=len(odds),
        context_sources_count=len(contexts),
        books_count=len(books),
    )
    strict = len(odds) >= 2 and len(contexts) >= 2 and len(books) >= 2
    coverage = dict(item.get("coverage") or {})
    coverage.update(
        odds=bool(odds),
        context=bool(contexts),
        odds_sources=odds,
        context_sources=contexts,
        ready_for_model=bool(odds and contexts),
        strict_coverage_ready=strict,
        daily_coverage_evidence_synced=bool(found),
    )
    coverage.pop("ready_for_publish", None)
    item["coverage"] = coverage
    metadata = dict(item.get("metadata") or {})
    metadata.update(
        independent_odds_sources_count=len(odds),
        odds_sources_count=len(odds),
        context_sources_count=len(contexts),
        confirmation_sources_count=len(contexts),
        price_confirmation_sources_count=len(books),
        price_sources_count=len(books),
        books_count=len(books),
        verified_odds_sources=odds,
        verified_context_sources=contexts,
        verified_bookmakers=books,
    )
    item["metadata"] = metadata
    kickoff = row_kickoff(item)
    score = (
        int(strict),
        min(len(odds), 3) + min(len(contexts), 3) + min(len(books), 4),
        int(len(odds) >= 2),
        int(len(contexts) >= 2),
        int(len(books) >= 2),
        _provider_hints(item),
        -(kickoff.timestamp() if kickoff else 10**15),
        row_key(item),
    )
    return score, item


def _run_number() -> int:
    try:
        return int(str(os.getenv("GITHUB_RUN_ID") or os.getenv("GITHUB_RUN_NUMBER") or "0"))
    except Exception:
        return 0


def _select(
    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]],
) -> tuple[list[dict[str, Any]], int]:
    strict = [pair for pair in ranked if pair[0][0] == 1]
    partial = [pair for pair in ranked if pair[0][0] == 0]
    selected = [row for _score, row in strict[:TARGET]]
    need = TARGET - len(selected)
    offset = 0
    if need > 0 and partial:
        # Keep two thirds of the best partial rows so repeated API calls can finish
        # their evidence, and rotate one third to explore the rest of the large pool.
        stable_count = min(need, (need * 2) // 3)
        selected.extend(row for _score, row in partial[:stable_count])
        explore_need = need - stable_count
        exploration = partial[stable_count:]
        if explore_need > 0 and exploration:
            offset = _run_number() % len(exploration)
            rotated = exploration[offset:] + exploration[:offset]
            selected.extend(row for _score, row in rotated[:explore_need])
    return selected[:TARGET], offset


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "matches_total": len(rows),
        "matches_with_1plus_odds_sources": sum(row.get("odds_sources_count", 0) >= 1 for row in rows),
        "matches_with_1plus_context_sources": sum(row.get("context_sources_count", 0) >= 1 for row in rows),
        "matches_with_2plus_odds_sources": sum(row.get("odds_sources_count", 0) >= 2 for row in rows),
        "matches_with_2plus_context_sources": sum(row.get("context_sources_count", 0) >= 2 for row in rows),
        "matches_with_2plus_bookmakers": sum(row.get("books_count", 0) >= 2 for row in rows),
        "matches_ready_for_model": sum(
            row.get("odds_sources_count", 0) >= 1
            and row.get("context_sources_count", 0) >= 1
            for row in rows
        ),
        "matches_a_tier_coverage_ready": sum(
            row.get("odds_sources_count", 0) >= 2
            and row.get("context_sources_count", 0) >= 2
            and row.get("books_count", 0) >= 2
            for row in rows
        ),
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
        evidence_rows = (
            evidence.get("matches")
            if isinstance(evidence, dict) and isinstance(evidence.get("matches"), dict)
            else {}
        )
        index = _evidence_index(evidence_rows)
        ranked = [
            _enrich(row, _match_evidence(row, evidence_rows, index))
            for row in candidates
        ]
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        selected, rotation_offset = _select(ranked)
        counts = _counts(selected)
        now = datetime.now(UTC).isoformat()
        payload = {
            "date_local": day,
            "created_at_utc": now,
            "updated_at_utc": now,
            "build_status": "strict_coverage_ranked",
            "target_matches": TARGET,
            "counts": counts,
            "matches": selected,
            "publication_contract_relaxed": False,
        }
        POOL.mkdir(parents=True, exist_ok=True)
        atomic_write(
            POOL / f"{day}.json",
            {
                "date_local": day,
                "updated_at_utc": now,
                "matches": [row for _score, row in ranked],
                "publication_contract_relaxed": False,
            },
        )
        for path in (
            DAY_DIR / f"{day}.json",
            DAY_DIR / "current.json",
            DAY_DIR / "latest.json",
            DAY_DIR / "today.json",
        ):
            atomic_write(path, payload)
        cohort = {
            "date_local": day,
            "created_at_utc": now,
            "updated_at_utc": now,
            "target_matches": TARGET,
            "frozen": counts["matches_a_tier_coverage_ready"] >= TARGET,
            "policy": "adaptive_verified_coverage_until_300_then_freeze",
            "rotation_offset": rotation_offset,
            "matches": selected,
            "publication_contract_relaxed": False,
        }
        atomic_write(DAY_DIR / f"daily-coverage-cohort-{day}.json", cohort)
        atomic_write(ROOT / ".data" / "exports" / "latest-daily-coverage-cohort.json", cohort)
        report = {
            "status": "ok",
            "date_local": day,
            "created_at_utc": now,
            "candidate_pool_matches": len(candidates),
            "selected_matches": len(selected),
            "evidence_matches": len(evidence_rows),
            "rotation_offset": rotation_offset,
            "counts": counts,
            "strict_shortfall": max(0, TARGET - counts["matches_a_tier_coverage_ready"]),
            "publication_contract_relaxed": False,
        }
        atomic_write(OUT, report)
        return report
    finally:
        _RUNNING = False


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    policy_reapply_updated = _reassert_final_env()
    initial = sync()
    atexit.register(sync)
    return {
        "status": "installed",
        "initial_sync": initial,
        "atexit_sync": True,
        "runtime_preflight_policy_reapply_updated": policy_reapply_updated,
        "publication_contract_relaxed": False,
    }


__all__ = [
    "CORE_CONTEXT_SOURCES",
    "FINAL_ENV",
    "install",
    "sync",
]
