from __future__ import annotations

"""Apply a bounded but less starved Bzzoiro runtime policy.

Bzzoiro/BSD is the best context source in this project (xG, prediction, stats,
lineups, metadata and odds comparison). The previous emergency cap was too low
and often produced data 0/0 with runner_provider_deadline_exhausted. This patch
keeps a hard coroutine deadline, but gives Bzzoiro enough budget to finish a
near-window pass and targeted odds/detail bridge.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
OUT = Path(".data/exports/latest-bzzoiro-runtime-deadline.json")
ART = Path("artifacts/run-bot/latest-bzzoiro-runtime-deadline.json")

POLICY = {
    "BZZOIRO_RUNTIME_HARD_REQUEST_CAP": "96",
    "BZZOIRO_RUNTIME_ABSOLUTE_MAX_REQUESTS": "140",
    "BZZOIRO_RUNTIME_HARD_SECONDS": "130",
    "BZZOIRO_RUNTIME_ABSOLUTE_MAX_SECONDS": "165",
    "BZZOIRO_RUNTIME_DETAIL_MATCH_LIMIT": "48",
    "BZZOIRO_RUNTIME_ABSOLUTE_DETAIL_MATCH_LIMIT": "72",
    "BZZOIRO_RUNTIME_COMPARISON_MATCH_LIMIT": "32",
    "BZZOIRO_RUNTIME_ABSOLUTE_COMPARISON_MATCH_LIMIT": "48",
    "BZZOIRO_RUNTIME_HTTP_TIMEOUT_SECONDS": "8",
    "BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT": "32",
    "BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS": "48",
    "BZZOIRO_TARGET_NEAR_WINDOW_FIRST": "true",
    "BZZOIRO_CACHE_TTL_MINUTES": "180",
}


def _write(payload: dict[str, Any]) -> None:
    for path in (OUT, ART):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass


def _deadline_seconds() -> float:
    try:
        value = float(os.getenv("BZZOIRO_RUNTIME_PROVIDER_DEADLINE_SECONDS") or 145.0)
    except Exception:
        value = 145.0
    return max(45.0, min(value, 170.0))


def _budget_snapshot() -> dict[str, Any]:
    try:
        from app.services import bzzoiro_runtime_budget_patch
        value = bzzoiro_runtime_budget_patch.snapshot()
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def install() -> dict[str, Any]:
    for key, value in POLICY.items():
        os.environ[key] = value
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
    if getattr(cls, "_harizon_provider_deadline_patched", False):
        return {"status": "already_patched", "deadline_seconds": _deadline_seconds(), "policy": POLICY}
    original = getattr(cls, "fetch_context", None)
    if not callable(original):
        result = {"status": "fetch_context_missing"}
        _write(result)
        return result

    async def fetch_context_with_deadline(self: Any, matches: Any):
        deadline = _deadline_seconds()
        started = datetime.now(UTC)
        try:
            result = await asyncio.wait_for(original(self, matches), timeout=deadline)
            elapsed = (datetime.now(UTC) - started).total_seconds()
            _write({
                "status": "completed",
                "created_at_utc": datetime.now(UTC).isoformat(),
                "deadline_seconds": deadline,
                "elapsed_seconds": round(elapsed, 3),
                "hard_budget": _budget_snapshot(),
                "policy": POLICY,
                "publication_contract_relaxed": False,
            })
            return result
        except TimeoutError:
            elapsed = (datetime.now(UTC) - started).total_seconds()
            hard_budget = _budget_snapshot()
            requests = int(hard_budget.get("requests_claimed") or 0)
            stats = {
                "enabled": bool(getattr(self, "api_key", None)),
                "api_key_present": bool(getattr(self, "api_key", None)),
                "requests": requests,
                "response_errors": 0,
                "contexts_built": 0,
                "budget_exhausted": True,
                "hard_budget_stop_reason": "provider_coroutine_deadline_exhausted",
                "hard_budget": hard_budget,
                "provider_deadline_seconds": deadline,
                "provider_deadline_elapsed_seconds": round(elapsed, 3),
                "publication_contract_relaxed": False,
            }
            preview = {"matched_examples": [], "unmatched_examples": [], "deadline_exhausted": True}
            _write({
                "status": "deadline_exhausted",
                "created_at_utc": datetime.now(UTC).isoformat(),
                "deadline_seconds": deadline,
                "elapsed_seconds": round(elapsed, 3),
                "hard_budget": hard_budget,
                "policy": POLICY,
                "publication_contract_relaxed": False,
            })
            return {}, stats, preview

    cls.fetch_context = fetch_context_with_deadline
    cls._harizon_provider_deadline_patched = True
    payload = {
        "status": "installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "deadline_seconds": _deadline_seconds(),
        "policy": POLICY,
        "publication_contract_relaxed": False,
    }
    _write(payload)
    return payload


__all__ = ["install"]
