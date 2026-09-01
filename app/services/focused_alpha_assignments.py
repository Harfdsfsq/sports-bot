"""Provider routing by expected marginal information value.

Focused Alpha does not broadcast every uncovered fixture to every API. It ranks the
providers that are most likely to add a missing independent source for this specific
match, then respects per-provider run budgets. Provider routing never changes the
publication contract.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
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


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _parse_time(value: Any) -> datetime | None:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None


def _fresh(payload: dict[str, Any], minutes: int = 180) -> bool:
    for key in ("created_at_utc", "updated_at_utc", "created_at", "updated_at"):
        parsed = _parse_time(payload.get(key))
        if parsed is not None:
            age = datetime.now(UTC) - parsed
            return timedelta(0) <= age <= timedelta(minutes=minutes)
    return False


def _provider_health() -> dict[str, dict[str, Any]]:
    health: dict[str, dict[str, Any]] = {}
    sportlogic = _load(EXPORT_DIR / "latest-sportlogic-coverage-probe.json")
    sportlogic_debug = _load(EXPORT_DIR / "latest-sportlogic-debug.json")
    stats = sportlogic_debug.get("stats") if isinstance(sportlogic_debug.get("stats"), dict) else {}
    diagnosis = str(stats.get("diagnosis") or sportlogic.get("diagnosis") or "").lower()
    matched = max(
        as_int(stats.get("events_matched")),
        as_int(sportlogic.get("matched_games")),
    )
    health["sportlogic"] = {
        "fresh": _fresh(sportlogic) or _fresh(sportlogic_debug),
        "matched": matched,
        "diagnosis": diagnosis,
        "usable": matched > 0 and "stale" not in diagnosis,
    }
    bzz = _load(EXPORT_DIR / "latest-sstats-bzzoiro-odds-merge.json")
    health["bzzoiro"] = {
        "fresh": _fresh(bzz),
        "matched": as_int((bzz.get("bzzoiro") or {}).get("matches_with_offers"))
        if isinstance(bzz.get("bzzoiro"), dict)
        else 0,
        "usable": True,
    }
    return health


def _containers(row: dict[str, Any]) -> list[dict[str, Any]]:
    result = [row]
    for key in ("metadata", "coverage", "source_ids", "provider_source_ids"):
        value = row.get(key)
        if isinstance(value, dict):
            result.append(value)
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for key in ("day_inventory_source_ids", "provider_source_ids", "source_ids"):
        value = metadata.get(key)
        if isinstance(value, dict):
            result.append(value)
    return result


def _has_provider_id(row: dict[str, Any], provider: str) -> bool:
    for box in _containers(row):
        value = box.get(provider)
        if value not in (None, "", [], {}):
            return True
    return False


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
            "mls",
            "libertadores",
            "sudamericana",
            "world cup",
            "euro",
        )
    )


def _german_competition(row: dict[str, Any]) -> bool:
    text = _text(row)
    return any(token in text for token in ("germany", "german", "bundesliga", "dfb", "regionalliga"))


def _budget(provider: str, role: str, default: int) -> int:
    key = f"FOCUSED_ALPHA_{provider.upper()}_{role.upper()}_BUDGET"
    return max(0, min(150, as_int(os.getenv(key), default)))


def _provider_budget(provider: str, role: str, health: dict[str, dict[str, Any]]) -> int:
    defaults = {
        ("odds_api_io", "offers"): 100,
        ("bzzoiro", "offers"): 80,
        ("sstats_pari", "offers"): 80,
        ("sportlogic", "offers"): 0,
        ("sstats", "context"): 100,
        ("bzzoiro", "context"): 80,
        ("clubelo", "context"): 60,
        ("espn", "context"): 50,
        ("football_data", "context"): 50,
        ("thesportsdb", "context"): 45,
        ("openligadb", "context"): 30,
        ("sportlogic", "context"): 0,
    }
    default = defaults.get((provider, role), 40)
    if provider == "sportlogic" and health.get("sportlogic", {}).get("usable"):
        default = 25
    return _budget(provider, role, default)


def _provider_score(
    row: dict[str, Any],
    provider: str,
    role: str,
    existing: set[str],
    health: dict[str, dict[str, Any]],
) -> float:
    if provider in existing:
        return -1000.0
    base = {
        ("odds_api_io", "offers"): 100.0,
        ("bzzoiro", "offers"): 84.0,
        ("sstats_pari", "offers"): 76.0,
        ("sportlogic", "offers"): 10.0,
        ("sstats", "context"): 100.0,
        ("bzzoiro", "context"): 88.0,
        ("clubelo", "context"): 76.0,
        ("espn", "context"): 65.0,
        ("football_data", "context"): 62.0,
        ("thesportsdb", "context"): 48.0,
        ("openligadb", "context"): 40.0,
        ("sportlogic", "context"): 8.0,
    }.get((provider, role), 0.0)
    if _has_provider_id(row, provider):
        base += 45.0
    if provider == "sportlogic" and not health.get("sportlogic", {}).get("usable"):
        return -1000.0
    if role == "context":
        if provider == "clubelo":
            base += 24.0 if _senior_club(row) else -1000.0
        elif provider in {"espn", "football_data"}:
            base += 22.0 if _major_competition(row) else -12.0
        elif provider == "openligadb":
            base += 35.0 if _german_competition(row) else -25.0
        elif provider == "thesportsdb" and not _senior_club(row):
            base -= 15.0
    focus = as_float(row.get("focused_alpha_score"), 0.0)
    base += min(12.0, max(0.0, focus - 40.0) * 0.15)
    hours = as_float(row.get("hours_to_kickoff"), 99.0)
    if hours <= 4:
        base += 8.0
    return base


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


def build_focused_assignments(
    rows: list[dict[str, Any]],
    run_index: int,
) -> dict[str, dict[str, list[str]]]:
    out = _empty_assignments()
    health = _provider_health()
    used: Counter[tuple[str, str]] = Counter()
    reasons: Counter[str] = Counter()
    routing_sample: list[dict[str, Any]] = []
    odds_choices = 2
    context_choices = 2 if run_index <= 1 else 3

    for row in rows:
        hours = as_float(row.get("hours_to_kickoff"), 99.0)
        if row.get("provider_assignment_eligible") is False or hours < -0.25:
            reasons["expired_or_ineligible"] += 1
            continue
        key = str(row.get("match_key") or "")
        if not key:
            reasons["missing_match_key"] += 1
            continue
        odds = set(str(value) for value in row.get("odds_sources") or [])
        contexts = set(str(value) for value in row.get("context_sources") or [])
        selected: dict[str, list[str]] = {"offers": [], "context": []}

        if len(odds) < 2 or hours <= 4:
            ranked_odds = sorted(
                (
                    (_provider_score(row, provider, "offers", odds, health), provider)
                    for provider in _ODDS_PROVIDERS
                ),
                reverse=True,
            )
            for score, provider in ranked_odds:
                if score < 0 or len(selected["offers"]) >= odds_choices:
                    continue
                budget = _provider_budget(provider, "offers", health)
                if used[(provider, "offers")] >= budget:
                    reasons[f"{provider}_offers_budget_exhausted"] += 1
                    continue
                out[provider]["offers"].append(key)
                used[(provider, "offers")] += 1
                selected["offers"].append(provider)

        if len(contexts) < 2 or hours <= 4:
            ranked_context = sorted(
                (
                    (_provider_score(row, provider, "context", contexts, health), provider)
                    for provider in _CONTEXT_PROVIDERS
                ),
                reverse=True,
            )
            for score, provider in ranked_context:
                if score < 0 or len(selected["context"]) >= context_choices:
                    continue
                budget = _provider_budget(provider, "context", health)
                if used[(provider, "context")] >= budget:
                    reasons[f"{provider}_context_budget_exhausted"] += 1
                    continue
                out[provider]["context"].append(key)
                used[(provider, "context")] += 1
                selected["context"].append(provider)

        if not selected["offers"] and len(odds) < 2:
            reasons["unfilled_odds_gap"] += 1
        if not selected["context"] and len(contexts) < 2:
            reasons["unfilled_context_gap"] += 1
        if len(routing_sample) < 40:
            routing_sample.append(
                {
                    "match_key": key,
                    "focused_alpha_score": row.get("focused_alpha_score"),
                    "hours_to_kickoff": hours,
                    "existing_odds_sources": sorted(odds),
                    "existing_context_sources": sorted(contexts),
                    "selected": selected,
                }
            )

    report = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "expected_marginal_information_value",
        "rows_seen": len(rows),
        "run_index": run_index,
        "providers_per_odds_gap": odds_choices,
        "providers_per_context_gap": context_choices,
        "assignment_counts": {
            provider: {role: len(keys) for role, keys in roles.items()}
            for provider, roles in out.items()
        },
        "budget_used": {
            f"{provider}:{role}": count
            for (provider, role), count in sorted(used.items())
        },
        "provider_health": health,
        "reason_counts": dict(reasons),
        "routing_sample": routing_sample,
        "broadcast_all_providers": False,
        "publication_contract_relaxed": False,
    }
    atomic_write(REPORT_PATH, report)
    return out


__all__ = ["REPORT_PATH", "build_focused_assignments", "enabled"]
