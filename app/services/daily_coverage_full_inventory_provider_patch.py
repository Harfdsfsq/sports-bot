"""Let coverage providers enrich the complete real-fixture horizon.

Prediction modelling intentionally stays inside the publication window. Coverage APIs,
however, must receive the complete upcoming inventory across ``RUN_DAYS_AHEAD``.
Runtime preflight can replace ``PredictionRunner`` methods after this patch was first
installed, so installation is deliberately re-entrant and the final wrapper can rebuild
its provider pool directly from the persisted strict cohort.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.schemas import Match
from app.services.daily_coverage_common import canonical_source, target_date
from app.services.daily_coverage_plan import filter_matches
from app.utils import (
    canonicalize_league_name,
    canonicalize_team_name,
    parse_datetime,
)

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / ".data" / "exports"
DAY_DIR = ROOT / ".data" / "day_inventory"
_INSTALLED = False

_ODDS = {
    "odds_api_io",
    "sstats_pari",
    "bzzoiro",
    "allsportsapi",
    "sportlogic",
    "bookies_api",
    "rapidapi_odds",
    "sharpapi",
}
_CONTEXT = {
    "sstats",
    "bzzoiro",
    "clubelo",
    "sportlogic",
    "football_data",
    "espn",
    "openligadb",
    "thesportsdb",
    "openfootball",
    "api_football",
}


def _horizon_days() -> int:
    for name in (
        "DAY_INVENTORY_HORIZON_DAYS",
        "DAY_INVENTORY_TARGET_HORIZON_DAYS",
        "RUN_DAYS_AHEAD",
    ):
        raw = os.getenv(name)
        if raw is None or not str(raw).strip():
            continue
        try:
            return max(1, min(4, int(float(str(raw)))))
        except (TypeError, ValueError):
            continue
    return 2


def _provider_name(runner: Any, provider: Any) -> str:
    try:
        return canonical_source(runner._provider_name(provider))
    except Exception:
        module = getattr(getattr(provider, "__class__", None), "__module__", "")
        return canonical_source(module.rsplit(".", 1)[-1])


def _dedupe(*groups: list[Any] | None) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for group in groups:
        for match in list(group or []):
            key = str(getattr(match, "match_key", ""))
            if key and key not in seen:
                seen.add(key)
                out.append(match)
    return out


def _coverage_bounds(runner: Any, now_utc: datetime) -> tuple[Any, date, date]:
    tz = getattr(getattr(runner, "settings", None), "tzinfo", UTC)
    try:
        start = date.fromisoformat(target_date(now_utc))
    except ValueError:
        start = now_utc.astimezone(tz).date()
    return tz, start, start + timedelta(days=_horizon_days())


def _coverage_horizon_matches(
    runner: Any, matches: list[Any], now_utc: datetime
) -> list[Any]:
    tz, start, end = _coverage_bounds(runner, now_utc)
    result: list[Any] = []
    for match in matches:
        kickoff = getattr(match, "commence_time", None)
        if not isinstance(kickoff, datetime):
            continue
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=UTC)
        local_day = kickoff.astimezone(tz).date()
        if not (start <= local_day < end):
            continue
        if kickoff.astimezone(UTC) < now_utc - timedelta(minutes=20):
            continue
        result.append(match)
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _cohort_paths(date_key: str) -> list[Path]:
    explicit = str(os.getenv("HARIZON_DAILY_COVERAGE_COHORT_PATH") or "").strip()
    paths = [
        Path(explicit) if explicit else None,
        EXPORT / "latest-daily-coverage-cohort.json",
        EXPORT / "latest-strict-day-inventory.json",
        DAY_DIR / f"{date_key}.json",
        DAY_DIR / "current.json",
        DAY_DIR / "latest.json",
        DAY_DIR / "today.json",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        if path is None:
            continue
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _row_value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _persisted_cohort_matches(
    runner: Any, now_utc: datetime
) -> tuple[list[Match], str, int]:
    """Build provider-addressable ``Match`` objects from the strict 300 cohort."""

    tz, start, end = _coverage_bounds(runner, now_utc)
    payload: dict[str, Any] = {}
    source_path = ""
    rows_seen = 0
    for path in _cohort_paths(start.isoformat()):
        candidate = _load_json(path)
        rows = candidate.get("matches") if isinstance(candidate.get("matches"), list) else []
        if not rows:
            continue
        payload = candidate
        source_path = str(path)
        rows_seen = len(rows)
        break
    if not payload:
        return [], source_path, rows_seen

    matches: list[Match] = []
    for row in payload.get("matches") or []:
        if not isinstance(row, dict):
            continue
        home = _row_value(row, "home_team", "home", "home_name", "team_home")
        away = _row_value(row, "away_team", "away", "away_name", "team_away")
        league = _row_value(row, "league_name", "competition", "league")
        raw_kickoff = (
            row.get("kickoff_utc")
            or row.get("commence_time")
            or row.get("start_time")
            or row.get("kickoff")
            or row.get("event_date")
        )
        try:
            kickoff = parse_datetime(raw_kickoff)
        except Exception:
            continue
        local_day = kickoff.astimezone(tz).date()
        if not (start <= local_day < end):
            continue
        if kickoff.astimezone(UTC) < now_utc - timedelta(minutes=20):
            continue
        if not home or not away or not league:
            continue
        if canonicalize_team_name(home) == canonicalize_team_name(away):
            continue

        source_ids = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
        provider_ids = (
            row.get("provider_source_ids")
            if isinstance(row.get("provider_source_ids"), dict)
            else {}
        )
        metadata = dict(row.get("metadata") or {})
        metadata.update(
            {
                "daily_coverage_cohort": True,
                "daily_coverage_cohort_path": source_path,
                "match_key": row.get("match_key"),
                "canonical_match_id": row.get("canonical_match_id"),
                "semantic_match_key": row.get("semantic_match_key"),
                "day_inventory_match_key": row.get("match_key"),
                "day_inventory_source_ids": source_ids,
                "provider_source_ids": provider_ids or source_ids,
                "coverage": row.get("coverage") or {},
            }
        )
        source_event_id = _row_value(row, "source_event_id") or next(
            (
                str(value).strip()
                for value in list(source_ids.values()) + list(provider_ids.values())
                if str(value or "").strip()
            ),
            str(row.get("match_key") or row.get("canonical_match_id") or ""),
        )
        matches.append(
            Match(
                source="daily_coverage_cohort",
                source_event_id=source_event_id,
                sport_key=str(row.get("sport_key") or "soccer"),  # type: ignore[arg-type]
                league_name=league,
                home_team=home,
                away_team=away,
                commence_time=kickoff,
                home_team_norm=str(
                    row.get("home_team_norm") or canonicalize_team_name(home)
                ),
                away_team_norm=str(
                    row.get("away_team_norm") or canonicalize_team_name(away)
                ),
                league_key=str(
                    row.get("league_key") or canonicalize_league_name(league)
                ),
                tier=str(row.get("tier") or "mid"),
                metadata=metadata,
            )
        )
    return _dedupe(matches), source_path, rows_seen


def install(prediction_runner: Any) -> dict[str, Any]:
    """Install or reassert the wrapper after runtime preflight replaced methods."""

    global _INSTALLED
    current_filter = prediction_runner._filter_matches
    current_fetch = prediction_runner._fetch_provider
    filter_patched = bool(
        getattr(current_filter, "_harizon_full_inventory_filter_capture", False)
    )
    fetch_patched = bool(
        getattr(current_fetch, "_harizon_full_inventory_provider_patch", False)
    )
    if filter_patched and fetch_patched:
        _INSTALLED = True
        return {"status": "already_patched", "publication_contract_relaxed": False}

    reasserted = _INSTALLED or filter_patched or fetch_patched

    if not filter_patched:
        original_filter = current_filter

        def _filter_matches(self: Any, matches: list[Any], now_utc: datetime):
            persisted, source_path, rows_seen = _persisted_cohort_matches(self, now_utc)
            captured = _coverage_horizon_matches(self, list(matches or []), now_utc)
            self._harizon_full_horizon_coverage_matches = _dedupe(persisted, captured)
            self._harizon_full_horizon_cohort_source = source_path
            self._harizon_full_horizon_cohort_rows_seen = rows_seen
            self._harizon_full_horizon_persisted_matches = len(persisted)
            return original_filter(self, matches, now_utc)

        _filter_matches._harizon_full_inventory_filter_capture = True
        _filter_matches._harizon_wrapped_method = original_filter
        prediction_runner._filter_matches = _filter_matches

    if not fetch_patched:
        original_fetch = current_fetch

        async def _fetch_provider(
            self: Any,
            provider: Any | None,
            method_name: str,
            *args: Any,
            empty_data: Any,
        ):
            if provider is None or not args or not isinstance(args[0], list):
                return await original_fetch(
                    self, provider, method_name, *args, empty_data=empty_data
                )
            name = _provider_name(self, provider)
            role_is_odds = "offer" in str(method_name).lower()
            eligible = name in (_ODDS if role_is_odds else _CONTEXT)
            if not eligible:
                return await original_fetch(
                    self, provider, method_name, *args, empty_data=empty_data
                )

            persisted = list(
                getattr(self, "_harizon_full_horizon_coverage_matches", []) or []
            )
            source_path = str(
                getattr(self, "_harizon_full_horizon_cohort_source", "") or ""
            )
            rows_seen = int(
                getattr(self, "_harizon_full_horizon_cohort_rows_seen", 0) or 0
            )
            if not persisted:
                persisted, source_path, rows_seen = _persisted_cohort_matches(
                    self, datetime.now(UTC)
                )
            broad = _dedupe(persisted, args[0])
            planned = list(filter_matches(name, method_name, broad) or [])
            call_args = list(args)
            call_args[0] = planned or list(args[0])
            data, stats, preview = await original_fetch(
                self,
                provider,
                method_name,
                *call_args,
                empty_data=empty_data,
            )
            if isinstance(stats, dict):
                stats["full_horizon_coverage_pool"] = len(broad)
                stats["full_day_coverage_pool"] = len(broad)
                stats["full_horizon_coverage_targets"] = len(call_args[0])
                stats["full_day_coverage_targets"] = len(call_args[0])
                stats["full_horizon_persisted_matches"] = len(persisted)
                stats["full_horizon_cohort_rows_seen"] = rows_seen
                stats["full_horizon_cohort_source"] = source_path
                stats["coverage_horizon_days"] = _horizon_days()
                stats["candidate_publish_window_targets"] = len(args[0])
                stats["full_horizon_runtime_reasserted"] = True
                stats["publication_window_relaxed"] = False
            return data, stats, preview

        _fetch_provider._harizon_full_inventory_provider_patch = True
        _fetch_provider._harizon_wrapped_method = original_fetch
        prediction_runner._fetch_provider = _fetch_provider

    _INSTALLED = True
    return {
        "status": "reasserted" if reasserted else "installed",
        "coverage_scope": "persisted_strict_cohort_local_time_horizon",
        "coverage_horizon_days": _horizon_days(),
        "odds_providers": sorted(_ODDS),
        "context_providers": sorted(_CONTEXT),
        "candidate_publication_window_unchanged": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
