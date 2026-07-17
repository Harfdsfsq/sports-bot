from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from app.services.daily_coverage_common import (
    DAY_DIR, LEDGER_PATH, MIN_CONTEXT_SOURCES, MIN_ODDS_SOURCES, PHASE_TARGETS,
    PLAN_PATH, PROVIDER_TIMEOUTS, TARGET_MATCHES, app_timezone, as_float, as_int,
    atomic_write, canonical_source, independent_sources, ledger_path, load, row_key,
    row_kickoff, select_inventory, state_path, target_date,
)


def _bucket(hours: float) -> tuple[int, str]:
    for index, edge in enumerate((4, 8, 12, 16, 20, 24)):
        if hours < edge:
            return index, f"{0 if index == 0 else edge - 4}_{edge}h"
    return 6, "24h_plus"


def _league_penalty(row: dict[str, Any]) -> int:
    text = " ".join(str(row.get(key) or "").lower() for key in ("league_name", "home_team", "away_team"))
    penalty = 3 if any(token in text for token in ("u17", "u18", "u19", "u20", "u21", "u23", "youth", "reserve")) else 0
    penalty += 2 if any(token in text for token in ("women", "femin", "friendly")) else 0
    return penalty


def _observed(row: dict[str, Any], ledger_row: dict[str, Any]) -> tuple[list[str], list[str]]:
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    odds = independent_sources(list(row.get("odds_sources") or []) + list(coverage.get("odds_sources") or []) + list(ledger_row.get("odds_sources") or []), role="odds")
    contexts = independent_sources(list(row.get("context_sources") or []) + list(coverage.get("context_sources") or []) + list(ledger_row.get("context_sources") or []), role="context")
    return odds, contexts


def rank_inventory(rows: list[dict[str, Any]], ledger: dict[str, Any], now: datetime, date_key: str) -> list[dict[str, Any]]:
    local_tz = app_timezone()
    ledger_matches = ledger.get("matches") if isinstance(ledger.get("matches"), dict) else {}
    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for row in rows:
        key, kickoff = row_key(row), row_kickoff(row)
        if not key or kickoff is None or kickoff.astimezone(local_tz).date().isoformat() != date_key:
            continue
        hours = (kickoff - now).total_seconds() / 3600.0
        if hours < -0.25:
            continue
        ledger_row = ledger_matches.get(key) if isinstance(ledger_matches.get(key), dict) else {}
        odds, contexts = _observed(row, ledger_row)
        source_ids = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
        provider_bonus = sum(bool(source_ids.get(name)) for name in ("odds_api_io", "sportlogic", "bzzoiro", "sstats", "football_data"))
        bucket, bucket_name = _bucket(hours)
        item = dict(row)
        item.update({
            "match_key": key,
            "kickoff_utc": kickoff.isoformat(),
            "hours_to_kickoff": round(hours, 3),
            "time_bucket": bucket_name,
            "odds_sources": odds,
            "context_sources": contexts,
            "odds_sources_count": len(odds),
            "context_sources_count": len(contexts),
            "line_deficit": max(0, MIN_ODDS_SOURCES - len(odds)),
            "context_deficit": max(0, MIN_CONTEXT_SOURCES - len(contexts)),
        })
        rank = (bucket, _league_penalty(row), -(bool(odds) + bool(contexts)), -provider_bonus, -as_float(row.get("priority")), kickoff, key)
        ranked.append((rank, item))
    ranked.sort(key=lambda item: item[0])
    return [item for _, item in ranked[:TARGET_MATCHES]]


def coverage_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "matches": len(rows),
        "with_1plus_odds_sources": sum(as_int(row.get("odds_sources_count")) >= 1 for row in rows),
        "with_2plus_odds_sources": sum(as_int(row.get("odds_sources_count")) >= 2 for row in rows),
        "with_1plus_context_sources": sum(as_int(row.get("context_sources_count")) >= 1 for row in rows),
        "with_2plus_context_sources": sum(as_int(row.get("context_sources_count")) >= 2 for row in rows),
        "with_2plus_both": sum(as_int(row.get("odds_sources_count")) >= 2 and as_int(row.get("context_sources_count")) >= 2 for row in rows),
    }


def _next_run(date_key: str, now: datetime) -> tuple[int, str]:
    path = state_path(date_key)
    state = load(path, {})
    if not isinstance(state, dict) or state.get("date_local") != date_key:
        state = {"date_local": date_key, "run_ids": [], "run_index": 0}
    run_id = str(os.getenv("GITHUB_RUN_ID") or f"local-{now.strftime('%Y%m%dT%H%M%S')}")
    run_ids = [str(value) for value in state.get("run_ids") or []]
    if run_id not in run_ids:
        run_ids.append(run_id)
        state["run_index"] = as_int(state.get("run_index")) + 1
    state.update({"run_ids": run_ids[-32:], "last_run_id": run_id, "last_run_at_utc": now.isoformat()})
    atomic_write(path, state)
    return max(1, as_int(state.get("run_index"), 1)), run_id


def _senior_club(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(key) or "").lower() for key in ("league_name", "home_team", "away_team"))
    return not any(token in text for token in ("women", "u17", "u18", "u19", "u20", "u21", "u23", "youth", "reserve", "friendly"))


