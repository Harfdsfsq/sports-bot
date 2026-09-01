from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.daily_coverage_common import (
    EVIDENCE_PATH,
    LEDGER_PATH,
    atomic_write,
    evidence_path,
    independent_sources,
    ledger_path,
    load,
    target_date,
)


def _valid(payload: Any, date_key: str) -> bool:
    return isinstance(payload, dict) and str(payload.get("date_local") or "") == date_key


def _newest(left: dict[str, Any] | None, right: dict[str, Any]) -> dict[str, Any]:
    if left is None:
        return dict(right)
    left_at = str(left.get("updated_at_utc") or "")
    right_at = str(right.get("updated_at_utc") or "")
    return dict(right) if right_at >= left_at else dict(left)


def _merge_evidence(date_key: str, payloads: list[Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {"date_local": date_key, "matches": {}}
    rows = merged["matches"]
    for payload in payloads:
        if not _valid(payload, date_key):
            continue
        for match_key, match_row in (payload.get("matches") or {}).items():
            if not isinstance(match_row, dict):
                continue
            target = rows.setdefault(str(match_key), {})
            for role in ("odds", "context"):
                role_rows = match_row.get(role)
                if not isinstance(role_rows, dict):
                    continue
                target_role = target.setdefault(role, {})
                for source, entry in role_rows.items():
                    if not isinstance(entry, dict):
                        continue
                    key = str(source)
                    target_role[key] = _newest(target_role.get(key), entry)
        updated = str(payload.get("updated_at_utc") or "")
        if updated >= str(merged.get("updated_at_utc") or ""):
            merged["updated_at_utc"] = updated
    merged["updated_at_utc"] = str(
        merged.get("updated_at_utc") or datetime.now(UTC).isoformat()
    )
    return merged


def _merge_rows(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if key in {"odds_sources", "context_sources"}:
            continue
        if value not in (None, "", [], {}):
            merged[key] = value
    merged["odds_sources"] = independent_sources(
        list(left.get("odds_sources") or []) + list(right.get("odds_sources") or []),
        role="odds",
    )
    merged["context_sources"] = independent_sources(
        list(left.get("context_sources") or [])
        + list(right.get("context_sources") or []),
        role="context",
    )
    return merged


def _dedupe_runs(
    rows: list[Any], fields: tuple[str, ...], limit: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    positions: dict[tuple[str, ...], int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = tuple(str(row.get(field) or "") for field in fields)
        if key in positions:
            out[positions[key]] = dict(row)
        else:
            positions[key] = len(out)
            out.append(dict(row))
    return out[-limit:]


def _merge_ledger(
    date_key: str, payloads: list[Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "date_local": date_key,
        "matches": {},
        "runs": [],
        "provider_runs": [],
    }
    all_runs: list[Any] = []
    all_provider_runs: list[Any] = []
    for payload in payloads:
        if not _valid(payload, date_key):
            continue
        for key, value in payload.items():
            if key in {"matches", "runs", "provider_runs", "date_local"}:
                continue
            if value not in (None, "", [], {}):
                merged[key] = value
        for match_key, row in (payload.get("matches") or {}).items():
            if not isinstance(row, dict):
                continue
            current = merged["matches"].get(str(match_key), {})
            merged["matches"][str(match_key)] = _merge_rows(current, row)
        all_runs.extend(payload.get("runs") or [])
        all_provider_runs.extend(payload.get("provider_runs") or [])

    # Evidence is the source of truth for independent-source membership. This
    # rebuild repairs checkout/cache divergence before the daily plan runs.
    for match_key, match_evidence in (evidence.get("matches") or {}).items():
        if not isinstance(match_evidence, dict):
            continue
        row = merged["matches"].setdefault(str(match_key), {})
        row["odds_sources"] = independent_sources(
            list((match_evidence.get("odds") or {}).keys()), role="odds"
        )
        row["context_sources"] = independent_sources(
            list((match_evidence.get("context") or {}).keys()), role="context"
        )

    merged["runs"] = _dedupe_runs(all_runs, ("run_id", "planned_at_utc"), 64)
    merged["provider_runs"] = _dedupe_runs(
        all_provider_runs,
        ("run_id", "provider", "method", "observed_at_utc"),
        256,
    )
    merged["updated_at_utc"] = datetime.now(UTC).isoformat()
    return merged


def restore_state() -> dict[str, Any]:
    date_key = target_date()
    dated_evidence = load(evidence_path(date_key), {})
    latest_evidence = load(EVIDENCE_PATH, {})
    evidence = _merge_evidence(date_key, [latest_evidence, dated_evidence])

    dated_ledger = load(ledger_path(date_key), {})
    latest_ledger = load(LEDGER_PATH, {})
    ledger = _merge_ledger(date_key, [latest_ledger, dated_ledger], evidence)

    atomic_write(evidence_path(date_key), evidence)
    atomic_write(EVIDENCE_PATH, evidence)
    atomic_write(ledger_path(date_key), ledger)
    atomic_write(LEDGER_PATH, ledger)

    evidence_rows = evidence.get("matches") or {}
    ledger_rows = ledger.get("matches") or {}
    return {
        "status": "restored",
        "date_local": date_key,
        "latest_evidence_accepted": _valid(latest_evidence, date_key),
        "dated_evidence_accepted": _valid(dated_evidence, date_key),
        "latest_ledger_accepted": _valid(latest_ledger, date_key),
        "dated_ledger_accepted": _valid(dated_ledger, date_key),
        "evidence_matches": len(evidence_rows),
        "ledger_matches": len(ledger_rows),
        "matches_with_odds_evidence": sum(
            bool((row or {}).get("odds")) for row in evidence_rows.values()
        ),
        "matches_with_context_evidence": sum(
            bool((row or {}).get("context")) for row in evidence_rows.values()
        ),
        "publication_contract_relaxed": False,
    }


def install() -> dict[str, Any]:
    return restore_state()


__all__ = ["install", "restore_state"]
