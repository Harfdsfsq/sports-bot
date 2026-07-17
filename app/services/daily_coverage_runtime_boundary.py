from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.services.daily_coverage_common import canonical_source
from app.services.daily_coverage_ledger import finalize_daily_coverage, record_provider_result
from app.services.daily_coverage_plan import filter_matches, provider_timeout

_ORIGINAL_FETCH = None
_ORIGINAL_BUILD = None
_ORIGINAL_RUN = None


def _provider_name(runner: Any, provider: Any) -> str:
    try:
        return canonical_source(runner._provider_name(provider))
    except Exception:
        module = getattr(getattr(provider, "__class__", None), "__module__", "")
        return canonical_source(module.rsplit(".", 1)[-1])


async def _fetch(self: Any, provider: Any | None, method_name: str, *args: Any, empty_data: Any):
    assert callable(_ORIGINAL_FETCH)
    if provider is None:
        return await _ORIGINAL_FETCH(self, provider, method_name, *args, empty_data=empty_data)
    name, call_args = _provider_name(self, provider), list(args)
    before = after = None
    if call_args and isinstance(call_args[0], list):
        before = len(call_args[0])
        call_args[0] = filter_matches(name, method_name, call_args[0])
        after = len(call_args[0])
        if after == 0:
            return empty_data, {"enabled": True, "planned_skip": True, "provider": name, "method": method_name, "matches_before_plan": before, "matches_after_plan": 0}, {"planned_skip": True}
    timeout = provider_timeout(name)
    started = datetime.now(UTC)
    try:
        call = _ORIGINAL_FETCH(self, provider, method_name, *call_args, empty_data=empty_data)
        data, stats, preview = await asyncio.wait_for(call, timeout=timeout) if timeout else await call
    except TimeoutError:
        stats = {
            "enabled": True, "provider": name, "method": method_name,
            "runtime_error": "daily_coverage_provider_deadline_exhausted",
            "budget_exhausted": True, "deadline_seconds": timeout,
            "elapsed_seconds": round((datetime.now(UTC) - started).total_seconds(), 3),
            "matches_before_plan": before, "matches_after_plan": after,
            "publication_contract_relaxed": False,
        }
        return empty_data, stats, {"deadline_exhausted": True}
    stats = stats if isinstance(stats, dict) else {}
    stats.update({"daily_coverage_provider": name, "matches_before_plan": before, "matches_after_plan": after, "daily_coverage_deadline_seconds": timeout})
    try:
        record_provider_result(name, method_name, data, stats)
    except Exception:
        pass
    return data, stats, preview


def _build(context_maps: dict[str, dict[str, Any]], merged_contexts: dict[str, Any], observed_at: datetime):
    assert callable(_ORIGINAL_BUILD)
    remapped: dict[str, dict[str, Any]] = {}
    for slot, mapping in (context_maps or {}).items():
        if not isinstance(mapping, dict):
            continue
        for match_key, context in mapping.items():
            source = canonical_source(getattr(context, "source", None) or slot)
            remapped.setdefault(source, {})[str(match_key)] = context
    return _ORIGINAL_BUILD(remapped, merged_contexts, observed_at)


async def _run(self: Any):
    assert callable(_ORIGINAL_RUN)
    summary = None
    try:
        result = await _ORIGINAL_RUN(self)
        summary = result if isinstance(result, dict) else None
        return result
    finally:
        try:
            finalize_daily_coverage(summary)
        except Exception:
            pass


def install(prediction_runner: Any, runner_module: Any, evidence_module: Any) -> dict[str, Any]:
    global _ORIGINAL_FETCH, _ORIGINAL_BUILD, _ORIGINAL_RUN
    _ORIGINAL_FETCH = prediction_runner._fetch_provider
    _ORIGINAL_BUILD = runner_module.build_context_bundles
    _ORIGINAL_RUN = prediction_runner.run_once
    prediction_runner._fetch_provider = _fetch
    prediction_runner.run_once = _run
    runner_module.build_context_bundles = _build
    evidence_module.build_context_bundles = _build
    return {"final_provider_deadlines": True, "actual_context_source_identity": True, "ledger_after_each_provider": True}
