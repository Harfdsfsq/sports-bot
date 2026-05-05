from __future__ import annotations

"""SQLite runtime store for bot state/history.

JSON remains the compatibility layer for the current release, but this script
makes SQLite the queryable runtime store by syncing state, debug, lifecycle,
inventory, provider budget and detailed reports into tables after every run.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(".").resolve()
DB_PATH = ROOT / ".data" / "runtime.sqlite"
EXPORT_PATH = ROOT / ".data" / "exports" / "latest-sqlite-runtime-store.json"

ARTIFACTS = {
    "state": ROOT / ".data" / "state.json",
    "debug_last_run": ROOT / ".logs" / "debug-last-run.json",
    "candidate_lifecycle_state": ROOT / ".data" / "candidate-lifecycle-state.json",
    "candidate_lifecycle_report": ROOT / ".data" / "exports" / "latest-candidate-lifecycle-report.json",
    "day_inventory_summary": ROOT / ".data" / "exports" / "latest-day-inventory-summary.json",
    "provider_request_budget": ROOT / ".data" / "exports" / "latest-provider-request-budget.json",
    "api_health": ROOT / ".data" / "exports" / "latest-api-health-run.json",
    "context_family_matching": ROOT / ".data" / "exports" / "latest-context-family-matching-report.json",
    "detailed_run_report": ROOT / ".data" / "exports" / "latest-detailed-run-report.json",
    "training_dataset": ROOT / ".data" / "exports" / "latest-training-dataset.json",
}


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runtime_artifacts (
            name TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at_utc TEXT NOT NULL,
            status TEXT,
            matches_seen INTEGER,
            matches_with_offers INTEGER,
            contexts_built INTEGER,
            raw_candidates INTEGER,
            publishable_candidates INTEGER,
            telegram_sent INTEGER,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_runtime (
            run_id INTEGER,
            provider TEXT,
            matches_with_data INTEGER,
            items_total INTEGER,
            requests INTEGER,
            errors INTEGER,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(run_id, provider)
        );
        CREATE TABLE IF NOT EXISTS candidate_lifecycle (
            key TEXT PRIMARY KEY,
            match_key TEXT,
            home_team TEXT,
            away_team TEXT,
            family TEXT,
            selection TEXT,
            point TEXT,
            seen_count INTEGER,
            value_streak INTEGER,
            last_value_ok INTEGER,
            priority_score REAL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS day_inventory_matches (
            match_key TEXT PRIMARY KEY,
            date_local TEXT,
            kickoff_utc TEXT,
            league_name TEXT,
            home_team TEXT,
            away_team TEXT,
            has_odds INTEGER,
            has_context INTEGER,
            ready_for_model INTEGER,
            sources_seen TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_health (
            provider TEXT PRIMARY KEY,
            status TEXT,
            configured INTEGER,
            requests INTEGER,
            useful_rows INTEGER,
            message TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS quality_segments (
            segment_key TEXT PRIMARY KEY,
            bets INTEGER,
            roi REAL,
            avg_clv REAL,
            win_rate REAL,
            payload_json TEXT NOT NULL
        );
        """
    )


