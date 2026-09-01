from __future__ import annotations

"""Expose provider semantic truth and stop repeating known-unusable calls.

A successful HTTP response is not useful coverage when a provider returns rows but
matches none of the current fixtures. This wrapper records that distinction and
opens a short per-provider circuit after a semantic or transport failure.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HEALTH_PATH = Path(".data/exports/latest-provider-semantic-health.json")
MARKER = "_harizon_provider_semantic_health_v1"


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _cooldown_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("PROVIDER_SEMANTIC_CIRCUIT_COOLDOWN_SECONDS", "900") or 900))
    except Exception:
        return 900.0


def _load() -> dict[str, Any]:
    try:
        payload = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write(provider: str, payload: dict[str, Any]) -> None:
    try:
        HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        current = _load()
        current[provider] = {"updated_at_utc": datetime.now(UTC).isoformat(), **payload}
        HEALTH_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _circuit_open(provider: str, now: datetime | None = None) -> bool:
    if not _truthy("PROVIDER_SEMANTIC_CIRCUIT_BREAKER_ENABLED", True):
        return False
    row = _load().get(provider)
    if not isinstance(row, dict) or not str(row.get("semantic_status") or "").startswith("degraded"):
        return False
    try:
        updated = str(row.get("updated_at_utc") or "").replace("Z", "+00:00")
        age = (now or datetime.now(UTC)) - datetime.fromisoformat(updated).astimezone(UTC)
        return age.total_seconds() < _cooldown_seconds()
    except Exception:
        return False


def _mapping_count(value: Any) -> int:
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, (list, tuple, set)):
        return len(value)
    try:
        return max(0, int(float(value))) if value not in (None, "") else 0
    except Exception:
        return 0


def stage_metrics(provider: str, stats: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Return explicit pipeline stages without inventing provider identities."""
    contexts = payload if isinstance(payload, dict) else {}
    raw = _mapping_count(
        stats.get("rows_received")
        or stats.get("rows_fetched")
        or stats.get("events_fetched")
        or stats.get("events_received")
    )
    matched = _mapping_count(stats.get("matched_rows") or stats.get("event_matches"))
    if not matched:
        matched = _mapping_count(stats.get("matched_exact")) + _mapping_count(stats.get("matched_loose")) + _mapping_count(stats.get("matched_fuzzy"))
    if not matched:
        matched = len(contexts)
    unmatched = _mapping_count(stats.get("unmatched_rows"))
    parsed = _mapping_count(stats.get("rows_parsed")) or max(0, raw - unmatched)
    direct = _mapping_count(stats.get("matched_exact")) + _mapping_count(stats.get("matched_loose")) + _mapping_count(stats.get("matched_fuzzy"))
    team_form = _mapping_count(stats.get("team_form_contexts_built"))
    usable = sum(1 for value in contexts.values() if value not in (None, {}, [], "")) if isinstance(contexts, dict) else 0
    return {
        "rows_received": raw,
        "rows_parsed": parsed,
        "rows_with_provider_ids": stats.get("rows_with_provider_ids"),
        "rows_with_valid_dates": stats.get("rows_with_valid_dates"),
        "rows_candidates_for_matching": stats.get("rows_candidates_for_matching", parsed),
        "rows_matched": matched,
        "direct_matches": direct,
        "team_form_contexts_built": team_form,
        "contexts_built": _mapping_count(stats.get("contexts_built")) or len(contexts),
        "contexts_usable": usable,
        "stage_metrics_complete": all(
            stats.get(key) is not None
            for key in ("rows_with_provider_ids", "rows_with_valid_dates")
        ),
        "provider": provider,
    }


def classify_status(provider: str, stats: dict[str, Any], metrics: dict[str, Any], payload: Any) -> str:
    errors = _mapping_count(stats.get("response_errors") or stats.get("errors"))
    raw = int(metrics.get("rows_received") or 0)
    direct = int(metrics.get("direct_matches") or 0)
    team_form = int(metrics.get("team_form_contexts_built") or 0)
    matched = int(metrics.get("rows_matched") or 0)
    if errors and not matched:
        return "degraded_transport_error"
    if provider == "sstats" and raw > 0 and direct == 0 and team_form == 0:
        return "degraded_semantic_no_match"
    if raw > 0 and matched == 0:
        return "degraded_semantic_no_match"
    if raw > 0 and int(metrics.get("contexts_usable") or 0) > 0:
        return "healthy"
    return "no_data"


def _install_method(module_name: str, class_name: str, method_name: str, provider: str) -> bool:
    try:
        module = __import__(module_name, fromlist=[class_name])
        cls = getattr(module, class_name)
        original = getattr(cls, method_name)
    except Exception:
        return False
    marker = f"{MARKER}_{provider}_{method_name}"
    if getattr(cls, marker, False):
        return True

    async def wrapped(self: Any, matches: list[Any]):
        if _circuit_open(provider):
            metrics = {"provider": provider, "rows_received": 0, "rows_matched": 0, "contexts_usable": 0}
            stats = {
                "provider": provider,
                "semantic_status": "circuit_open",
                "circuit_open": True,
                "circuit_cooldown_seconds": _cooldown_seconds(),
                "stage_metrics": metrics,
            }
            return {}, stats, {"semantic_status": "circuit_open"}
        result = await original(self, matches)
        if not isinstance(result, tuple) or len(result) != 3:
            return result
        payload, stats_raw, preview = result
        stats = dict(stats_raw or {}) if isinstance(stats_raw, dict) else {}
        metrics = stage_metrics(provider, stats, payload)
        status = classify_status(provider, stats, metrics, payload)
        stats["semantic_status"] = status
        stats["semantic_health"] = "degraded" if status.startswith("degraded") else status
        stats["stage_metrics"] = metrics
        if isinstance(preview, dict):
            preview = dict(preview)
            preview["semantic_status"] = status
            preview["stage_metrics"] = metrics
        if status.startswith("degraded"):
            _write(provider, {
                "semantic_status": status,
                "circuit_open_until_next_run": True,
                "stage_metrics": metrics,
                "last_error": stats.get("last_error"),
            })
        else:
            _write(provider, {"semantic_status": status, "stage_metrics": metrics})
        return payload, stats, preview

    setattr(cls, method_name, wrapped)
    setattr(cls, marker, True)
    return True


def install() -> dict[str, Any]:
    installed = {
        "bzzoiro_context": _install_method("app.providers.bzzoiro_v2", "BzzoiroContextProvider", "fetch_context", "bzzoiro"),
        "bzzoiro_offers": _install_method("app.providers.bzzoiro_v2", "BzzoiroContextProvider", "fetch_offers", "bzzoiro"),
        "sstats_context": _install_method("app.providers.sstats", "SStatsContextProvider", "fetch_context", "sstats"),
    }
    return {"status": "installed" if any(installed.values()) else "not_installed", **installed}
