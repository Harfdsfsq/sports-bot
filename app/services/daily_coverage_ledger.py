from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.schemas import MatchContext, Offer
from app.services.daily_coverage_common import (
    EVIDENCE_PATH,
    LEDGER_PATH,
    REPORT_PATH,
    atomic_write,
    canonical_source,
    evidence_path,
    independent_sources,
    ledger_path,
    load,
    select_inventory,
    target_date,
)
from app.services.daily_coverage_plan import coverage_summary, load_plan, rank_inventory

_LOCK = threading.RLock()


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _compact_context(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, MatchContext):
        return None
    row = _serialize(value)
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    try:
        if len(json.dumps(payload, ensure_ascii=False)) > 20_000:
            row["payload"] = {"cache_compacted": True}
    except Exception:
        row["payload"] = {"cache_compacted": True}
    return row


def _compact_offers(value: Any) -> list[dict[str, Any]]:
    rows = []
    for item in list(value or [])[:80]:
        if isinstance(item, Offer):
            rows.append(_serialize(item))
    return rows


def record_provider_result(provider_name: str, method_name: str, data: Any, stats: Any = None) -> None:
    provider = canonical_source(provider_name)
    role = "odds" if "offer" in method_name.lower() else "context"
    if role == "context" and provider in {"", "unknown", "day_inventory", "merged", "self_history"}:
        return
    if not isinstance(data, dict):
        return
    keys = [str(key) for key, value in data.items() if value]
    if not keys:
        return
    now, date_key = datetime.now(UTC), target_date()
    with _LOCK:
        ledger = load(ledger_path(date_key), {})
        if not isinstance(ledger, dict) or ledger.get("date_local") != date_key:
            ledger = {"date_local": date_key, "matches": {}, "runs": []}
        evidence = load(evidence_path(date_key), {})
        if not isinstance(evidence, dict) or evidence.get("date_local") != date_key:
            evidence = {"date_local": date_key, "matches": {}}
        match_rows, evidence_rows = ledger.setdefault("matches", {}), evidence.setdefault("matches", {})
        field = "odds_sources" if role == "odds" else "context_sources"
        for key in keys:
            row = match_rows.setdefault(key, {})
            row[field] = independent_sources(list(row.get(field) or []) + [provider], role=role)
            row[f"last_{provider}_{role}_observed_at_utc"] = now.isoformat()
            match_evidence = evidence_rows.setdefault(key, {}).setdefault(role, {})
            compact = _compact_offers(data.get(key)) if role == "odds" else _compact_context(data.get(key))
            if compact:
                match_evidence[provider] = {"updated_at_utc": now.isoformat(), "data": compact}
        runs = ledger.setdefault("provider_runs", [])
        runs.append({
            "run_id": str(os.getenv("GITHUB_RUN_ID") or ""), "provider": provider,
            "method": method_name, "role": role, "matched": len(keys),
            "observed_at_utc": now.isoformat(), "stats": _serialize(stats or {}),
        })
        ledger["provider_runs"] = runs[-128:]
        ledger["updated_at_utc"] = evidence["updated_at_utc"] = now.isoformat()
        atomic_write(ledger_path(date_key), ledger)
        atomic_write(LEDGER_PATH, ledger)
        atomic_write(evidence_path(date_key), evidence)
        atomic_write(EVIDENCE_PATH, evidence)


def cached_provider_data(provider_name: str, method_name: str, matches: list[Any]) -> dict[str, Any]:
    provider = canonical_source(provider_name)
    role = "odds" if "offer" in method_name.lower() else "context"
    evidence = load(evidence_path(target_date()), {})
    rows = evidence.get("matches") if isinstance(evidence, dict) and isinstance(evidence.get("matches"), dict) else {}
    max_minutes = int(float(os.getenv("DAILY_COVERAGE_ODDS_CACHE_MINUTES") or 360)) if role == "odds" else int(float(os.getenv("DAILY_COVERAGE_CONTEXT_CACHE_MINUTES") or 1440))
    cutoff = datetime.now(UTC) - timedelta(minutes=max_minutes)
    result: dict[str, Any] = {}
    for match in matches:
        key = str(getattr(match, "match_key", ""))
        item = (((rows.get(key) or {}).get(role) or {}).get(provider) or {}) if key else {}
        try:
            updated = datetime.fromisoformat(str(item.get("updated_at_utc") or "").replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
        except Exception:
            continue
        if updated.astimezone(UTC) < cutoff:
            continue
        raw = item.get("data")
        try:
            if role == "odds" and isinstance(raw, list):
                result[key] = [Offer(**row) for row in raw if isinstance(row, dict)]
            elif role == "context" and isinstance(raw, dict):
                result[key] = MatchContext(**raw)
        except Exception:
            continue
    return result


def merge_provider_data(cached: dict[str, Any], fresh: Any, method_name: str) -> dict[str, Any]:
    fresh_map = fresh if isinstance(fresh, dict) else {}
    if "offer" not in method_name.lower():
        return {**cached, **fresh_map}
    merged: dict[str, list[Offer]] = {key: list(value or []) for key, value in cached.items()}
    for key, offers in fresh_map.items():
        current = merged.setdefault(str(key), [])
        seen = {(item.source, item.bookmaker, item.family, item.selection, item.point, round(float(item.price), 4)) for item in current if isinstance(item, Offer)}
        for offer in offers or []:
            if not isinstance(offer, Offer):
                continue
            identity = (offer.source, offer.bookmaker, offer.family, offer.selection, offer.point, round(float(offer.price), 4))
            if identity not in seen:
                seen.add(identity)
                current.append(offer)
    return merged


def finalize_daily_coverage(summary: dict[str, Any] | None = None) -> dict[str, Any]:
    now, date_key = datetime.now(UTC), target_date()
    plan, ledger = load_plan(), load(ledger_path(date_key), {})
    inventory_path, rows = select_inventory(date_key)
    ranked = rank_inventory(rows, ledger if isinstance(ledger, dict) else {}, now, date_key)
    report = {
        "status": "ok", "created_at_utc": now.isoformat(), "date_local": date_key,
        "run_id": str(os.getenv("GITHUB_RUN_ID") or ""), "run_index": plan.get("run_index"),
        "phase_cumulative_target": plan.get("phase_cumulative_target"),
        "inventory_path": str(inventory_path) if inventory_path else None,
        "coverage_after": coverage_summary(ranked),
        "goal": {"matches": 300, "min_independent_odds_sources": 2, "min_independent_context_sources": 2},
        "provider_assignments": {provider: {role: len(keys or []) for role, keys in roles.items()} for provider, roles in (plan.get("assignments") or {}).items() if isinstance(roles, dict)},
        "runner_summary_available": isinstance(summary, dict),
        "publication_contract_relaxed": False,
    }
    atomic_write(REPORT_PATH, report)
    return report
