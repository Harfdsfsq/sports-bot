"""Use one adaptive cohort for modelling and provider enrichment.

The broad daily inventory remains a discovery and identity ledger. The daily plan is
authoritative for expensive provider work and for the CandidateFactory model pass:
an explicit empty Focused Alpha cohort means zero targets rather than a silent
fallback to the original broad list.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.services.daily_coverage_common import canonical_source, target_date
from app.services.daily_coverage_plan import (
    filter_matches,
    load_plan,
    planned_target_identities,
)
from app.utils import canonicalize_team_name, parse_datetime

_INSTALLED = False
_ORIGINAL_FILTER = None
_ORIGINAL_FETCH = None
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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


def _coverage_horizon_matches(
    runner: Any, matches: list[Any], now_utc: datetime
) -> list[Any]:
    tz = getattr(getattr(runner, "settings", None), "tzinfo", UTC)
    try:
        start = date.fromisoformat(target_date(now_utc))
    except ValueError:
        start = now_utc.astimezone(tz).date()
    end = start + timedelta(days=_horizon_days())
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


def _team_pair(home: Any, away: Any) -> tuple[str, str] | None:
    first = canonicalize_team_name(str(home or ""))
    second = canonicalize_team_name(str(away or ""))
    if not first or not second:
        return None
    return tuple(sorted((first, second)))


def _identity_from_key(value: Any) -> tuple[str, tuple[str, str]] | None:
    parts = [part.strip() for part in str(value or "").split("|")]
    if len(parts) >= 3 and _DATE_RE.fullmatch(parts[0]):
        pair = _team_pair(parts[1], parts[2])
        return (parts[0], pair) if pair else None
    if len(parts) >= 4 and _DATE_RE.fullmatch(parts[-1]):
        pair = _team_pair(parts[1], parts[2])
        return (parts[-1], pair) if pair else None
    return None


def _match_identities(match: Any, tz: Any) -> set[tuple[str, tuple[str, str]]]:
    identities: set[tuple[str, tuple[str, str]]] = set()
    direct = _identity_from_key(getattr(match, "match_key", ""))
    if direct:
        identities.add(direct)
    pair = _team_pair(getattr(match, "home_team", ""), getattr(match, "away_team", ""))
    kickoff = getattr(match, "commence_time", None)
    if pair and kickoff is not None:
        try:
            parsed = parse_datetime(kickoff)
        except Exception:
            parsed = None
        if parsed is not None:
            identities.add((parsed.astimezone(UTC).date().isoformat(), pair))
            identities.add((parsed.astimezone(tz).date().isoformat(), pair))
    return identities


def _focused_model_scope(runner: Any, matches: list[Any]) -> tuple[list[Any], bool]:
    plan = load_plan()
    focus = plan.get("focused_alpha") if isinstance(plan.get("focused_alpha"), dict) else {}
    declared = bool(focus) and plan.get("fixed_300_provider_target") is False
    if not declared:
        return matches, False
    keys = {
        str(value)
        for value in plan.get("target_match_keys") or []
        if str(value).strip()
    }
    if not keys:
        return [], True
    tz = getattr(getattr(runner, "settings", None), "tzinfo", UTC)
    identities = planned_target_identities(plan, keys, tz=tz)
    selected: list[Any] = []
    for match in matches:
        runtime_key = str(getattr(match, "match_key", ""))
        if runtime_key in keys or identities.intersection(_match_identities(match, tz)):
            selected.append(match)
    return selected, True


def _assignment_declared(provider: str, method_name: str) -> bool:
    role = "offers" if "offer" in str(method_name).lower() else "context"
    assignments = load_plan().get("assignments") or {}
    return (
        isinstance(assignments, dict)
        and provider in assignments
        and isinstance(assignments.get(provider), dict)
        and role in assignments[provider]
    )


def install(prediction_runner: Any) -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_FILTER, _ORIGINAL_FETCH
    if _INSTALLED:
        return {"status": "already_installed"}

    current_filter = prediction_runner._filter_matches
    current_fetch = prediction_runner._fetch_provider
    if getattr(current_fetch, "_harizon_full_inventory_provider_patch", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_FILTER = current_filter
    _ORIGINAL_FETCH = current_fetch

    def _filter_matches(self: Any, matches: list[Any], now_utc: datetime):
        assert callable(_ORIGINAL_FILTER)
        self._harizon_full_horizon_coverage_matches = _coverage_horizon_matches(
            self, list(matches or []), now_utc
        )
        publication_window = list(_ORIGINAL_FILTER(self, matches, now_utc) or [])
        focused, declared = _focused_model_scope(self, publication_window)
        self._harizon_focused_alpha_model_scope_declared = declared
        self._harizon_focused_alpha_model_targets = len(focused)
        self._harizon_original_model_window_targets = len(publication_window)
        return focused if declared else publication_window

    async def _fetch_provider(
        self: Any,
        provider: Any | None,
        method_name: str,
        *args: Any,
        empty_data: Any,
    ):
        assert callable(_ORIGINAL_FETCH)
        if provider is None or not args or not isinstance(args[0], list):
            return await _ORIGINAL_FETCH(
                self, provider, method_name, *args, empty_data=empty_data
            )
        name = _provider_name(self, provider)
        role_is_odds = "offer" in str(method_name).lower()
        eligible = name in (_ODDS if role_is_odds else _CONTEXT)
        if not eligible:
            return await _ORIGINAL_FETCH(
                self, provider, method_name, *args, empty_data=empty_data
            )

        broad = _dedupe(
            getattr(self, "_harizon_full_horizon_coverage_matches", []), args[0]
        )
        planned = list(filter_matches(name, method_name, broad) or [])
        declared = _assignment_declared(name, method_name)
        call_args = list(args)
        # An explicit [] is a valid Focused Alpha decision. Only providers absent
        # from the plan retain legacy fallback-to-current-window behaviour.
        call_args[0] = planned if declared else list(args[0])
        data, stats, preview = await _ORIGINAL_FETCH(
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
            stats["coverage_horizon_days"] = _horizon_days()
            stats["candidate_publish_window_targets"] = len(args[0])
            stats["focused_alpha_model_targets"] = int(
                getattr(self, "_harizon_focused_alpha_model_targets", len(args[0]))
            )
            stats["provider_assignment_declared"] = declared
            stats["explicit_empty_assignment_respected"] = declared and not planned
            stats["publication_window_relaxed"] = False
        return data, stats, preview

    _filter_matches._harizon_full_inventory_filter_capture = True
    _fetch_provider._harizon_full_inventory_provider_patch = True
    prediction_runner._filter_matches = _filter_matches
    prediction_runner._fetch_provider = _fetch_provider
    _INSTALLED = True
    return {
        "status": "installed",
        "coverage_scope": "adaptive_planned_local_time_horizon",
        "model_scope": "focused_alpha_target_match_keys",
        "coverage_horizon_days": _horizon_days(),
        "odds_providers": sorted(_ODDS),
        "context_providers": sorted(_CONTEXT),
        "explicit_empty_assignments_are_authoritative": True,
        "explicit_empty_focus_cohort_is_authoritative": True,
        "candidate_publication_window_relaxed": False,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
