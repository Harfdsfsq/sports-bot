from __future__ import annotations

import contextlib
import os
import threading
from datetime import UTC, datetime
from typing import Any

from app.services.daily_coverage_common import (
    CONTEXT_SOURCE_ALLOWLIST,
    EVIDENCE_PATH,
    LEDGER_PATH,
    ODDS_SOURCE_ALLOWLIST,
    atomic_write,
    canonical_source,
    evidence_path,
    independent_sources,
    ledger_path,
    load,
    target_date,
)

_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINAL_RECORD = None


def _normalize_actual_source(value: Any) -> str:
    source = canonical_source(value)
    prefix_map = (
        ("odds_api_io", "odds_api_io"),
        ("sstats_pari", "sstats_pari"),
        ("sstats", "sstats"),
        ("bzzoiro", "bzzoiro"),
        ("clubelo", "clubelo"),
        ("sportlogic", "sportlogic"),
        ("football_data", "football_data"),
        ("thesportsdb", "thesportsdb"),
        ("openligadb", "openligadb"),
        ("openfootball", "openfootball"),
        ("api_football", "api_football"),
    )
    for prefix, normalized in prefix_map:
        if source == prefix or source.startswith(prefix + "_"):
            return normalized
    return source


def _source_from_context(value: Any, fallback: str) -> str:
    source = _normalize_actual_source(getattr(value, "source", None))
    if not source and isinstance(value, dict):
        source = _normalize_actual_source(value.get("source"))
    return source or _normalize_actual_source(fallback)


def _sources_from_offers(value: Any, fallback: str) -> list[str]:
    sources: list[str] = []
    for item in list(value or []):
        source = _normalize_actual_source(getattr(item, "source", None))
        if not source and isinstance(item, dict):
            source = _normalize_actual_source(item.get("source"))
        if source:
            sources.append(source)
    return independent_sources(sources or [fallback], role="odds")


def _actual_sources(provider_name: str, role: str, value: Any) -> list[str]:
    fallback = _normalize_actual_source(provider_name)
    if role == "odds":
        return [source for source in _sources_from_offers(value, fallback) if source in ODDS_SOURCE_ALLOWLIST]
    source = _source_from_context(value, fallback)
    return [source] if source in CONTEXT_SOURCE_ALLOWLIST else []


def _entry_source(provider_name: str, role: str, entry: Any) -> list[str]:
    if not isinstance(entry, dict):
        return []
    return _actual_sources(provider_name, role, entry.get("data"))


def _newest(left: dict[str, Any] | None, right: dict[str, Any]) -> dict[str, Any]:
    if not left:
        return right
    return right if str(right.get("updated_at_utc") or "") >= str(left.get("updated_at_utc") or "") else left


def repair_state() -> dict[str, Any]:
    date_key = target_date()
    with _LOCK:
        evidence = load(evidence_path(date_key), {})
        ledger = load(ledger_path(date_key), {})
        if not isinstance(evidence, dict) or evidence.get("date_local") != date_key:
            evidence = {"date_local": date_key, "matches": {}}
        if not isinstance(ledger, dict) or ledger.get("date_local") != date_key:
            ledger = {"date_local": date_key, "matches": {}, "runs": []}
        evidence_rows = evidence.setdefault("matches", {})
        ledger_rows = ledger.setdefault("matches", {})
        moved = removed = rebuilt = 0
        for match_key, match_evidence in list(evidence_rows.items()):
            if not isinstance(match_evidence, dict):
                evidence_rows.pop(match_key, None)
                removed += 1
                continue
            row = ledger_rows.setdefault(match_key, {})
            for role, field in (("odds", "odds_sources"), ("context", "context_sources")):
                old = match_evidence.get(role)
                normalized: dict[str, dict[str, Any]] = {}
                if isinstance(old, dict):
                    for provider_name, entry in old.items():
                        if not isinstance(entry, dict):
                            removed += 1
                            continue
                        actual = _entry_source(str(provider_name), role, entry)
                        if not actual:
                            removed += 1
                            continue
                        for source in actual:
                            if canonical_source(provider_name) != source:
                                moved += 1
                            normalized[source] = _newest(normalized.get(source), entry)
                match_evidence[role] = normalized
                sources = independent_sources(normalized.keys(), role=role)
                if list(row.get(field) or []) != sources:
                    rebuilt += 1
                row[field] = sources
        now = datetime.now(UTC).isoformat()
        evidence["updated_at_utc"] = now
        ledger["updated_at_utc"] = now
        atomic_write(evidence_path(date_key), evidence)
        atomic_write(EVIDENCE_PATH, evidence)
        atomic_write(ledger_path(date_key), ledger)
        atomic_write(LEDGER_PATH, ledger)
        return {
            "status": "repaired",
            "date_local": date_key,
            "matches": len(evidence_rows),
            "entries_moved_to_actual_source": moved,
            "entries_removed_non_independent": removed,
            "ledger_rows_rebuilt": rebuilt,
            "publication_contract_relaxed": False,
        }


