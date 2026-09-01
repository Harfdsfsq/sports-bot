from __future__ import annotations

"""Accumulate latest run fixtures into the daily inventory.

The detailed Telegram report should not show only the current run's fixture slice
as the whole day inventory. This script merges every match exported by run-once
(`.data/exports/latest-matches.json`) into `.data/day_inventory/<date>.json`
before the detailed report is built.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.day_inventory_aliases import should_update_current_aliases, write_current_aliases

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_PATH = ROOT / ".data" / "exports" / "latest-day-inventory-accumulation.json"
SUMMARY_PATH = ROOT / ".data" / "exports" / "latest-day-inventory-summary.json"
HIGHWATER_NAMES = ("best-day-inventory-highwater.json", "highwater.json", "largest.json")


def app_tz() -> ZoneInfo:
    name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def norm_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", " ", str(value or "").lower()).strip()


def compact_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "_", norm_text(value)).strip("_")


def local_date_for_dt(dt: datetime) -> str:
    return dt.astimezone(app_tz()).date().isoformat()


def latest_match_rows() -> list[dict[str, Any]]:
    paths = [
        ROOT / ".data" / "exports" / "latest-matches.json",
        ROOT / ".data" / "latest-matches.json",
    ]
    for path in paths:
        payload = load_json(path, None)
        if isinstance(payload, list):
            return [dict(row) for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("matches", "rows", "data"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    return [dict(row) for row in rows if isinstance(row, dict)]
    return []


def source_id(row: dict[str, Any]) -> str:
    for key in ("source_event_id", "event_id", "id", "fixture_id", "match_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def kickoff_from_row(row: dict[str, Any]) -> datetime | None:
    for key in ("commence_time", "commence_time_utc", "kickoff_utc", "start_time", "date", "utcDate"):
        dt = parse_dt(row.get(key))
        if dt is not None:
            return dt
    return None


def match_key_from_row(row: dict[str, Any], kickoff: datetime | None) -> str:
    direct = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
    if direct:
        return direct
    home = compact_key(row.get("home_team") or row.get("home") or row.get("homeTeam") or "")
    away = compact_key(row.get("away_team") or row.get("away") or row.get("awayTeam") or "")
    if not home or not away:
        return ""
    day = kickoff.date().isoformat() if kickoff else "unknown_day"
    teams = sorted([home, away])
    return f"soccer|{teams[0]}|{teams[1]}|{day}"


def inventory_row_from_match(row: dict[str, Any], local_date: str) -> dict[str, Any] | None:
    kickoff = kickoff_from_row(row)
    if kickoff is None:
        return None
    if local_date_for_dt(kickoff) != local_date:
        return None
    key = match_key_from_row(row, kickoff)
    if not key:
        return None
    home = str(row.get("home_team") or row.get("home") or row.get("homeTeam") or "").strip()
    away = str(row.get("away_team") or row.get("away") or row.get("awayTeam") or "").strip()
    league = str(row.get("league_name") or row.get("league") or row.get("competition") or row.get("tournament") or "").strip()
    sport = str(row.get("sport_key") or row.get("sport") or "soccer").strip() or "soccer"
    source = str(row.get("source") or row.get("provider") or "latest_run").strip() or "latest_run"
    sid = source_id(row)
    return {
        "canonical_match_id": key,
        "match_key": key,
        "loose_key": "|".join(str(key).split("|")[:-1]) if "|" in key else key,
        "date_local": local_date,
        "kickoff_utc": kickoff.isoformat(),
        "kickoff_local": kickoff.astimezone(app_tz()).isoformat(),
        "sport_key": sport,
        "league_name": league,
        "league_key": compact_key(league),
        "home_team": home,
        "away_team": away,
        "home_team_norm": compact_key(home),
        "away_team_norm": compact_key(away),
        "tier": str(row.get("tier") or "mid"),
        "source_ids": {source: sid} if sid else {},
        "sources_seen": [source],
        "coverage": {
            "fixture_core": True,
            "odds": False,
            "context": False,
            "weather": False,
            "news": False,
            "xg": False,
            "form": False,
            "ready_for_model": False,
            "ready_for_publish": False,
        },
        "priority": 0.0,
        "last_enriched_at": None,
        "next_retry_at": None,
        "refresh": {
            "last_fixture_refresh_utc": datetime.now(UTC).isoformat(),
            "last_odds_refresh_utc": None,
            "last_context_refresh_utc": None,
        },
        "metadata": {
            "accumulated_from_latest_matches": True,
            "latest_run_source": source,
            "latest_run_source_event_id": sid,
        },
    }


def merge_rows(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    current = dict(existing)
    coverage = dict(current.get("coverage") or {})
    for key, value in dict(incoming.get("coverage") or {}).items():
        coverage[key] = bool(coverage.get(key)) or bool(value)
    refresh = dict(current.get("refresh") or {})
    incoming_refresh = dict(incoming.get("refresh") or {})
    for key, value in incoming_refresh.items():
        if value:
            refresh[key] = value if not refresh.get(key) else refresh.get(key)
    source_ids = dict(current.get("source_ids") or {})
    source_ids.update(dict(incoming.get("source_ids") or {}))
    sources_seen = sorted({*(current.get("sources_seen") or []), *(incoming.get("sources_seen") or [])})
    metadata = dict(current.get("metadata") or {})
    metadata.update(dict(incoming.get("metadata") or {}))
    current.update({
        "kickoff_utc": current.get("kickoff_utc") or incoming.get("kickoff_utc"),
        "kickoff_local": current.get("kickoff_local") or incoming.get("kickoff_local"),
        "league_name": current.get("league_name") or incoming.get("league_name"),
        "league_key": current.get("league_key") or incoming.get("league_key"),
        "home_team": current.get("home_team") or incoming.get("home_team"),
        "away_team": current.get("away_team") or incoming.get("away_team"),
        "home_team_norm": current.get("home_team_norm") or incoming.get("home_team_norm"),
        "away_team_norm": current.get("away_team_norm") or incoming.get("away_team_norm"),
        "source_ids": source_ids,
        "sources_seen": sources_seen,
        "coverage": coverage,
        "refresh": refresh,
        "metadata": metadata,
    })
    return current


def recompute_counts(rows: list[dict[str, Any]], previous: dict[str, Any], latest_rows_seen: int, added: int, updated: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    counts = dict(previous or {})
    base = {
        "matches_total": len(rows),
        "matches_with_odds": 0,
        "matches_with_context": 0,
        "matches_with_weather": 0,
        "matches_with_news": 0,
        "matches_with_xg": 0,
        "matches_with_form": 0,
        "matches_ready_for_model": 0,
        "matches_ready_for_publish": 0,
        "matches_next_6h": 0,
        "matches_next_6h_ready": 0,
        "matches_next_12h": 0,
        "matches_next_12h_ready": 0,
    }
    for row in rows:
        coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        has_odds = bool(coverage.get("odds"))
        has_context = bool(coverage.get("context"))
        ready = bool(coverage.get("ready_for_model")) or (has_odds and has_context)
        base["matches_with_odds"] += int(has_odds)
        base["matches_with_context"] += int(has_context)
        base["matches_with_weather"] += int(bool(coverage.get("weather")))
        base["matches_with_news"] += int(bool(coverage.get("news")))
        base["matches_with_xg"] += int(bool(coverage.get("xg")))
        base["matches_with_form"] += int(bool(coverage.get("form")))
        base["matches_ready_for_model"] += int(ready)
        base["matches_ready_for_publish"] += int(bool(coverage.get("ready_for_publish")))
        kickoff = parse_dt(row.get("kickoff_utc") or row.get("commence_time"))
        if kickoff is None:
            continue
        hours = (kickoff - now).total_seconds() / 3600.0
        if 0 <= hours <= 6:
            base["matches_next_6h"] += 1
            base["matches_next_6h_ready"] += int(ready)
        if 0 <= hours <= 12:
            base["matches_next_12h"] += 1
            base["matches_next_12h_ready"] += int(ready)
    for key, value in base.items():
        if key == "matches_total":
            counts[key] = max(as_int(counts.get(key)), value)
        elif key.startswith("matches_with_") or key.startswith("matches_ready_"):
            counts[key] = max(as_int(counts.get(key)), value)
        else:
            counts[key] = value
    counts["matches_seen_latest_run"] = latest_rows_seen
    counts["matches_added_from_latest_run"] = added
    counts["matches_updated_from_latest_run"] = updated
    counts["matches_total_high_watermark"] = max(as_int(counts.get("matches_total_high_watermark")), as_int(counts.get("matches_total")), len(rows))
    return counts


def highwater_paths(local_date: str) -> list[Path]:
    inv_dir = ROOT / ".data" / "day_inventory"
    cache_dir = ROOT / ".data" / "cache" / "day_inventory"
    return [
        *(inv_dir / name for name in HIGHWATER_NAMES),
        *(cache_dir / name for name in HIGHWATER_NAMES),
        inv_dir / f"{local_date}-highwater.json",
        cache_dir / f"{local_date}-highwater.json",
        ROOT / ".data" / "inventory_guard" / "best-day-inventory.json",
    ]


def write_full_highwater(local_date: str, inventory: dict[str, Any]) -> list[str]:
    matches = inventory.get("matches") if isinstance(inventory.get("matches"), list) else []
    if not matches:
        return []
    clone = dict(inventory)
    clone["highwater_updated_at_utc"] = datetime.now(UTC).isoformat()
    written: list[str] = []
    for path in highwater_paths(local_date):
        write_json(path, clone)
        written.append(str(path))
    return written


def main() -> int:
    now = datetime.now(UTC)
    local_date = target_date()
    inv_dir = ROOT / ".data" / "day_inventory"
    inventory_path = inv_dir / f"{local_date}.json"
    inventory = load_json(inventory_path, {})
    if not isinstance(inventory, dict):
        inventory = {}
    matches = inventory.get("matches") if isinstance(inventory.get("matches"), list) else []
    by_key: dict[str, dict[str, Any]] = {}
    for row in matches:
        if not isinstance(row, dict):
            continue
        key = str(row.get("canonical_match_id") or row.get("match_key") or "").strip()
        if key:
            by_key[key] = dict(row)

    latest_rows = latest_match_rows()
    added = 0
    updated = 0
    rejected = 0
    for raw in latest_rows:
        row = inventory_row_from_match(raw, local_date)
        if row is None:
            rejected += 1
            continue
        key = str(row.get("canonical_match_id") or row.get("match_key") or "").strip()
        if key in by_key:
            before = json.dumps(by_key[key], sort_keys=True, ensure_ascii=False)
            by_key[key] = merge_rows(by_key[key], row)
            after = json.dumps(by_key[key], sort_keys=True, ensure_ascii=False)
            updated += int(before != after)
        else:
            by_key[key] = row
            added += 1

    sorted_rows = sorted(by_key.values(), key=lambda item: (str(item.get("kickoff_utc") or ""), str(item.get("league_name") or ""), str(item.get("home_team") or "")))
    previous_counts = inventory.get("counts") if isinstance(inventory.get("counts"), dict) else {}
    counts = recompute_counts(sorted_rows, previous_counts, len(latest_rows), added, updated)
    inventory.update({
        "date_local": local_date,
        "timezone": str(app_tz()),
        "build_status": inventory.get("build_status") or "ok",
        "created_at_utc": inventory.get("created_at_utc") or now.isoformat(),
        "updated_at_utc": now.isoformat(),
        "counts": counts,
        "matches": sorted_rows,
    })
    sources = inventory.setdefault("sources", {})
    if not isinstance(sources, dict):
        sources = {}
        inventory["sources"] = sources
    sources["latest_match_accumulation"] = {
        "updated_at_utc": now.isoformat(),
        "latest_rows_seen": len(latest_rows),
        "added": added,
        "updated": updated,
        "rejected": rejected,
    }

    inv_dir.mkdir(parents=True, exist_ok=True)
    write_json(inventory_path, inventory)
    highwater_paths_written = write_full_highwater(local_date, inventory)
    alias_update = write_current_aliases(ROOT, local_date, inventory, write_json)
    summary = {
        "date_local": local_date,
        "updated_at_utc": now.isoformat(),
        "timezone": str(app_tz()),
        "build_status": inventory.get("build_status") or "ok",
        "counts": counts,
        "source_match_counts": dict(inventory.get("source_match_counts") or {}),
        "league_match_counts": dict(inventory.get("league_match_counts") or {}),
        "sources": dict(inventory.get("sources") or {}),
        "alias_update": alias_update,
        "highwater_paths": highwater_paths_written,
    }
    if should_update_current_aliases(local_date):
        write_json(SUMMARY_PATH, summary)
    report = {
        "status": "ok",
        "target_date": local_date,
        "updated_at_utc": now.isoformat(),
        "inventory_path": str(inventory_path),
        "summary_path": str(SUMMARY_PATH) if should_update_current_aliases(local_date) else None,
        "alias_update": alias_update,
        "highwater_paths": highwater_paths_written,
        "latest_rows_seen": len(latest_rows),
        "matches_before": len(matches),
        "matches_after": len(sorted_rows),
        "matches_added": added,
        "matches_updated": updated,
        "matches_rejected_not_target_date_or_malformed": rejected,
        "counts": counts,
        "notes": [
            "This runs after run-once, so the day inventory grows from actual latest-matches exports, not only from the pre-run bootstrap.",
            "Telegram detailed report reads latest-day-inventory-summary.json, so matches_total now represents the cumulative daily high watermark.",
            "The full accumulated inventory rows are persisted to day_inventory highwater files so later top-cut/alias steps cannot reduce the daily pool.",
        ],
    }
    write_json(EXPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
