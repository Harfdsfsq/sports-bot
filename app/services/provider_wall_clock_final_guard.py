"""Final runner-level wall clock for the Bzzoiro context provider.

Provider methods are wrapped by several compatibility modules.  A deadline attached
to ``BzzoiroContextProvider.fetch_context`` can therefore be replaced later in the
startup sequence.  ``PredictionRunner._fetch_provider`` is the final call boundary
used by the production runner, so enforcing the deadline here cannot be bypassed by
later provider-method wrappers.

This installer is also the post-discovery bootstrap point for the cumulative daily
coverage orchestrator.  app.cli calls it immediately before PredictionRunner is
instantiated, after the day inventory exists and after legacy provider wrappers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUT = Path(".data/exports/latest-provider-wall-clock-final-guard.json")
ART = Path("artifacts/run-bot/latest-provider-wall-clock-final-guard.json")


def _float_env(name: str, default: float, minimum: float = 1.0) -> float:
    try:
        value = float(str(os.getenv(name) or default).strip())
    except Exception:
        value = default
    return max(minimum, value)


def _deadline_seconds() -> float:
    requested = _float_env("BZZOIRO_RUNNER_CONTEXT_DEADLINE_SECONDS", 55.0, 10.0)
    absolute = _float_env("BZZOIRO_RUNNER_CONTEXT_ABSOLUTE_MAX_SECONDS", 70.0, 10.0)
    return min(requested, absolute)


def _write(payload: dict[str, Any]) -> None:
    value = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "deadline_seconds": _deadline_seconds(),
        "publication_contract_relaxed": False,
        **payload,
    }
    for path in (OUT, ART):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except Exception:
            pass


def _provider_name(runner: Any, provider: Any) -> str:
    resolver = getattr(runner, "_provider_name", None)
    if callable(resolver):
        try:
            return str(resolver(provider) or "").strip().lower()
        except Exception:
            pass
    module_name = str(getattr(getattr(provider, "__class__", None), "__module__", "") or "").lower()
    class_name = str(getattr(getattr(provider, "__class__", None), "__name__", "") or "").lower()
    if "bzzoiro" in module_name or "bzzoiro" in class_name:
        return "bzzoiro"
    return module_name.rsplit(".", 1)[-1] or class_name or "unknown"


def _hard_budget_snapshot() -> dict[str, Any]:
    try:
        from app.services import bzzoiro_runtime_budget_patch

        value = bzzoiro_runtime_budget_patch.snapshot()
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _install_daily_coverage() -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        from app.services.daily_coverage_plan import prepare_daily_coverage

        plan = prepare_daily_coverage()
        result["plan"] = {
            "status": plan.get("status"),
            "run_index": plan.get("run_index"),
            "phase_cumulative_target": plan.get("phase_cumulative_target"),
            "top_inventory_matches": plan.get("top_inventory_matches"),
        }
    except Exception as exc:
        result["plan_error"] = f"{type(exc).__name__}: {exc}"
    try:
        from app.services.daily_coverage_runtime_patch import (
            install as install_daily_runtime,
        )

        result["runtime_patch"] = install_daily_runtime()
    except Exception as exc:
        result["runtime_patch_error"] = f"{type(exc).__name__}: {exc}"
    return result


def install() -> dict[str, Any]:
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        result = {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
        _write(result)
        return result

    current = getattr(PredictionRunner, "_fetch_provider", None)
    if not callable(current):
        result = {"status": "runner_method_missing"}
        _write(result)
        return result
    if getattr(current, "_harizon_provider_wall_clock_final_guard", False):
        result = {"status": "already_patched", "daily_coverage": _install_daily_coverage()}
        _write(result)
        return result

    original = current

    async def fetch_provider_with_final_deadline(
        self: Any,
        provider: Any | None,
        method_name: str,
        *args: Any,
        empty_data: Any,
    ) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        provider_name = _provider_name(self, provider)
        if provider_name != "bzzoiro" or str(method_name) != "fetch_context":
            return await original(self, provider, method_name, *args, empty_data=empty_data)

        deadline = _deadline_seconds()
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                original(self, provider, method_name, *args, empty_data=empty_data),
                timeout=deadline,
            )
        except TimeoutError:
            elapsed = round(time.monotonic() - started, 3)
            hard_budget = _hard_budget_snapshot()
            stats = {
                "enabled": provider is not None,
                "api_key_present": bool(getattr(provider, "api_key", None)),
                "requests": int(hard_budget.get("requests_claimed") or 0),
                "response_errors": 0,
                "contexts_built": 0,
                "budget_exhausted": True,
                "hard_budget_stop_reason": "runner_provider_deadline_exhausted",
                "runner_provider_deadline_seconds": deadline,
                "runner_provider_elapsed_seconds": elapsed,
                "runtime_hard_budget": hard_budget,
                "publication_contract_relaxed": False,
            }
            preview = {
                "deadline_exhausted": True,
                "provider": "bzzoiro",
                "method": str(method_name),
            }
            marker = getattr(self, "_mark_provider_status", None)
            if callable(marker):
                with contextlib.suppress(Exception):
                    marker(
                        "bzzoiro",
                        degraded=True,
                        budget_exhausted=True,
                        stop_reason="runner_provider_deadline_exhausted",
                    )
            _write(
                {
                    "status": "deadline_exhausted",
                    "provider": "bzzoiro",
                    "method": str(method_name),
                    "elapsed_seconds": elapsed,
                    "hard_budget": hard_budget,
                }
            )
            return empty_data, stats, preview

        elapsed = round(time.monotonic() - started, 3)
        _write(
            {
                "status": "completed",
                "provider": "bzzoiro",
                "method": str(method_name),
                "elapsed_seconds": elapsed,
                "hard_budget": _hard_budget_snapshot(),
            }
        )
        return result

    fetch_provider_with_final_deadline._harizon_provider_wall_clock_final_guard = True  # type: ignore[attr-defined]
    fetch_provider_with_final_deadline._harizon_wrapped_method = original  # type: ignore[attr-defined]
    PredictionRunner._fetch_provider = fetch_provider_with_final_deadline  # type: ignore[method-assign]

    result = {
        "status": "installed",
        "deadline_seconds": _deadline_seconds(),
        "boundary": "PredictionRunner._fetch_provider:bzzoiro.fetch_context",
        "daily_coverage": _install_daily_coverage(),
    }
    _write(result)
    return result


__all__ = ["install"]