def artifact_sync(conn: sqlite3.Connection) -> dict[str, int]:
    synced = 0
    for name, path in ARTIFACTS.items():
        payload = load_json(path, None)
        if payload is None:
            continue
        conn.execute(
            "REPLACE INTO runtime_artifacts(name,path,updated_at_utc,payload_json) VALUES(?,?,?,?)",
            (name, str(path), datetime.now(UTC).isoformat(), json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
        synced += 1
    return {"artifacts_synced": synced}


def deep_int(payload: Any, keys: set[str]) -> int:
    best = 0
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in keys:
                    try:
                        best = max(best, int(float(value or 0)))
                    except Exception:
                        pass
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(value for value in item if isinstance(value, (dict, list)))
    return best


def sync_run(conn: sqlite3.Connection) -> int | None:
    debug = load_json(ARTIFACTS["debug_last_run"], {})
    if not isinstance(debug, dict):
        return None
    summary = debug.get("summary") if isinstance(debug.get("summary"), dict) else debug
    run_payload = json.dumps(debug, ensure_ascii=False, sort_keys=True)
    cur = conn.execute(
        "INSERT INTO runs(created_at_utc,status,matches_seen,matches_with_offers,contexts_built,raw_candidates,publishable_candidates,telegram_sent,payload_json) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            datetime.now(UTC).isoformat(),
            str(summary.get("status") or debug.get("status") or "unknown"),
            deep_int(debug, {"matches_seen"}),
            deep_int(debug, {"matches_with_offers", "matches_with_any_offer_source"}),
            deep_int(debug, {"contexts_built", "matches_with_any_context_source"}),
            deep_int(debug, {"candidates_raw", "raw_candidates"}),
            deep_int(debug, {"candidates_publishable", "publishable_candidates"}),
            deep_int(debug, {"sent_messages", "telegram_picks_sent"}),
            run_payload,
        ),
    )
    run_id = int(cur.lastrowid)
    providers = (((debug.get("provider_diagnostics") or {}).get("summary") or {}).get("providers") or {}) if isinstance(debug.get("provider_diagnostics"), dict) else {}
    if isinstance(providers, dict):
        for name, row in providers.items():
            if not isinstance(row, dict):
                continue
            conn.execute(
                "REPLACE INTO provider_runtime(run_id,provider,matches_with_data,items_total,requests,errors,payload_json) VALUES(?,?,?,?,?,?,?)",
                (
                    run_id,
                    str(name),
                    int(row.get("matches_with_data") or row.get("contexts_built") or row.get("matches_built") or row.get("offers_parsed") or 0),
                    int(row.get("items_total") or 0),
                    int((row.get("stats") or {}).get("requests") or row.get("requests") or 0) if isinstance(row.get("stats") or row, dict) else 0,
                    int((row.get("stats") or {}).get("response_errors") or row.get("response_errors") or 0) if isinstance(row.get("stats") or row, dict) else 0,
                    json.dumps(row, ensure_ascii=False, sort_keys=True),
                ),
            )
    return run_id


def sync_lifecycle(conn: sqlite3.Connection) -> int:
    state = load_json(ARTIFACTS["candidate_lifecycle_state"], {})
    candidates = state.get("candidates") if isinstance(state, dict) else {}
    count = 0
    if isinstance(candidates, dict):
        for key, row in candidates.items():
            if not isinstance(row, dict):
                continue
            conn.execute(
                "REPLACE INTO candidate_lifecycle(key,match_key,home_team,away_team,family,selection,point,seen_count,value_streak,last_value_ok,priority_score,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(key), str(row.get("match_key") or ""), str(row.get("home_team") or ""), str(row.get("away_team") or ""), str(row.get("family") or ""), str(row.get("selection") or ""), str(row.get("point") or ""),
                    int(row.get("seen_count") or 0), int(row.get("value_streak") or 0), int(bool(row.get("last_value_ok"))), float(row.get("priority_score") or 0.0), json.dumps(row, ensure_ascii=False, sort_keys=True),
                ),
            )
            count += 1
    return count


def sync_inventory(conn: sqlite3.Connection) -> int:
    summary = load_json(ARTIFACTS["day_inventory_summary"], {})
    date_local = str((summary or {}).get("date_local") or "") if isinstance(summary, dict) else ""
    inv_path = ROOT / ".data" / "day_inventory" / f"{date_local}.json" if date_local else ROOT / ".data" / "day_inventory" / "latest.json"
    inventory = load_json(inv_path, {})
    rows = inventory.get("matches") if isinstance(inventory, dict) else []
    count = 0
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
            conn.execute(
                "REPLACE INTO day_inventory_matches(match_key,date_local,kickoff_utc,league_name,home_team,away_team,has_odds,has_context,ready_for_model,sources_seen,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(row.get("match_key") or row.get("canonical_match_id") or ""), str(row.get("date_local") or date_local), str(row.get("kickoff_utc") or ""), str(row.get("league_name") or ""), str(row.get("home_team") or ""), str(row.get("away_team") or ""),
                    int(bool(coverage.get("odds"))), int(bool(coverage.get("context"))), int(bool(coverage.get("ready_for_model"))), ",".join(str(x) for x in (row.get("sources_seen") or [])), json.dumps(row, ensure_ascii=False, sort_keys=True),
                ),
            )
            count += 1
    return count


def sync_health(conn: sqlite3.Connection) -> int:
    health = load_json(ARTIFACTS["api_health"], {})
    rows = health.get("results") if isinstance(health, dict) else []
    count = 0
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            conn.execute(
                "REPLACE INTO api_health(provider,status,configured,requests,useful_rows,message,payload_json) VALUES(?,?,?,?,?,?,?)",
                (str(row.get("provider") or ""), str(row.get("status") or ""), int(bool(row.get("configured"))), int(row.get("requests") or 0), int(row.get("useful_rows") or 0), str(row.get("message") or ""), json.dumps(row, ensure_ascii=False, sort_keys=True)),
            )
            count += 1
    return count


def main() -> int:
    with connect() as conn:
        init_schema(conn)
        artifact_stats = artifact_sync(conn)
        run_id = sync_run(conn)
        lifecycle_rows = sync_lifecycle(conn)
        inventory_rows = sync_inventory(conn)
        health_rows_count = sync_health(conn)
        conn.commit()
    report = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "db_path": str(DB_PATH),
        "run_id": run_id,
        **artifact_stats,
        "candidate_lifecycle_rows": lifecycle_rows,
        "day_inventory_rows": inventory_rows,
        "api_health_rows": health_rows_count,
        "notes": ["JSON files remain compatibility inputs.", "SQLite is now the queryable runtime store for runs, providers, inventory, lifecycle and health."],
    }
    write_json(EXPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
