from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.daily_coverage_bootstrap_restore_patch import restore_state
from app.services.daily_coverage_common import (
    EVIDENCE_PATH,
    independent_sources,
    load,
    row_key,
    row_kickoff,
    target_date,
)
from app.services.daily_coverage_identity import identity_from_key, row_identities
from scripts.day_inventory_aliases import write_current_aliases

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".data" / "exports" / "latest-daily-coverage-inventory-sync.json"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _evidence_index(
    rows: dict[str, Any],
) -> dict[tuple[str, tuple[str, str]], list[dict[str, Any]]]:
    index: dict[tuple[str, tuple[str, str]], list[dict[str, Any]]] = {}
    for key, value in rows.items():
        if not isinstance(value, dict):
            continue
        identity = identity_from_key(key)
        if identity is not None:
            index.setdefault(identity, []).append(value)
    return index


def _matching_evidence(
    row: dict[str, Any],
    rows: dict[str, Any],
    index: dict[tuple[str, tuple[str, str]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    key = row_key(row)
    direct = rows.get(key)
    if isinstance(direct, dict):
        matches.append(direct)
    kickoff = row_kickoff(row)
    for identity in row_identities(row, kickoff):
        matches.extend(index.get(identity, []))
    deduped: list[dict[str, Any]] = []
    seen: set[int] = set()
    for value in matches:
        marker = id(value)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(value)
    return deduped


def _sources(matches: list[dict[str, Any]], role: str) -> list[str]:
    values: list[str] = []
    for match in matches:
        role_rows = match.get(role)
        if isinstance(role_rows, dict):
            values.extend(str(source) for source in role_rows if source)
    return independent_sources(values, role=role)


def sync_inventory() -> dict[str, Any]:
    date_key = target_date()
    restore = restore_state()
    inventory_path = ROOT / ".data" / "day_inventory" / f"{date_key}.json"
    inventory = load(inventory_path, {})
    if not isinstance(inventory, dict):
        inventory = {}
    rows = inventory.get("matches")
    if not isinstance(rows, list) or not rows:
        report = {
            "status": "skipped",
            "reason": "day_inventory_missing_or_empty",
            "date_local": date_key,
            "inventory_path": str(inventory_path),
            "bootstrap_restore": restore,
            "publication_contract_relaxed": False,
        }
        _write_json(OUT, report)
        return report

    evidence = load(EVIDENCE_PATH, {})
    evidence_rows = (
        evidence.get("matches")
        if isinstance(evidence, dict) and isinstance(evidence.get("matches"), dict)
        else {}
    )
    index = _evidence_index(evidence_rows)
    matched = changed = 0
    with_odds = with_context = with_2plus_odds = with_2plus_context = with_both = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        evidence_matches = _matching_evidence(row, evidence_rows, index)
        evidence_odds = _sources(evidence_matches, "odds")
        evidence_context = _sources(evidence_matches, "context")
        existing_odds = independent_sources(row.get("odds_sources") or [], role="odds")
        existing_context = independent_sources(
            row.get("context_sources") or [], role="context"
        )
        odds = independent_sources(existing_odds + evidence_odds, role="odds")
        contexts = independent_sources(
            existing_context + evidence_context, role="context"
        )
        if evidence_matches:
            matched += 1
        if odds != existing_odds or contexts != existing_context:
            changed += 1
        row["odds_sources"] = odds
        row["context_sources"] = contexts

        coverage = row.get("coverage")
        if not isinstance(coverage, dict):
            coverage = {}
            row["coverage"] = coverage
        coverage["odds_sources"] = odds
        coverage["context_sources"] = contexts
        coverage["odds"] = bool(coverage.get("odds")) or bool(odds)
        coverage["context"] = bool(coverage.get("context")) or bool(contexts)
        coverage["ready_for_model"] = bool(coverage.get("ready_for_model")) or (
            bool(odds) and bool(contexts)
        )
        coverage["daily_coverage_evidence_synced"] = bool(evidence_matches)

        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            row["metadata"] = metadata
        metadata["daily_coverage_odds_sources_count"] = len(odds)
        metadata["daily_coverage_context_sources_count"] = len(contexts)
        metadata["daily_coverage_evidence_match_count"] = len(evidence_matches)

        with_odds += int(bool(odds))
        with_context += int(bool(contexts))
        with_2plus_odds += int(len(odds) >= 2)
        with_2plus_context += int(len(contexts) >= 2)
        with_both += int(len(odds) >= 2 and len(contexts) >= 2)

    inventory["updated_at_utc"] = datetime.now(UTC).isoformat()
    sources = inventory.get("sources")
    if not isinstance(sources, dict):
        sources = {}
        inventory["sources"] = sources
    sources["daily_coverage_evidence_sync"] = {
        "updated_at_utc": inventory["updated_at_utc"],
        "evidence_matches": len(evidence_rows),
        "inventory_matches_semantically_matched": matched,
        "matches_changed": changed,
    }
    _write_json(inventory_path, inventory)
    aliases = write_current_aliases(ROOT, date_key, inventory, _write_json)

    report = {
        "status": "ok",
        "date_local": date_key,
        "inventory_path": str(inventory_path),
        "inventory_matches": len(rows),
        "evidence_matches": len(evidence_rows),
        "inventory_matches_semantically_matched": matched,
        "matches_changed": changed,
        "matches_with_odds_evidence": with_odds,
        "matches_with_context_evidence": with_context,
        "matches_with_2plus_odds_sources": with_2plus_odds,
        "matches_with_2plus_context_sources": with_2plus_context,
        "matches_with_2plus_both": with_both,
        "aliases": aliases,
        "bootstrap_restore": restore,
        "publication_contract_relaxed": False,
    }
    _write_json(OUT, report)
    return report


def main() -> int:
    print(json.dumps(sync_inventory(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
