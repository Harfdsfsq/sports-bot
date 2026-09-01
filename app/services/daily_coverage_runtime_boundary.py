from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

from app.services.daily_coverage_common import canonical_source, parse_dt
from app.services.daily_coverage_ledger import (
    cached_provider_data,
    finalize_daily_coverage,
    merge_provider_data,
    record_provider_result,
)
from app.services.daily_coverage_plan import filter_matches, provider_timeout

_ORIGINAL_FETCH = None
_ORIGINAL_BUILD = None
_ORIGINAL_RUN = None
_ORIGINAL_BUILD_LINES = None


def _provider_name(runner: Any, provider: Any) -> str:
    try:
        return canonical_source(runner._provider_name(provider))
    except Exception:
        module = getattr(getattr(provider, "__class__", None), "__module__", "")
        return canonical_source(module.rsplit(".", 1)[-1])


def _near_kickoff(matches: list[Any], hours: float = 4.0) -> list[Any]:
    now = datetime.now(UTC)
    result: list[Any] = []
    for match in matches:
        kickoff = getattr(match, "commence_time", None)
        if not isinstance(kickoff, datetime):
            continue
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=UTC)
        delta = (kickoff.astimezone(UTC) - now).total_seconds() / 3600.0
        if -0.25 <= delta <= hours:
            result.append(match)
    return result


def _merge_match_scope(planned: list[Any], near: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for match in list(planned or []) + list(near or []):
        key = str(getattr(match, "match_key", ""))
        if key and key not in seen:
            seen.add(key)
            out.append(match)
    return out


async def _fetch(
    self: Any,
    provider: Any | None,
    method_name: str,
    *args: Any,
    empty_data: Any,
):
    assert callable(_ORIGINAL_FETCH)
    if provider is None:
        return await _ORIGINAL_FETCH(
            self, provider, method_name, *args, empty_data=empty_data
        )
    name = _provider_name(self, provider)
    call_args = list(args)
    original_matches = (
        list(call_args[0]) if call_args and isinstance(call_args[0], list) else []
    )
    cached = (
        cached_provider_data(name, method_name, original_matches)
        if original_matches
        else {}
    )
    before = after = None
    if call_args and isinstance(call_args[0], list):
        before = len(call_args[0])
        planned = list(filter_matches(name, method_name, call_args[0]) or [])
        role = "offers" if "offer" in method_name.lower() else "context"
        refresh_provider = (
            role == "offers" and name in {"odds_api_io", "sstats_pari", "sportlogic"}
        ) or (role == "context" and name in {"sstats", "clubelo"})
        near = _near_kickoff(original_matches) if refresh_provider else []
        call_args[0] = _merge_match_scope(planned, near)
        after = len(call_args[0])
        if after == 0:
            return (
                cached or empty_data,
                {
                    "enabled": True,
                    "planned_skip": True,
                    "cache_reused": len(cached),
                    "provider": name,
                    "method": method_name,
                    "matches_before_plan": before,
                    "matches_after_plan": 0,
                },
                {"planned_skip": True, "cache_reused": len(cached)},
            )
    timeout = provider_timeout(name)
    started = datetime.now(UTC)
    try:
        call = _ORIGINAL_FETCH(
            self, provider, method_name, *call_args, empty_data=empty_data
        )
        if timeout:
            data, stats, preview = await asyncio.wait_for(call, timeout=timeout)
        else:
            data, stats, preview = await call
    except TimeoutError:
        stats = {
            "enabled": True,
            "provider": name,
            "method": method_name,
            "runtime_error": "daily_coverage_provider_deadline_exhausted",
            "budget_exhausted": True,
            "deadline_seconds": timeout,
            "elapsed_seconds": round(
                (datetime.now(UTC) - started).total_seconds(), 3
            ),
            "matches_before_plan": before,
            "matches_after_plan": after,
            "publication_contract_relaxed": False,
        }
        return (
            cached or empty_data,
            stats,
            {"deadline_exhausted": True, "cache_reused": len(cached)},
        )
    fresh_data = data
    data = merge_provider_data(cached, fresh_data, method_name)
    stats = stats if isinstance(stats, dict) else {}
    stats.update(
        {
            "daily_coverage_provider": name,
            "matches_before_plan": before,
            "matches_after_plan": after,
            "daily_coverage_deadline_seconds": timeout,
            "daily_coverage_cache_reused": len(cached),
        }
    )
    with contextlib.suppress(Exception):
        record_provider_result(name, method_name, fresh_data, stats)
    return data, stats, preview


def _build(
    context_maps: dict[str, dict[str, Any]],
    merged_contexts: dict[str, Any],
    observed_at: datetime,
):
    assert callable(_ORIGINAL_BUILD)
    remapped: dict[str, dict[str, Any]] = {}
    for slot, mapping in (context_maps or {}).items():
        if not isinstance(mapping, dict):
            continue
        for match_key, context in mapping.items():
            source = canonical_source(getattr(context, "source", None) or slot)
            remapped.setdefault(source, {})[str(match_key)] = context
    return _ORIGINAL_BUILD(remapped, merged_contexts, observed_at)


def _build_lines(offers_by_match: dict[str, list[Any]], observed_at: datetime):
    assert callable(_ORIGINAL_BUILD_LINES)
    rows = _ORIGINAL_BUILD_LINES(offers_by_match, observed_at)
    for row in rows:
        metadata = dict(getattr(row, "metadata", {}) or {})
        fetched_at = parse_dt(
            metadata.get("fetched_at_utc")
            or metadata.get("observed_at_utc")
            or metadata.get("updated_at_utc")
        )
        if fetched_at is not None:
            row.observed_at = fetched_at
    return rows


async def _run(self: Any):
    assert callable(_ORIGINAL_RUN)
    summary = None
    try:
        result = await _ORIGINAL_RUN(self)
        summary = result if isinstance(result, dict) else None
        return result
    finally:
        with contextlib.suppress(Exception):
            finalize_daily_coverage(summary)


def install(
    prediction_runner: Any, runner_module: Any, evidence_module: Any
) -> dict[str, Any]:
    global _ORIGINAL_FETCH, _ORIGINAL_BUILD, _ORIGINAL_RUN, _ORIGINAL_BUILD_LINES
    _ORIGINAL_FETCH = prediction_runner._fetch_provider
    _ORIGINAL_BUILD = runner_module.build_context_bundles
    _ORIGINAL_BUILD_LINES = runner_module.build_line_snapshots
    _ORIGINAL_RUN = prediction_runner.run_once
    prediction_runner._fetch_provider = _fetch
    prediction_runner.run_once = _run
    runner_module.build_context_bundles = _build
    evidence_module.build_context_bundles = _build
    runner_module.build_line_snapshots = _build_lines
    evidence_module.build_line_snapshots = _build_lines
    return {
        "final_provider_deadlines": True,
        "actual_context_source_identity": True,
        "ledger_after_each_provider": True,
        "cached_evidence_reused": True,
        "near_kickoff_refresh_hours": 4,
        "cached_line_observation_time_preserved": True,
    }
