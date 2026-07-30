"""Fresh-market-first provider routing for Focused Alpha.

Cumulative inventory evidence is useful for identity and provider selection, but it is
not a current exact price. This router forces a bounded odds refresh lane for the top
focus matches and spends context budget only on the highest-information subset.
Publication guards remain unchanged.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.daily_coverage_common import EXPORT_DIR, as_float, as_int, atomic_write

REPORT_PATH = EXPORT_DIR / "latest-focused-alpha-provider-routing.json"

_ODDS_PROVIDERS = ("odds_api_io", "bzzoiro", "sstats_pari", "sportlogic")
_CONTEXT_PROVIDERS = (
    "sstats",
    "bzzoiro",
    "clubelo",
    "espn",
    "football_data",
    "thesportsdb",
    "openligadb",
    "sportlogic",
)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def enabled() -> bool:
    return _truthy("FOCUSED_ALPHA_PROVIDER_ROUTING_ENABLED", True)


def _limit(name: str, default: int, low: int = 0, high: int = 150) -> int:
    try:
        return max(low, min(high, int(float(str(os.getenv(name) or default)))))
    except Exception:
        return default


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _boxes(row: dict[str, Any]) -> list[dict[str, Any]]:
    boxes = [row]
    for key in (
        "refresh",
        "refresh_plan",
        "metadata",
        "coverage",
        "source_summary",
        "day_inventory_refresh",
    ):
        value = row.get(key)
        if isinstance(value, dict):
            boxes.append(value)
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for key in ("refresh", "refresh_plan", "day_inventory_refresh"):
        value = metadata.get(key)
        if isinstance(value, dict):
            boxes.append(value)
    return boxes


def _freshness_minutes(row: dict[str, Any], role: str, now: datetime) -> float | None:
    keys = (
        (
            "last_odds_refresh_utc",
            "odds_refreshed_at_utc",
            "odds_updated_at_utc",
            "bookmaker_backfill_updated_at_utc",
        )
        if role == "odds"
        else (
            "last_context_refresh_utc",
            "context_refreshed_at_utc",
            "context_updated_at_utc",
            "runtime_context_bridge_updated_utc",
        )
    )
    ages: list[float] = []
    for box in _boxes(row):
        for key in keys:
            parsed = _parse_time(box.get(key))
            if parsed is None:
                continue
            age = (now - parsed).total_seconds() / 60.0
            if age >= -2:
                ages.append(max(0.0, age))
    return min(ages) if ages else None


def _source_ids(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for box in _boxes(row):
        for key in ("source_ids", "provider_source_ids", "day_inventory_source_ids"):
            value = box.get(key)
            if isinstance(value, dict):
                result.update(value)
    return result


def _has_provider_id(row: dict[str, Any], provider: str) -> bool:
    return _source_ids(row).get(provider) not in (None, "", [], {})


def _text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "").lower()
        for key in ("league_name", "home_team", "away_team")
    )


def _senior_club(row: dict[str, Any]) -> bool:
    text = _text(row)
    return not any(
        token in text
        for token in (
            "women",
            "u17",
            "u18",
            "u19",
            "u20",
            "u21",
            "u23",
            "youth",
            "reserve",
            "friendly",
        )
    )


def _major_competition(row: dict[str, Any]) -> bool:
    text = _text(row)
    return any(
        token in text
        for token in (
            "premier league",
            "champions league",
            "europa league",
            "conference league",
            "la liga",
            "bundesliga",
            "serie a",
            "ligue 1",
            "eredivisie",
            "allsvenskan",
            "ekstraklasa",
            "superliga",
            "veikkausliiga",
            "mls",
            "libertadores",
            "sudamericana",
            "world cup",
            "euro",
        )
    )


def _german_competition(row: dict[str, Any]) -> bool:
    text = _text(row)
    return any(
        token in text
        for token in ("germany", "german", "bundesliga", "dfb", "regionalliga")
    )


def _kickoff_bucket(hours: float) -> int:
    for index, upper in enumerate((4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 36.0)):
        if hours <= upper:
            return index
    return 7


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _provider_health() -> dict[str, dict[str, Any]]:
    sportlogic = _load(EXPORT_DIR / "latest-sportlogic-coverage-probe.json")
    sportlogic_debug = _load(EXPORT_DIR / "latest-sportlogic-debug.json")
    stats = (
        sportlogic_debug.get("stats")
        if isinstance(sportlogic_debug.get("stats"), dict)
        else {}
    )
    diagnosis = str(stats.get("diagnosis") or sportlogic.get("diagnosis") or "").lower()
    matched = max(
        as_int(stats.get("events_matched")),
        as_int(sportlogic.get("matched_games")),
    )
    return {
        "sportlogic": {
            "matched": matched,
            "diagnosis": diagnosis,
            "usable": matched > 0 and "stale" not in diagnosis,
        }
    }


def _empty_assignments() -> dict[str, dict[str, list[str]]]:
    return {
        "odds_api_io": {"offers": []},
        "sstats_pari": {"offers": []},
        "sportlogic": {"offers": [], "context": []},
        "sstats": {"context": []},
        "bzzoiro": {"offers": [], "context": []},
        "clubelo": {"context": []},
        "football_data": {"context": []},
        "espn": {"context": []},
        "openligadb": {"context": []},
        "thesportsdb": {"context": []},
    }


def _provider_score(
    row: dict[str, Any],
    provider: str,
    role: str,
    existing: set[str],
    *,
    allow_existing_refresh: bool,
    health: dict[str, dict[str, Any]],
) -> float:
    if provider in existing and not allow_existing_refresh:
        return -1000.0
    base = {
        ("odds_api_io", "offers"): 110.0,
        ("bzzoiro", "offers"): 92.0,
        ("sstats_pari", "offers"): 82.0,
        ("sportlogic", "offers"): 5.0,
        ("sstats", "context"): 106.0,
        ("bzzoiro", "context"): 96.0,
        ("clubelo", "context"): 78.0,
        ("espn", "context"): 72.0,
        ("football_data", "context"): 64.0,
        ("thesportsdb", "context"): 48.0,
        ("openligadb", "context"): 40.0,
        ("sportlogic", "context"): 5.0,
    }.get((provider, role), 0.0)
    if _has_provider_id(row, provider):
        base += 45.0
    if provider == "sportlogic" and not health.get("sportlogic", {}).get("usable"):
        return -1000.0
    if role == "context":
        if provider == "clubelo":
            base += 26.0 if _senior_club(row) else -1000.0
        elif provider in {"espn", "football_data"}:
            base += 24.0 if _major_competition(row) else -18.0
        elif provider == "openligadb":
            base += 35.0 if _german_competition(row) else -30.0
        elif provider == "thesportsdb" and not _senior_club(row):
            base -= 20.0
    base += min(
        15.0,
        max(0.0, as_float(row.get("focused_alpha_score")) - 40.0) * 0.18,
    )
    hours = as_float(row.get("hours_to_kickoff"), 99.0)
    if hours <= 4:
        base += 10.0
    return base


def _append_best(
    *,
    row: dict[str, Any],
    key: str,
    role: str,
    providers: tuple[str, ...],
    existing: set[str],
    allow_existing_refresh: bool,
    choices: int,
    out: dict[str, dict[str, list[str]]],
    used: Counter[tuple[str, str]],
    budgets: dict[tuple[str, str], int],
    health: dict[str, dict[str, Any]],
) -> list[str]:
    ranked = sorted(
        (
            (
                _provider_score(
                    row,
                    provider,
                    role,
                    existing,
                    allow_existing_refresh=allow_existing_refresh,
                    health=health,
                ),
                provider,
            )
            for provider in providers
        ),
        reverse=True,
    )
    selected: list[str] = []
    for score, provider in ranked:
        if score < 0 or len(selected) >= choices:
            continue
        budget = budgets.get((provider, role), 0)
        if used[(provider, role)] >= budget:
            continue
        out[provider][role].append(key)
        used[(provider, role)] += 1
        selected.append(provider)
    return selected


def build_focused_assignments(
    rows: list[dict[str, Any]],
    run_index: int,
) -> dict[str, dict[str, list[str]]]:
    del run_index
    now = datetime.now(UTC)
    out = _empty_assignments()
    health = _provider_health()
    used: Counter[tuple[str, str]] = Counter()
    reasons: Counter[str] = Counter()
    routing_sample: list[dict[str, Any]] = []

    odds_lane = _limit("FOCUSED_ALPHA_ODDS_REFRESH_MATCHES", 40, 1, 100)
    double_odds_lane = _limit(
        "FOCUSED_ALPHA_DOUBLE_ODDS_REFRESH_MATCHES", 24, 0, odds_lane
    )
    context_lane = _limit("FOCUSED_ALPHA_CONTEXT_ENRICH_MATCHES", 30, 1, 80)
    double_context_lane = _limit(
        "FOCUSED_ALPHA_DOUBLE_CONTEXT_MATCHES", 15, 0, context_lane
    )
    odds_ttl = _limit("FOCUSED_ALPHA_ODDS_FRESH_MINUTES", 35, 5, 180)
    context_ttl = _limit("FOCUSED_ALPHA_CONTEXT_FRESH_MINUTES", 360, 30, 1440)

    budgets = {
        ("odds_api_io", "offers"): _limit(
            "FOCUSED_ALPHA_ODDS_API_IO_OFFERS_BUDGET", 40
        ),
        ("bzzoiro", "offers"): _limit("FOCUSED_ALPHA_BZZOIRO_OFFERS_BUDGET", 24),
        ("sstats_pari", "offers"): _limit(
            "FOCUSED_ALPHA_SSTATS_PARI_OFFERS_BUDGET", 16
        ),
        ("sportlogic", "offers"): 0,
        ("sstats", "context"): _limit("FOCUSED_ALPHA_SSTATS_CONTEXT_BUDGET", 30),
        ("bzzoiro", "context"): _limit("FOCUSED_ALPHA_BZZOIRO_CONTEXT_BUDGET", 24),
        ("clubelo", "context"): _limit("FOCUSED_ALPHA_CLUBELO_CONTEXT_BUDGET", 16),
        ("espn", "context"): _limit("FOCUSED_ALPHA_ESPN_CONTEXT_BUDGET", 16),
        ("football_data", "context"): _limit(
            "FOCUSED_ALPHA_FOOTBALL_DATA_CONTEXT_BUDGET", 8
        ),
        ("thesportsdb", "context"): _limit(
            "FOCUSED_ALPHA_THESPORTSDB_CONTEXT_BUDGET", 6
        ),
        ("openligadb", "context"): _limit(
            "FOCUSED_ALPHA_OPENLIGADB_CONTEXT_BUDGET", 4
        ),
        ("sportlogic", "context"): 0,
    }

    bootstrap_priority = any(
        bool(row.get("focused_alpha_bootstrap"))
        for row in rows
        if isinstance(row, dict)
    )
    ordered = sorted(
        [row for row in rows if isinstance(row, dict)],
        key=lambda row: (
            (
                _kickoff_bucket(as_float(row.get("hours_to_kickoff"), 999.0))
                if bootstrap_priority
                else 0
            ),
            -as_float(row.get("focused_alpha_score")),
            as_float(row.get("hours_to_kickoff"), 999.0),
            str(row.get("match_key") or ""),
        ),
    )

    for index, row in enumerate(ordered):
        key = str(row.get("match_key") or "")
        hours = as_float(row.get("hours_to_kickoff"), 99.0)
        if not key or row.get("provider_assignment_eligible") is False or hours < 0.33:
            reasons["expired_or_ineligible"] += 1
            continue
        odds = {
            str(value) for value in row.get("odds_sources") or [] if str(value)
        }
        contexts = {
            str(value) for value in row.get("context_sources") or [] if str(value)
        }
        odds_age = _freshness_minutes(row, "odds", now)
        context_age = _freshness_minutes(row, "context", now)
        odds_fresh = odds_age is not None and odds_age <= odds_ttl
        context_fresh = context_age is not None and context_age <= context_ttl
        selected = {"offers": [], "context": []}

        # Cumulative source names never substitute for a current exact market. The top
        # lane is refreshed every run; near-kickoff matches are refreshed as well.
        needs_odds_refresh = (
            index < odds_lane or hours <= 4 or len(odds) < 2 or not odds_fresh
        )
        if needs_odds_refresh and (index < odds_lane or hours <= 4):
            selected["offers"] = _append_best(
                row=row,
                key=key,
                role="offers",
                providers=_ODDS_PROVIDERS,
                existing=odds,
                allow_existing_refresh=True,
                choices=2 if index < double_odds_lane else 1,
                out=out,
                used=used,
                budgets=budgets,
                health=health,
            )
            reasons["forced_current_market_refresh"] += bool(selected["offers"])
        elif needs_odds_refresh:
            reasons["odds_refresh_deferred_outside_lane"] += 1

        # Expensive context work is limited to the best rows. Stale existing context is
        # refreshed; one fresh source is supplemented by a different source.
        context_eligible = index < context_lane and (
            bool(odds) or bool(selected["offers"])
        )
        if context_eligible and (len(contexts) < 2 or not context_fresh):
            selected["context"] = _append_best(
                row=row,
                key=key,
                role="context",
                providers=_CONTEXT_PROVIDERS,
                existing=contexts,
                allow_existing_refresh=not context_fresh,
                choices=2 if index < double_context_lane else 1,
                out=out,
                used=used,
                budgets=budgets,
                health=health,
            )
        elif len(contexts) < 2:
            reasons["context_deferred_outside_lane"] += 1

        if len(routing_sample) < 50:
            routing_sample.append(
                {
                    "rank": index + 1,
                    "match_key": key,
                    "focused_alpha_score": row.get("focused_alpha_score"),
                    "hours_to_kickoff": hours,
                    "existing_odds_sources": sorted(odds),
                    "existing_context_sources": sorted(contexts),
                    "odds_age_minutes": (
                        None if odds_age is None else round(odds_age, 1)
                    ),
                    "context_age_minutes": (
                        None if context_age is None else round(context_age, 1)
                    ),
                    "odds_fresh": odds_fresh,
                    "context_fresh": context_fresh,
                    "selected": selected,
                }
            )

    report = {
        "status": "ok",
        "created_at_utc": now.isoformat(),
        "mode": "fresh_market_then_bounded_context",
        "priority_mode": (
            "nearest_kickoff_bucket_first"
            if bootstrap_priority
            else "focused_alpha_score_first"
        ),
        "rows_seen": len(ordered),
        "odds_refresh_lane_rows": min(len(ordered), odds_lane),
        "double_odds_lane_rows": min(len(ordered), double_odds_lane),
        "context_enrichment_lane_rows": min(len(ordered), context_lane),
        "double_context_lane_rows": min(len(ordered), double_context_lane),
        "odds_fresh_minutes": odds_ttl,
        "context_fresh_minutes": context_ttl,
        "assignment_counts": {
            provider: {role: len(keys) for role, keys in roles.items()}
            for provider, roles in out.items()
        },
        "budget_used": {
            f"{provider}:{role}": count
            for (provider, role), count in sorted(used.items())
        },
        "reason_counts": dict(reasons),
        "routing_sample": routing_sample,
        "broadcast_all_providers": False,
        "cumulative_source_names_count_as_current_price": False,
        "publication_contract_relaxed": False,
    }
    atomic_write(REPORT_PATH, report)
    return out


__all__ = [
    "REPORT_PATH",
    "build_focused_assignments",
    "enabled",
    "_freshness_minutes",
]