def _assign(rows: list[dict[str, Any]], run_index: int) -> dict[str, dict[str, list[str]]]:
    out = {
        "odds_api_io": {"offers": []}, "sstats_pari": {"offers": []},
        "sportlogic": {"offers": [], "context": []}, "sstats": {"context": []},
        "bzzoiro": {"offers": [], "context": []}, "clubelo": {"context": []},
        "football_data": {"context": []}, "espn": {"context": []},
        "openligadb": {"context": []}, "thesportsdb": {"context": []},
    }
    for row in rows:
        key, odds, contexts = row["match_key"], set(row["odds_sources"]), set(row["context_sources"])
        hours = as_float(row.get("hours_to_kickoff"), 99.0)
        if len(odds) < 2 or hours <= 4:
            out["odds_api_io"]["offers"].append(key)
        for provider in ("sstats_pari", "sportlogic", "bzzoiro"):
            if len(odds) < 2 and provider not in odds:
                out[provider]["offers"].append(key)
        for provider in ("sstats", "sportlogic", "bzzoiro", "football_data", "espn", "openligadb", "thesportsdb"):
            if len(contexts) < 2 and provider not in contexts:
                out[provider]["context"].append(key)
        if len(contexts) < 2 and "clubelo" not in contexts and _senior_club(row):
            out["clubelo"]["context"].append(key)
    pari_limit = (150, 110, 80)[min(run_index, 3) - 1]
    sportlogic_limit = max(80, min(300, as_int(os.getenv("SPORTLOGIC_MATCH_LIMIT"), 180)))
    bzz_limit = max(8, as_int(os.getenv("BZZOIRO_RUNTIME_DETAIL_MATCH_LIMIT"), 24))
    out["sstats_pari"]["offers"] = out["sstats_pari"]["offers"][:pari_limit]
    for role in ("offers", "context"):
        out["sportlogic"][role] = out["sportlogic"][role][:sportlogic_limit]
        out["bzzoiro"][role] = out["bzzoiro"][role][:bzz_limit]
    return out


def prepare_daily_coverage(now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    date_key = target_date(current)
    inventory_path, rows = select_inventory(date_key)
    ledger = load(ledger_path(date_key), {})
    if not isinstance(ledger, dict) or ledger.get("date_local") != date_key:
        ledger = {"date_local": date_key, "matches": {}, "runs": []}
    ranked = rank_inventory(rows, ledger, current, date_key)
    run_index, run_id = _next_run(date_key, current)
    target_count = min(len(ranked), PHASE_TARGETS[min(run_index, 3) - 1])
    targets = ranked[:target_count]
    assignments = _assign(targets, run_index)
    os.environ["SSTATS_PARI_DETAIL_MATCH_LIMIT"] = str(len(assignments["sstats_pari"]["offers"]))
    os.environ.setdefault("SSTATS_PARI_CONCURRENCY", "12")
    os.environ.setdefault("SSTATS_PARI_TIMEOUT_SECONDS", "7")
    os.environ.setdefault("CLUBELO_CONTEXT_MATCH_LIMIT", "300")
    plan = {
        "status": "ok" if ranked else "inventory_missing_or_empty",
        "created_at_utc": current.isoformat(), "date_local": date_key,
        "inventory_path": str(inventory_path) if inventory_path else None,
        "inventory_rows_seen": len(rows), "top_inventory_matches": len(ranked),
        "run_id": run_id, "run_index": run_index, "phase": min(run_index, 3),
        "phase_targets": list(PHASE_TARGETS), "phase_cumulative_target": target_count,
        "min_odds_sources": 2, "min_context_sources": 2,
        "coverage_before": coverage_summary(ranked), "target_coverage_before": coverage_summary(targets),
        "target_match_keys": [row["match_key"] for row in targets],
        "assignments": assignments, "provider_timeouts_seconds": PROVIDER_TIMEOUTS,
        "matches": {row["match_key"]: {key: row.get(key) for key in ("home_team", "away_team", "league_name", "kickoff_utc", "hours_to_kickoff", "time_bucket", "odds_sources", "context_sources", "line_deficit", "context_deficit")} for row in ranked},
        "independence_policy": {
            "odds_api_io_accounts_count_as_one_source": True,
            "bookmakers_do_not_count_as_provider_sources": True,
            "synthetic_inventory_context_does_not_count": True,
        },
        "publication_contract_relaxed": False,
    }
    atomic_write(PLAN_PATH, plan)
    atomic_write(DAY_DIR / f"daily-coverage-plan-{date_key}.json", plan)
    ledger.setdefault("runs", []).append({"run_id": run_id, "run_index": run_index, "planned_at_utc": current.isoformat(), "cumulative_target": target_count})
    ledger["runs"] = ledger["runs"][-32:]
    ledger["updated_at_utc"] = current.isoformat()
    atomic_write(ledger_path(date_key), ledger)
    atomic_write(LEDGER_PATH, ledger)
    os.environ["HARIZON_DAILY_COVERAGE_PLAN_PATH"] = str(PLAN_PATH)
    return plan


def load_plan() -> dict[str, Any]:
    value = load(PLAN_PATH, {})
    return value if isinstance(value, dict) else {}


def provider_timeout(name: str) -> float | None:
    provider = canonical_source(name)
    try:
        return max(5.0, float(os.getenv(f"HARIZON_{provider.upper()}_WALL_SECONDS") or PROVIDER_TIMEOUTS.get(provider)))
    except (TypeError, ValueError):
        return None


def filter_matches(provider_name: str, method_name: str, matches: Any) -> Any:
    if not isinstance(matches, list) or not matches:
        return matches
    provider, role = canonical_source(provider_name), "offers" if "offer" in method_name.lower() else "context"
    assignments = load_plan().get("assignments") or {}
    if provider not in assignments or role not in (assignments.get(provider) or {}):
        return matches
    keys = set((assignments.get(provider) or {}).get(role) or [])
    return [match for match in matches if str(getattr(match, "match_key", "")) in keys] if keys else []
