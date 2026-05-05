from __future__ import annotations

"""Expand the daily fixture inventory before prediction.

Fast inventory is intentionally non-blocking, but it should still grow beyond the
current odds-api.io slice. This script performs small, bounded fixture-only calls
to free fixture providers and merges newly discovered matches into the daily
inventory before `run-once` starts.
"""

import asyncio
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

UTC = timezone.utc
ROOT = Path(".").resolve()
OUT_PATH = ROOT / ".data" / "exports" / "latest-day-inventory-fixture-expansion.json"
SUMMARY_PATH = ROOT / ".data" / "exports" / "latest-day-inventory-summary.json"


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def env_int(name: str, default: int) -> int:
    try:
        raw = env(name)
        return int(float(raw)) if raw else default
    except Exception:
        return default


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(env("APP_TIMEZONE") or env("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def target_date() -> str:
    explicit = env("DAY_INVENTORY_TARGET_DATE")
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            text += "T12:00:00+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def local_date_of(dt: datetime) -> str:
    return dt.astimezone(app_tz()).date().isoformat()


def norm_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", " ", str(value or "").lower()).strip()


def compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "_", norm_text(value)).strip("_")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def canonical_key(home: str, away: str, kickoff: datetime) -> str:
    teams = sorted([compact(home), compact(away)])
    return f"soccer|{teams[0]}|{teams[1]}|{kickoff.date().isoformat()}"


def base_inventory_row(*, source: str, source_id: str, home: str, away: str, league: str, kickoff: datetime, raw: dict[str, Any]) -> dict[str, Any]:
    key = canonical_key(home, away, kickoff)
    return {
        "canonical_match_id": key,
        "match_key": key,
        "loose_key": "|".join(key.split("|")[:-1]),
        "date_local": local_date_of(kickoff),
        "kickoff_utc": kickoff.isoformat(),
        "kickoff_local": kickoff.astimezone(app_tz()).isoformat(),
        "sport_key": "soccer",
        "league_name": league,
        "league_key": compact(league),
        "home_team": home,
        "away_team": away,
        "home_team_norm": compact(home),
        "away_team_norm": compact(away),
        "tier": "mid",
        "source_ids": {source: source_id} if source_id else {},
        "sources_seen": [source],
        "coverage": {"fixture_core": True, "odds": False, "context": False, "weather": False, "news": False, "xg": False, "form": False, "ready_for_model": False, "ready_for_publish": False},
        "priority": 0.0,
        "last_enriched_at": None,
        "next_retry_at": None,
        "refresh": {"last_fixture_refresh_utc": datetime.now(UTC).isoformat(), "last_odds_refresh_utc": None, "last_context_refresh_utc": None},
        "metadata": {"fixture_expansion_source": source, "fixture_expansion_raw_sample": raw},
    }


def merge_rows(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    current = dict(existing)
    source_ids = dict(current.get("source_ids") or {})
    source_ids.update(dict(incoming.get("source_ids") or {}))
    sources_seen = sorted({*(current.get("sources_seen") or []), *(incoming.get("sources_seen") or [])})
    coverage = dict(current.get("coverage") or {})
    for key, value in dict(incoming.get("coverage") or {}).items():
        coverage[key] = bool(coverage.get(key)) or bool(value)
    metadata = dict(current.get("metadata") or {})
    metadata.update(dict(incoming.get("metadata") or {}))
    refresh = dict(current.get("refresh") or {})
    refresh["last_fixture_refresh_utc"] = datetime.now(UTC).isoformat()
    current.update({
        "kickoff_utc": current.get("kickoff_utc") or incoming.get("kickoff_utc"),
        "kickoff_local": current.get("kickoff_local") or incoming.get("kickoff_local"),
        "league_name": current.get("league_name") or incoming.get("league_name"),
        "league_key": current.get("league_key") or incoming.get("league_key"),
        "home_team": current.get("home_team") or incoming.get("home_team"),
        "away_team": current.get("away_team") or incoming.get("away_team"),
        "source_ids": source_ids,
        "sources_seen": sources_seen,
        "coverage": coverage,
        "metadata": metadata,
        "refresh": refresh,
    })
    return current


def rows_from_football_data(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("matches") if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        home = str((row.get("homeTeam") or {}).get("name") or "").strip()
        away = str((row.get("awayTeam") or {}).get("name") or "").strip()
        league = str((row.get("competition") or {}).get("name") or "").strip()
        kickoff = parse_dt(row.get("utcDate"))
        if home and away and league and kickoff:
            out.append(base_inventory_row(source="football_data", source_id=str(row.get("id") or ""), home=home, away=away, league=league, kickoff=kickoff, raw={"status": row.get("status"), "stage": row.get("stage")}))
    return out


def rows_from_thesportsdb(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("events") if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        home = str(row.get("strHomeTeam") or "").strip()
        away = str(row.get("strAwayTeam") or "").strip()
        league = str(row.get("strLeague") or "").strip()
        kickoff = parse_dt(row.get("strTimestamp")) or parse_dt(f"{row.get('dateEvent') or ''}T{row.get('strTime') or '12:00:00'}+00:00")
        if home and away and league and kickoff:
            out.append(base_inventory_row(source="thesportsdb", source_id=str(row.get("idEvent") or ""), home=home, away=away, league=league, kickoff=kickoff, raw={"season": row.get("strSeason"), "venue": row.get("strVenue")}))
    return out


def rows_from_allsportsapi(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("result") if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        home = str(row.get("event_home_team") or row.get("home_team") or "").strip()
        away = str(row.get("event_away_team") or row.get("away_team") or "").strip()
        league = str(row.get("league_name") or row.get("country_name") or "").strip()
        date_value = str(row.get("event_date") or "").strip()
        time_value = str(row.get("event_time") or "12:00").strip()
        kickoff = parse_dt(f"{date_value}T{time_value}:00+00:00") if date_value else None
        if home and away and league and kickoff:
            out.append(base_inventory_row(source="allsportsapi", source_id=str(row.get("event_key") or ""), home=home, away=away, league=league, kickoff=kickoff, raw={"country": row.get("country_name"), "league_key": row.get("league_key")}))
    return out


async def fetch_json(client: httpx.AsyncClient, name: str, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[str, int, Any, str]:
    try:
        response = await client.get(url, params=params, headers=headers)
        try:
            payload = response.json()
        except Exception:
            payload = None
        return name, response.status_code, payload, response.text[:1000]
    except Exception as exc:
        return name, 0, None, f"{type(exc).__name__}: {exc}"


async def fetch_provider_rows(local_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    d = date.fromisoformat(local_date)
    rows: list[dict[str, Any]] = []
    provider_reports: dict[str, Any] = {}
    timeout = env_int("FIXTURE_EXPANSION_TIMEOUT_SECONDS", 18)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        calls = []
        fd_key = env("FOOTBALL_DATA_API_KEY")
        if fd_key:
            base = env("FOOTBALL_DATA_BASE_URL", "https://api.football-data.org/v4").rstrip("/")
            calls.append(fetch_json(client, "football_data", f"{base}/matches", params={"dateFrom": local_date, "dateTo": local_date}, headers={"X-Auth-Token": fd_key}))
        tdb_key = env("THESPORTSDB_API_KEY", "123")
        if tdb_key:
            base = env("THESPORTSDB_BASE_URL", "https://www.thesportsdb.com/api/v1/json").rstrip("/")
            calls.append(fetch_json(client, "thesportsdb", f"{base}/{tdb_key}/eventsday.php", params={"d": local_date, "s": "Soccer"}))
        all_key = env("ALLSPORTSAPI_API_KEY")
        if all_key:
            base = env("ALLSPORTSAPI_BASE_URL", "https://apiv2.allsportsapi.com/football").rstrip("/") + "/"
            calls.append(fetch_json(client, "allsportsapi", base, params={"met": "Fixtures", "APIkey": all_key, "from": local_date, "to": local_date, "timezone": "UTC"}))
        results = await asyncio.gather(*calls, return_exceptions=False)
    for name, status, payload, preview in results:
        if name == "football_data":
            parsed = rows_from_football_data(payload)
        elif name == "thesportsdb":
            parsed = rows_from_thesportsdb(payload)
        elif name == "allsportsapi":
            parsed = rows_from_allsportsapi(payload)
        else:
            parsed = []
        parsed = [row for row in parsed if row.get("date_local") == local_date]
        rows.extend(parsed)
        provider_reports[name] = {"http_status": status, "rows_parsed": len(parsed), "payload_type": type(payload).__name__, "body_preview": preview[:500]}
    return rows, provider_reports


def recompute_counts(rows: list[dict[str, Any]], previous: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC)
    counts = dict(previous or {})
    base = {"matches_total": len(rows), "matches_with_odds": 0, "matches_with_context": 0, "matches_with_weather": 0, "matches_with_news": 0, "matches_with_xg": 0, "matches_with_form": 0, "matches_ready_for_model": 0, "matches_ready_for_publish": 0, "matches_next_6h": 0, "matches_next_6h_ready": 0, "matches_next_12h": 0, "matches_next_12h_ready": 0}
    for row in rows:
        cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        has_odds = bool(cov.get("odds"))
        has_context = bool(cov.get("context"))
        ready = bool(cov.get("ready_for_model")) or (has_odds and has_context)
        base["matches_with_odds"] += int(has_odds)
        base["matches_with_context"] += int(has_context)
        base["matches_with_weather"] += int(bool(cov.get("weather")))
        base["matches_with_news"] += int(bool(cov.get("news")))
        base["matches_with_xg"] += int(bool(cov.get("xg")))
        base["matches_with_form"] += int(bool(cov.get("form")))
        base["matches_ready_for_model"] += int(ready)
        base["matches_ready_for_publish"] += int(bool(cov.get("ready_for_publish")))
        kickoff = parse_dt(row.get("kickoff_utc"))
        if kickoff:
            hours = (kickoff - now).total_seconds() / 3600.0
            if 0 <= hours <= 6:
                base["matches_next_6h"] += 1
                base["matches_next_6h_ready"] += int(ready)
            if 0 <= hours <= 12:
                base["matches_next_12h"] += 1
                base["matches_next_12h_ready"] += int(ready)
    for key, value in base.items():
        if key in {"matches_next_6h", "matches_next_6h_ready", "matches_next_12h", "matches_next_12h_ready"}:
            counts[key] = value
        else:
            counts[key] = max(int(counts.get(key) or 0), value)
    counts["matches_total_high_watermark"] = max(int(counts.get("matches_total_high_watermark") or 0), counts["matches_total"])
    return counts


async def main_async() -> int:
    if not truthy(env("FIXTURE_EXPANSION_ENABLED", "true")):
        write_json(OUT_PATH, {"status": "disabled"})
        return 0
    local_date = target_date()
    inv_dir = ROOT / ".data" / "day_inventory"
    inv_path = inv_dir / f"{local_date}.json"
    inventory = load_json(inv_path, {})
    if not isinstance(inventory, dict):
        inventory = {}
    current_rows = [dict(row) for row in inventory.get("matches", []) if isinstance(row, dict)]
    by_key = {str(row.get("canonical_match_id") or row.get("match_key")): row for row in current_rows if str(row.get("canonical_match_id") or row.get("match_key") or "").strip()}
    fetched_rows, provider_reports = await fetch_provider_rows(local_date)
    added = 0
    updated = 0
    for row in fetched_rows:
        key = str(row.get("canonical_match_id") or row.get("match_key") or "").strip()
        if not key:
            continue
        if key in by_key:
            before = json.dumps(by_key[key], sort_keys=True, ensure_ascii=False)
            by_key[key] = merge_rows(by_key[key], row)
            updated += int(before != json.dumps(by_key[key], sort_keys=True, ensure_ascii=False))
        else:
            by_key[key] = row
            added += 1
    sorted_rows = sorted(by_key.values(), key=lambda item: (str(item.get("kickoff_utc") or ""), str(item.get("league_name") or ""), str(item.get("home_team") or "")))
    counts = recompute_counts(sorted_rows, inventory.get("counts") if isinstance(inventory.get("counts"), dict) else {})
    inventory.update({"date_local": local_date, "timezone": str(app_tz()), "build_status": inventory.get("build_status") or "ok", "created_at_utc": inventory.get("created_at_utc") or datetime.now(UTC).isoformat(), "updated_at_utc": datetime.now(UTC).isoformat(), "counts": counts, "matches": sorted_rows})
    sources = inventory.setdefault("sources", {})
    if not isinstance(sources, dict):
        sources = {}
        inventory["sources"] = sources
    sources["fixture_expansion"] = {"updated_at_utc": datetime.now(UTC).isoformat(), "providers": provider_reports, "rows_fetched": len(fetched_rows), "added": added, "updated": updated}
    for path in [inv_path, inv_dir / "latest.json", inv_dir / "current.json", inv_dir / "today.json"]:
        write_json(path, inventory)
    summary = {"date_local": local_date, "updated_at_utc": datetime.now(UTC).isoformat(), "timezone": str(app_tz()), "build_status": inventory.get("build_status") or "ok", "counts": counts, "sources": dict(inventory.get("sources") or {})}
    write_json(SUMMARY_PATH, summary)
    report = {"status": "ok", "target_date": local_date, "matches_before": len(current_rows), "provider_rows_fetched": len(fetched_rows), "matches_added": added, "matches_updated": updated, "matches_after": len(sorted_rows), "counts": counts, "providers": provider_reports, "notes": ["Runs before prediction so run-once can see a broader daily fixture inventory.", "Fixture-only expansion does not create publishable predictions by itself; odds/context guards still apply."]}
    write_json(OUT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
