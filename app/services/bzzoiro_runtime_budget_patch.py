from __future__ import annotations

"""Hard wall-clock/request budget for the expensive Bzzoiro v2 detail layer.

The v2 provider can issue one request per event for odds, stats, metadata,
prediction and odds comparison.  In production this expanded to hundreds of
sequential HTTP calls and the main ``run-once`` process reached its 600-second
shell timeout before candidate construction.  This patch keeps useful odds and
stats enrichment, disables redundant per-event metadata/prediction calls, and
shares one bounded budget across every v2 fetch in the Python process.

The bulk predictions provider remains enabled, so disabling the per-event
prediction endpoint does not remove the pre-match xG source.  Publication
contracts are not relaxed.
"""

import json
import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
OUT = Path(".data/exports/latest-bzzoiro-runtime-hard-budget.json")
ART = Path("artifacts/run-bot/latest-bzzoiro-runtime-hard-budget.json")

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "started_monotonic": None,
    "requests_claimed": 0,
    "requests_denied": 0,
    "endpoint_counts": Counter(),
    "last_stop_reason": "",
}
_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(float(str(os.getenv(name) or default).strip()))
    except Exception:
        value = default
    return max(minimum, value)


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(str(os.getenv(name) or default).strip())
    except Exception:
        value = default
    return max(minimum, value)


def _request_cap() -> int:
    requested = _int_env("BZZOIRO_RUNTIME_HARD_REQUEST_CAP", 120, 1)
    absolute = _int_env("BZZOIRO_RUNTIME_ABSOLUTE_MAX_REQUESTS", 140, 1)
    return min(requested, absolute)


def _wall_seconds() -> float:
    requested = _float_env("BZZOIRO_RUNTIME_HARD_SECONDS", 150.0, 10.0)
    absolute = _float_env("BZZOIRO_RUNTIME_ABSOLUTE_MAX_SECONDS", 210.0, 10.0)
    return min(requested, absolute)


def _detail_match_limit() -> int:
    requested = _int_env("BZZOIRO_RUNTIME_DETAIL_MATCH_LIMIT", 48, 1)
    absolute = _int_env("BZZOIRO_RUNTIME_ABSOLUTE_DETAIL_MATCH_LIMIT", 64, 1)
    return min(requested, absolute)


def _comparison_limit() -> int:
    requested = _int_env("BZZOIRO_RUNTIME_COMPARISON_MATCH_LIMIT", 24, 0)
    absolute = _int_env("BZZOIRO_RUNTIME_ABSOLUTE_COMPARISON_MATCH_LIMIT", 32, 0)
    return min(requested, absolute)


def _timeout_seconds() -> float:
    return min(_float_env("BZZOIRO_RUNTIME_HTTP_TIMEOUT_SECONDS", 8.0, 2.0), 12.0)


def _endpoint(path: Any) -> str:
    text = str(path or "").lower()
    if "odds/comparison" in text:
        return "odds_comparison"
    if text.endswith("/odds/") or "/odds/?" in text:
        return "odds"
    if text.endswith("/stats/") or "/stats/?" in text:
        return "stats"
    if text.endswith("/metadata/") or "/metadata/?" in text:
        return "metadata"
    if text.endswith("/prediction/") or "/prediction/?" in text:
        return "prediction"
    if "/events/" in text and text.rstrip("/").split("/")[-1].isdigit():
        return "event_detail"
    if "/events/" in text:
        return "events_list"
    return "other"


def _elapsed_locked(now: float | None = None) -> float:
    current = time.monotonic() if now is None else now
    started = _STATE.get("started_monotonic")
    if started is None:
        return 0.0
    return max(0.0, current - float(started))


def _claim(path: Any) -> tuple[bool, str]:
    now = time.monotonic()
    with _LOCK:
        if _STATE.get("started_monotonic") is None:
            _STATE["started_monotonic"] = now
        if _elapsed_locked(now) >= _wall_seconds():
            _STATE["requests_denied"] = int(_STATE.get("requests_denied", 0)) + 1
            _STATE["last_stop_reason"] = "wall_clock_budget_exhausted"
            return False, "wall_clock_budget_exhausted"
        if int(_STATE.get("requests_claimed", 0)) >= _request_cap():
            _STATE["requests_denied"] = int(_STATE.get("requests_denied", 0)) + 1
            _STATE["last_stop_reason"] = "request_budget_exhausted"
            return False, "request_budget_exhausted"
        _STATE["requests_claimed"] = int(_STATE.get("requests_claimed", 0)) + 1
        counts = _STATE.setdefault("endpoint_counts", Counter())
        counts[_endpoint(path)] += 1
        return True, ""


def snapshot() -> dict[str, Any]:
    with _LOCK:
        endpoint_counts = dict(_STATE.get("endpoint_counts") or {})
        elapsed = round(_elapsed_locked(), 3)
        return {
            "status": "installed" if _INSTALLED else "not_installed",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "request_cap": _request_cap(),
            "wall_seconds": _wall_seconds(),
            "detail_match_limit": _detail_match_limit(),
            "comparison_match_limit": _comparison_limit(),
            "http_timeout_seconds": _timeout_seconds(),
            "requests_claimed": int(_STATE.get("requests_claimed", 0)),
            "requests_denied": int(_STATE.get("requests_denied", 0)),
            "elapsed_seconds": elapsed,
            "endpoint_counts": endpoint_counts,
            "last_stop_reason": str(_STATE.get("last_stop_reason") or ""),
            "per_event_metadata_enabled": False,
            "per_event_prediction_enabled": False,
            "bulk_prediction_provider_preserved": True,
            "publication_contract_relaxed": False,
        }


