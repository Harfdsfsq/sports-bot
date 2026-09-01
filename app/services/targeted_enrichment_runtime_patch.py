from __future__ import annotations

"""Runtime patch that turns broad context providers into per-run targeted enrichers.

It does not call external APIs directly. It only changes provider shortlists and
therefore protects free quotas while the normal provider code keeps its own
per-run request budgets.

The queue is also responsible for keeping the current model scope complete:
provider enrichment must be allowed to add no-offer matches to the same run,
otherwise context is fetched only for the already-covered subset and the result
can never repair the coverage gaps reported by the bot.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PATCH_MARKER = "_harizon_targeted_enrichment_runtime_patch_v3"
UTC = timezone.utc
STATUS_PATH = Path(".data/exports/latest-targeted-enrichment-runtime-patch.json")


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _write_status(payload: dict[str, Any]) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"created_at_utc": datetime.now(UTC).isoformat(), **payload}
        STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def merge_target_pools(*pools: list[Any] | None) -> list[Any]:
    """Merge provider target pools without dropping fallback inventory rows."""
    merged: list[Any] = []
    seen: set[str] = set()
    for pool in pools:
        for item in pool or []:
            key = str(getattr(item, "match_key", "") or "").strip()
            if not key:
                key = f"object:{id(item)}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def call_without_offer_gate(settings: Any, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a context selector with the offer gate disabled, then restore settings."""
    marker = object()
    previous = getattr(settings, "context_enrichment_requires_offers", marker)
    try:
        setattr(settings, "context_enrichment_requires_offers", False)
        return callback(*args, **kwargs)
    finally:
        if previous is marker:
            try:
                delattr(settings, "context_enrichment_requires_offers")
            except Exception:
                pass
        else:
            setattr(settings, "context_enrichment_requires_offers", previous)


def install() -> bool:
    if not _truthy("TARGETED_ENRICHMENT_QUEUE_ENABLED", True):
        _write_status({"status": "disabled", "reason": "TARGETED_ENRICHMENT_QUEUE_ENABLED=false"})
        return False
    try:
        from app.services.runner import PredictionRunner
        from app.services import targeted_enrichment_queue as queue
    except Exception as exc:
        _write_status({"status": "error", "stage": "import", "error": f"{type(exc).__name__}: {exc}"})
        return False
    if getattr(PredictionRunner, PATCH_MARKER, False):
        _write_status({"status": "already_installed", "patch_marker": PATCH_MARKER})
        return True

    original_select = getattr(PredictionRunner, "_select_provider_context_matches", None)
    original_context = getattr(PredictionRunner, "_select_context_enrichment_matches", None)
    if not callable(original_select) or not callable(original_context):
        _write_status({
            "status": "not_installed",
            "reason": "runner_selection_methods_missing",
            "has_select_provider_context_matches": callable(original_select),
            "has_select_context_enrichment_matches": callable(original_context),
        })
        return False

    reports: list[dict[str, Any]] = []

    def _report(row: dict[str, Any]) -> None:
        reports.append(row)
        queue.write_queue_report(reports[-40:])

    def select_provider_context_matches_patched(
        self: Any,
        matches: list[Any],
        provider_name: str,
        *,
        fallback_matches: list[Any] | None = None,
        offers_by_match: dict[str, list[Any]] | None = None,
    ) -> list[Any]:
        provider_key = str(provider_name or "").strip().lower()
        # The old flag disabled SportLogic before current fixtures were tested.
        # Keep an explicit kill switch, but do not let the legacy targeting flag
        # silently turn a configured provider into a no-op.
        if provider_key == "sportlogic" and _truthy("SPORTLOGIC_DISABLE_CONTEXT_ENRICHMENT", False):
            _report({"provider": provider_key, "selected_matches": 0, "limit": 0, "reason": "sportlogic_disabled_explicitly"})
            return []
        base = original_select(
            self,
            matches,
            provider_name,
            fallback_matches=fallback_matches,
            offers_by_match=offers_by_match,
        )
        targeted_providers = {
            "sstats",
            "bzzoiro",
            "thesportsdb",
            "football_data",
            "futrixmetrics",
            "api_football",
            "allsportsapi",
            "newsapi",
            "gnews",
            "openfootball",
            "openligadb",
            "espn",
            "weather",
            "sportlogic",
        }
        if provider_key in targeted_providers:
            # `base or matches` used to discard fallback inventory whenever base
            # was non-empty. That made enrichment operate on the already-covered
            # subset and left no-offer coverage gaps outside the model scope.
            candidate_pool = merge_target_pools(base, matches, fallback_matches)
            selected, info = queue.select_for_provider(
                candidate_pool,
                provider_key,
                offers_by_match or {},
                fallback_matches=[],
            )
            info = dict(info or {})
            info["candidate_pool_matches"] = len(candidate_pool)
            info["base_matches"] = len(base or [])
            info["fallback_matches"] = len(fallback_matches or [])
            info["candidate_pool_expanded"] = len(candidate_pool) > len(base or [])
            _report(info)
            return selected
        return base

    def select_context_enrichment_matches_patched(
        self: Any,
        matches: list[Any],
        offers_by_match: dict[str, list[Any]],
        now_utc: Any,
        market_signals_by_match: dict[str, dict[str, Any]] | None = None,
    ):
        requested_gate = bool(getattr(self.settings, "context_enrichment_requires_offers", True))
        selected, summary = call_without_offer_gate(
            self.settings,
            original_context,
            self,
            matches,
            offers_by_match,
            now_utc,
            market_signals_by_match,
        )
        if not _truthy("TARGETED_ENRICHMENT_REORDER_CORE_QUEUE", True):
            return selected, summary
        ranked = queue.rank_matches(
            merge_target_pools(selected, matches),
            "core",
            offers_by_match,
        )
        limit = queue.env_int("TARGETED_ENRICHMENT_CORE_MATCH_LIMIT", len(ranked), 0)
        if limit > 0:
            ranked = ranked[:limit]
        summary = dict(summary or {})
        summary["targeted_queue_enabled"] = True
        summary["targeted_queue_selected"] = len(ranked)
        summary["targeted_queue_core_limit"] = limit
        summary["offer_gate_requested"] = requested_gate
        summary["offer_gate_effective"] = False
        summary["offer_gate_override_reason"] = "enrichment_must_repair_no_offer_coverage"
        summary["candidate_pool_matches"] = len(merge_target_pools(selected, matches))
        return ranked, summary

    PredictionRunner._select_provider_context_matches = select_provider_context_matches_patched
    PredictionRunner._select_context_enrichment_matches = select_context_enrichment_matches_patched
    setattr(PredictionRunner, PATCH_MARKER, True)
    _write_status({"status": "installed", "patch_marker": PATCH_MARKER})
    queue.write_queue_report([{"provider": "startup", "selected_matches": 0, "limit": 0, "status": "installed", "offer_gate_effective": False}])
    return True