def _record_provider_result(provider_name: str, method_name: str, data: Any, stats: Any = None) -> None:
    if not isinstance(data, dict):
        return
    from app.services import daily_coverage_ledger as ledger_module

    role = "odds" if "offer" in method_name.lower() else "context"
    call_provider = canonical_source(provider_name)
    now = datetime.now(UTC)
    date_key = target_date()
    grouped: dict[str, dict[str, Any]] = {}
    for match_key, value in data.items():
        if not value:
            continue
        sources = _actual_sources(call_provider, role, value)
        for source in sources:
            if role == "odds":
                selected = [
                    item
                    for item in list(value or [])
                    if _normalize_actual_source(getattr(item, "source", None)) == source
                    or (
                        isinstance(item, dict)
                        and _normalize_actual_source(item.get("source")) == source
                    )
                ]
                if selected:
                    grouped.setdefault(source, {})[str(match_key)] = selected
            else:
                grouped.setdefault(source, {})[str(match_key)] = value
    if not grouped:
        return
    with _LOCK:
        ledger = load(ledger_path(date_key), {})
        evidence = load(evidence_path(date_key), {})
        if not isinstance(ledger, dict) or ledger.get("date_local") != date_key:
            ledger = {"date_local": date_key, "matches": {}, "runs": []}
        if not isinstance(evidence, dict) or evidence.get("date_local") != date_key:
            evidence = {"date_local": date_key, "matches": {}}
        match_rows = ledger.setdefault("matches", {})
        evidence_rows = evidence.setdefault("matches", {})
        field = "odds_sources" if role == "odds" else "context_sources"
        matched_keys: set[str] = set()
        for source, source_data in grouped.items():
            for match_key, value in source_data.items():
                matched_keys.add(match_key)
                row = match_rows.setdefault(match_key, {})
                row[field] = independent_sources(list(row.get(field) or []) + [source], role=role)
                row[f"last_{source}_{role}_observed_at_utc"] = now.isoformat()
                compact = (
                    ledger_module._compact_offers(value)
                    if role == "odds"
                    else ledger_module._compact_context(value)
                )
                if compact:
                    evidence_rows.setdefault(match_key, {}).setdefault(role, {})[source] = {
                        "updated_at_utc": now.isoformat(),
                        "data": compact,
                    }
        run_id = str(os.getenv("GITHUB_RUN_ID") or "")
        run_row = {
            "run_id": run_id,
            "provider": call_provider,
            "actual_sources": sorted(grouped),
            "method": method_name,
            "role": role,
            "matched": len(matched_keys),
            "observed_at_utc": now.isoformat(),
            "stats": ledger_module._serialize(stats or {}),
        }
        runs = [
            item
            for item in list(ledger.get("provider_runs") or [])
            if not (
                str(item.get("run_id") or "") == run_id
                and canonical_source(item.get("provider")) == call_provider
                and str(item.get("method") or "") == method_name
            )
        ]
        runs.append(run_row)
        ledger["provider_runs"] = runs[-128:]
        ledger["updated_at_utc"] = evidence["updated_at_utc"] = now.isoformat()
        atomic_write(ledger_path(date_key), ledger)
        atomic_write(LEDGER_PATH, ledger)
        atomic_write(evidence_path(date_key), evidence)
        atomic_write(EVIDENCE_PATH, evidence)


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_RECORD
    if _INSTALLED:
        return {"status": "already_installed"}
    from app.services import daily_coverage_ledger as ledger_module

    _ORIGINAL_RECORD = ledger_module.record_provider_result
    ledger_module.record_provider_result = _record_provider_result
    with contextlib.suppress(Exception):
        from app.services import daily_coverage_runtime_boundary as boundary_module

        boundary_module.record_provider_result = _record_provider_result
    repair = repair_state()
    _INSTALLED = True
    return {
        "status": "installed",
        "actual_offer_source_persisted": True,
        "actual_context_source_persisted": True,
        "duplicate_provider_run_rows_collapsed": True,
        "state_repair": repair,
        "publication_contract_relaxed": False,
    }


__all__ = ["install", "repair_state"]
