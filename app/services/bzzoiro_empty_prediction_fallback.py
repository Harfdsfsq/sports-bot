"""Use the supported Bzzoiro v1 context endpoint when v2 is healthy but empty.

Bzzoiro v2 can return HTTP 200 and a populated event list while the batch
``/predictions/`` endpoint returns zero rows. The outage fallback correctly does
not open its 5xx circuit in that case, but the run receives no Bzzoiro context.
This wrapper performs one bounded v1 context attempt per process, caches the hard
contexts, and keeps v1/v2 canonicalized as the same ``bzzoiro`` source.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services import bzzoiro_v2_outage_fallback as outage
from app.services import daily_coverage_ledger as coverage_ledger

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = (
    ROOT
    / ".data"
    / "exports"
    / "latest-bzzoiro-empty-v2-context-fallback.json"
)
_INSTALLED = False
_ORIGINAL_CONTEXT = None
_ATTEMPTED = False
_CACHE: dict[str, Any] = {}
_LAST_FALLBACK_STATS: dict[str, Any] = {}


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _write(extra: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "status": "installed" if _INSTALLED else "not_installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "attempted": _ATTEMPTED,
        "cached_contexts": len(_CACHE),
        "last_fallback_stats": dict(_LAST_FALLBACK_STATS),
        "provider_identity_policy": "v1_and_v2_are_one_bzzoiro_source",
        "publication_contract_relaxed": False,
    }
    if extra:
        payload.update(extra)
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = REPORT_PATH.with_suffix(REPORT_PATH.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(REPORT_PATH)
    except Exception:
        pass


def _as_int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _successful_empty_v2(contexts: Any, stats: Any) -> bool:
    """Identify a healthy v2 event batch with an empty prediction response."""

    if contexts or not isinstance(stats, dict) or outage._server_failure(stats):
        return False
    enabled = _truthy(
        os.getenv("BZZOIRO_V1_CONTEXT_EMPTY_V2_FALLBACK_ENABLED"),
        True,
    )
    if not enabled:
        return False
    codes = outage._status_codes(stats)
    if codes and any(code != 200 for code in codes):
        return False
    if _as_int(stats.get("response_errors")) > 0 or stats.get("last_error"):
        return False
    return (
        _as_int(stats.get("events_fetched")) > 0
        and _as_int(stats.get("predictions_fetched")) == 0
        and _as_int(stats.get("contexts_built")) == 0
    )


def _requested_keys(matches: list[Any]) -> set[str]:
    return {
        str(getattr(match, "match_key", "")).strip()
        for match in matches
        if str(getattr(match, "match_key", "")).strip()
    }


def _cached_for(matches: list[Any]) -> dict[str, Any]:
    keys = _requested_keys(matches)
    if not keys:
        return {}
    return {key: value for key, value in _CACHE.items() if key in keys}


async def _v1_once(
    settings: Any,
    matches: list[Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    global _ATTEMPTED

    cached = _cached_for(matches)
    if _ATTEMPTED:
        return (
            cached,
            {
                "enabled": True,
                "skipped": True,
                "reason": "process_local_empty_v2_fallback_already_attempted",
                "cache_hit_contexts": len(cached),
                "publication_contract_relaxed": False,
            },
            {},
        )

    _ATTEMPTED = True
    contexts, stats, preview = await outage._v1_context_fallback(settings, matches)
    kept = dict(contexts or {})
    for context in kept.values():
        details = dict(getattr(context, "details", {}) or {})
        details["bzzoiro_api_fallback"] = "v1_after_v2_empty_predictions"
        details["bzzoiro_provider_identity"] = "bzzoiro"
        context.details = details
    _CACHE.update(kept)
    _LAST_FALLBACK_STATS.clear()
    _LAST_FALLBACK_STATS.update(dict(stats or {}))
    _write(
        {
            "trigger": "v2_http_200_events_present_predictions_empty",
            "new_contexts": len(kept),
        }
    )
    return kept, dict(stats or {}), dict(preview or {})


def _context_factory(original: Any):
    async def fetch_context(self: Any, matches: list[Any]):
        primary_contexts, primary_stats, primary_preview = await original(
            self,
            matches,
        )
        contexts = dict(primary_contexts or {})
        stats = dict(primary_stats or {})
        preview = dict(primary_preview or {})
        if not _successful_empty_v2(contexts, stats):
            return contexts, stats, preview

        fallback_contexts, fallback_stats, fallback_preview = await _v1_once(
            getattr(self, "settings", None),
            list(matches or []),
        )
        stats["v1_empty_prediction_fallback"] = fallback_stats
        preview["v1_empty_prediction_fallback"] = fallback_preview
        if fallback_contexts:
            contexts.update(fallback_contexts)
            stats["contexts_built"] = len(contexts)
            stats["contexts_built_from_v1_empty_prediction_fallback"] = len(
                fallback_contexts
            )
            coverage_ledger.record_provider_result(
                "bzzoiro",
                "fetch_context",
                fallback_contexts,
                stats,
            )
        return contexts, stats, preview

    fetch_context._harizon_bzzoiro_v2_empty_prediction_fallback = True  # type: ignore[attr-defined]
    # The outage wrapper remains inside this wrapper. Preserve its marker so
    # routine reassertion does not stack another copy around the same method.
    fetch_context._harizon_bzzoiro_v2_outage_fallback = bool(  # type: ignore[attr-defined]
        getattr(original, "_harizon_bzzoiro_v2_outage_fallback", False)
    )
    return fetch_context


def reassert() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_CONTEXT
    from app.providers.bzzoiro_v2 import BzzoiroContextProvider

    current = BzzoiroContextProvider.fetch_context
    if getattr(current, "_harizon_bzzoiro_v2_empty_prediction_fallback", False):
        result = {
            "status": "already_wrapped",
            "publication_contract_relaxed": False,
        }
    else:
        _ORIGINAL_CONTEXT = current
        BzzoiroContextProvider.fetch_context = _context_factory(current)  # type: ignore[assignment]
        result = {
            "status": "wrapped",
            "publication_contract_relaxed": False,
        }
    _INSTALLED = True
    _write({"runtime_reassert": result})
    return result


def install() -> dict[str, Any]:
    global _INSTALLED
    enabled = _truthy(
        os.getenv("BZZOIRO_V1_CONTEXT_EMPTY_V2_FALLBACK_ENABLED"),
        True,
    )
    if not enabled:
        return {"status": "disabled_by_env"}
    runtime = reassert()
    _INSTALLED = True
    result = {
        "status": "installed",
        "runtime": runtime,
        "empty_v2_prediction_fallback": True,
        "one_v1_attempt_per_process": True,
        "hard_context_filter_inherited": True,
        "provider_identity_policy": "v1_and_v2_are_one_bzzoiro_source",
        "publication_contract_relaxed": False,
    }
    _write(result)
    return result


__all__ = ["_successful_empty_v2", "install", "reassert"]