def _write(extra: dict[str, Any] | None = None) -> None:
    payload = snapshot()
    if extra:
        payload.update(extra)
    for path in (OUT, ART):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except Exception:
            pass


def reset_for_tests(*, started_monotonic: float | None = None) -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update(
            {
                "started_monotonic": started_monotonic,
                "requests_claimed": 0,
                "requests_denied": 0,
                "endpoint_counts": Counter(),
                "last_stop_reason": "",
            }
        )


def install() -> dict[str, Any]:
    global _INSTALLED
    if not _truthy(os.getenv("BZZOIRO_RUNTIME_HARD_BUDGET_ENABLED"), True):
        return {"status": "disabled_by_env"}
    if _INSTALLED:
        return snapshot()

    # These settings are intentionally authoritative for the regular run.  The
    # bulk predictions endpoint supplies pre-match model/xG data much more
    # efficiently than one prediction call per event.
    os.environ["BZZOIRO_V2_FETCH_EVENT_METADATA"] = "false"
    os.environ["BZZOIRO_V2_FETCH_EVENT_PREDICTION"] = "false"
    os.environ["BZZOIRO_REQUEST_RETRIES"] = "0"
    os.environ["BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT"] = str(_comparison_limit())
    os.environ["BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS"] = str(_comparison_limit())

    try:
        from app.providers import bzzoiro_v2
    except Exception as exc:
        result = {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
        _write(result)
        return result

    cls = getattr(bzzoiro_v2, "BzzoiroContextProvider", None)
    if cls is None:
        result = {"status": "provider_class_missing"}
        _write(result)
        return result
    if getattr(cls, "_harizon_runtime_hard_budget_patched", False):
        _INSTALLED = True
        _write({"status": "already_patched"})
        return snapshot()

    original_get_json = getattr(cls, "_get_json", None)
    original_fetch_context = getattr(cls, "fetch_context", None)
    if not callable(original_get_json) or not callable(original_fetch_context):
        result = {"status": "provider_methods_missing"}
        _write(result)
        return result

    async def budgeted_get_json(self: Any, client: Any, path: str, headers: dict[str, str], params: dict[str, Any], stats: dict[str, Any]):
        allowed, reason = _claim(path)
        if not allowed:
            if isinstance(stats, dict):
                stats["budget_exhausted"] = True
                stats["hard_budget_stop_reason"] = reason
                stats["hard_budget"] = snapshot()
            _write({"status": "budget_exhausted", "stop_reason": reason})
            return None
        return await original_get_json(self, client, path, headers, params, stats)

    async def budgeted_fetch_context(self: Any, matches: Any):
        incoming = list(matches or [])
        prioritized = incoming
        prioritizer = getattr(self, "_prioritize_matches", None)
        if callable(prioritizer):
            try:
                prioritized = list(prioritizer(incoming))
            except Exception:
                prioritized = incoming
        selected = prioritized[: _detail_match_limit()]

        saved = {
            "fetch_event_metadata": getattr(self, "fetch_event_metadata", None),
            "fetch_event_prediction": getattr(self, "fetch_event_prediction", None),
            "retries": getattr(self, "retries", None),
            "timeout": getattr(self, "timeout", None),
            "enforce_context_limit": getattr(self, "enforce_context_limit", None),
        }
        try:
            self.fetch_event_metadata = False
            self.fetch_event_prediction = False
            self.retries = 0
            self.timeout = min(float(getattr(self, "timeout", _timeout_seconds()) or _timeout_seconds()), _timeout_seconds())
            # The input was already deterministically sliced; avoid a later env
            # expanding it back to the full 300-row inventory.
            self.enforce_context_limit = False
            contexts, stats, preview = await original_fetch_context(self, selected)
        finally:
            for name, value in saved.items():
                if value is not None:
                    try:
                        setattr(self, name, value)
                    except Exception:
                        pass

        if isinstance(stats, dict):
            stats["runtime_hard_budget"] = snapshot()
            stats["matches_received_before_hard_limit"] = len(incoming)
            stats["matches_selected_after_hard_limit"] = len(selected)
            stats["per_event_metadata_disabled"] = True
            stats["per_event_prediction_disabled"] = True
        _write(
            {
                "status": "fetch_complete",
                "matches_received_before_hard_limit": len(incoming),
                "matches_selected_after_hard_limit": len(selected),
                "contexts_built": len(contexts or {}),
            }
        )
        return contexts, stats, preview

    cls._get_json = budgeted_get_json
    cls.fetch_context = budgeted_fetch_context
    cls._harizon_runtime_hard_budget_patched = True
    _INSTALLED = True
    _write({"status": "installed"})
    return snapshot()


__all__ = ["install", "snapshot", "reset_for_tests"]
