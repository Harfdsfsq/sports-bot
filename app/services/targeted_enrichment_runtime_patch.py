from __future__ import annotations

"""Runtime patch that turns broad context providers into per-run targeted enrichers.

It does not call external APIs directly.  It only changes provider shortlists and
therefore protects free quotas while the normal provider code keeps its own
per-run request budgets.
"""

import os
from typing import Any

PATCH_MARKER = "_harizon_targeted_enrichment_runtime_patch_v1"


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def install() -> bool:
    if not _truthy("TARGETED_ENRICHMENT_QUEUE_ENABLED", True):
        return False
    try:
        from app.services.runner import PredictionRunner
        from app.services import targeted_enrichment_queue as queue
    except Exception:
        return False
    if getattr(PredictionRunner, PATCH_MARKER, False):
        return False

    original_select = getattr(PredictionRunner, "_select_provider_context_matches", None)
    original_context = getattr(PredictionRunner, "_select_context_enrichment_matches", None)
    if not callable(original_select) or not callable(original_context):
        return False

    reports: list[dict[str, Any]] = []

    def select_provider_context_matches_patched(
        self: Any,
        matches: list[Any],
        provider_name: str,
        *,
        fallback_matches: list[Any] | None = None,
        offers_by_match: dict[str, list[Any]] | None = None,
    ) -> list[Any]:
        provider_key = str(provider_name or "").strip().lower()
        if provider_key == "sportlogic" and _truthy("SPORTLOGIC_TARGETED_ONLY_AFTER_VALUE_CANDIDATES", True):
            # SportLogic is quota-sensitive; keep it out of broad context enrichment.
            reports.append({"provider": provider_key, "selected_matches": 0, "limit": 0, "reason": "sportlogic_targeted_only"})
            queue.write_queue_report(reports[-20:])
            return []
        base = original_select(
            self,
            matches,
            provider_name,
            fallback_matches=fallback_matches,
            offers_by_match=offers_by_match,
        )
        if provider_key in {"sstats", "bzzoiro", "thesportsdb", "football_data", "futrixmetrics", "api_football", "newsapi", "gnews", "openfootball", "openligadb", "espn"}:
            selected, info = queue.select_for_provider(
                base or matches or [],
                provider_key,
                offers_by_match or {},
                fallback_matches=fallback_matches,
            )
            reports.append(info)
            queue.write_queue_report(reports[-20:])
            return selected
        return base

    def select_context_enrichment_matches_patched(
        self: Any,
        matches: list[Any],
        offers_by_match: dict[str, list[Any]],
        now_utc: Any,
        market_signals_by_match: dict[str, dict[str, Any]] | None = None,
    ):
        selected, summary = original_context(self, matches, offers_by_match, now_utc, market_signals_by_match)
        if not _truthy("TARGETED_ENRICHMENT_REORDER_CORE_QUEUE", True):
            return selected, summary
        ranked = queue.rank_matches(selected, "core", offers_by_match)
        limit = queue.env_int("TARGETED_ENRICHMENT_CORE_MATCH_LIMIT", len(ranked), 0)
        if limit > 0:
            ranked = ranked[:limit]
        summary = dict(summary or {})
        summary["targeted_queue_enabled"] = True
        summary["targeted_queue_selected"] = len(ranked)
        summary["targeted_queue_core_limit"] = limit
        return ranked, summary

    PredictionRunner._select_provider_context_matches = select_provider_context_matches_patched
    PredictionRunner._select_context_enrichment_matches = select_context_enrichment_matches_patched
    setattr(PredictionRunner, PATCH_MARKER, True)
    return True
