"""Activate strict dual-source repairs on the real production startup path.

Production keeps ``LEGACY_RUNTIME_EXTENSIONS_ENABLED=false``.  Repairs that only live
in ``runtime_startup_chain`` therefore never run.  This installer is loaded from
``app.providers`` and re-applies the repairs after every native preflight setup, before
discovery spends provider quota and again before ``PredictionRunner`` starts.

It does not change cron/workflow or publication, value, movement and integrity guards.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.services import daily_coverage_ledger as coverage_ledger

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-strict-coverage-native-activation.json"
_INSTALLED = False
_ORIGINAL_APPLY_SAFE_DEFAULTS = None
_ORIGINAL_RUN_BEFORE_PREDICTION = None

_LINE_POINTS = {
    "over_under_15": 1.5,
    "over_under_25": 2.5,
    "over_under_35": 3.5,
}
_SYNTHETIC_BOOKS = {
    "bzzoiro",
    "bzzoirobest",
    "bzzoiroconsensus",
    "oddssafariconsensus",
}


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _synthetic_book(value: Any) -> bool:
    compact = "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())
    return not compact or compact in _SYNTHETIC_BOOKS


def _fixed_line_from_factory(original: Any):
    def line_from(*values: Any) -> float | None:
        text = " ".join(str(value or "").strip().lower() for value in values)
        for market, point in _LINE_POINTS.items():
            if market in text:
                return point
        return original(*values)

    line_from._harizon_bzzoiro_market_point_fix = True  # type: ignore[attr-defined]
    return line_from


def _real_book_add_offer_factory(original: Any):
    def add_offer(out: Any, seen: Any, source: str, book: Any, *args: Any, **kwargs: Any) -> Any:
        if str(source or "").strip().lower().startswith("bzzoiro") and _synthetic_book(book):
            return None
        return original(out, seen, source, book, *args, **kwargs)

    add_offer._harizon_real_bzzoiro_book_only = True  # type: ignore[attr-defined]
    return add_offer


def _persistent_context_factory(batch_fetch: Any):
    async def fetch_context(self: Any, matches: list[Any]):
        deadline = max(15.0, float(os.getenv("BZZOIRO_NATIVE_BATCH_CONTEXT_DEADLINE_SECONDS", "55") or 55))
        try:
            contexts, stats, preview = await asyncio.wait_for(batch_fetch(self, matches), timeout=deadline)
        except TimeoutError:
            return {}, {
                "enabled": True,
                "runtime_error": "native_batch_context_deadline_exhausted",
                "deadline_seconds": deadline,
                "contexts_built": 0,
                "publication_contract_relaxed": False,
            }, {"deadline_exhausted": True}
        if isinstance(contexts, dict) and contexts:
            coverage_ledger.record_provider_result("bzzoiro", "fetch_context", contexts, stats)
        return contexts, stats, preview

    fetch_context._harizon_batch_context_persisted = True  # type: ignore[attr-defined]
    return fetch_context


def _batch_bzzoiro_odds_factory(merge_module: Any):
    async def fetch_bzzoiro(settings: Any, matches: list[Any], base: dict[str, list[Any]], _amap: dict[str, dict[str, str]]):
        key = os.getenv("BZZOIRO_API_KEY") or getattr(settings, "bzzoiro_api_key", None)
        stats: dict[str, Any] = {
            "enabled": bool(key),
            "mode": "documented_odds_best_batch_only",
            "requests": 0,
            "response_errors": 0,
            "events_fetched": 0,
            "events_matched": 0,
            "event_odds_requests": 0,
            "event_comparison_requests": 0,
            "odds_best_requests": 0,
            "odds_best_rows": 0,
            "odds_best_matched": 0,
            "offers_parsed": 0,
            "offers_from_compact_odds": 0,
            "offers_from_comparison": 0,
            "offers_from_best": 0,
            "publication_contract_relaxed": False,
        }
        if not key:
            return {}, stats
        target = merge_module.selected_matches(matches, base)
        if not target:
            return {}, stats
        api = str(os.getenv("BZZOIRO_BASE_URL") or "https://sports.bzzoiro.com/api/v2").rstrip("/")
        headers = {"Authorization": f"Token {key}"}
        date_from = min(match.commence_time.astimezone(UTC).date() for match in target).isoformat()
        date_to = (max(match.commence_time.astimezone(UTC).date() for match in target) + timedelta(days=1)).isoformat()
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            offers = await merge_module.fetch_bzzoiro_best_odds(
                client, api, headers, target, date_from, date_to, stats
            )
        stats["offers_parsed"] = sum(len(rows or []) for rows in offers.values())
        return offers, stats

    fetch_bzzoiro._harizon_bzzoiro_best_batch_only = True  # type: ignore[attr-defined]
    return fetch_bzzoiro


def _persistent_bzzoiro_odds_factory(original: Any):
    async def fetch_bzzoiro(settings: Any, matches: list[Any], base: dict[str, list[Any]], amap: dict[str, dict[str, str]]):
        offers, stats = await original(settings, matches, base, amap)
        now = datetime.now(UTC).isoformat()
        if isinstance(offers, dict):
            for rows in offers.values():
                for offer in rows or []:
                    metadata = dict(getattr(offer, "metadata", {}) or {})
                    metadata.setdefault("fetched_at_utc", now)
                    metadata.setdefault("provider_source", "bzzoiro")
                    offer.metadata = metadata
            if offers:
                coverage_ledger.record_provider_result("bzzoiro", "fetch_offers", offers, stats)
        return offers, stats

    fetch_bzzoiro._harizon_bzzoiro_odds_persisted = True  # type: ignore[attr-defined]
    return fetch_bzzoiro


def _cached_bzzoiro_offer_factory(original: Any, merge_func: Any):
    async def fetch_offers(self: Any, matches: list[Any]):
        data, stats, preview = await original(self, matches)
        pool = {key: list(value or []) for key, value in dict(data or {}).items()}
        cached = coverage_ledger.cached_provider_data("bzzoiro", "fetch_offers", matches)
        added = merge_func(pool, cached)
        stats = dict(stats or {})
        stats["bzzoiro_cached_evidence_matches"] = len(cached)
        stats["bzzoiro_cached_evidence_offers_added"] = added
        preview = dict(preview or {})
        preview["bzzoiro_cached_evidence"] = {
            "matches": len(cached),
            "offers_added": added,
        }
        return pool, stats, preview

    fetch_offers._harizon_bzzoiro_cache_injected = True  # type: ignore[attr-defined]
    return fetch_offers


def _reassert() -> dict[str, Any]:
    result: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "publication_contract_relaxed": False,
    }
    try:
        from app.providers.bzzoiro_v2 import BzzoiroContextProvider
        from app.providers.odds_api_io import OddsApiIoProvider
        from app.services.strict_coverage_runtime_repair import (
            fetch_context_batch_predictions,
            score_event_match_compat,
        )
        from app.services.strict_coverage_runtime_repair import (
            install as install_repair,
        )

        from app.services import sstats_bzzoiro_odds_merge_patch as merge_module

        result["odds_merge_install"] = merge_module.install()
        result["strict_runtime_repair"] = install_repair()
        os.environ["CORE_ODDS_PATCH_MATCH_LIMIT"] = "300"
        os.environ["BZZOIRO_CONTEXT_MATCH_LIMIT"] = "300"
        os.environ["BZZOIRO_V2_INVENTORY_TARGET_LIMIT"] = "300"
        os.environ.setdefault("BZZOIRO_PREDICTIONS_PAGE_SIZE", "200")
        os.environ.setdefault("BZZOIRO_PREDICTIONS_MAX_PAGES", "10")
        os.environ["BZZOIRO_CONTEXT_DETAIL_GAP_LIMIT"] = "0"
        # Context must come from Bzzoiro prediction/model data, not be inferred from
        # the same odds line that is used as the independent price source.
        os.environ["BZZOIRO_CONTEXT_ODDS_FALLBACK_LIMIT"] = "0"
        # The old pool-ID prefill performs event detail + odds + stats + comparison
        # for every row and consumed all 48 Bzzoiro claims in run 29697518250.
        # Bulk event/prediction pages already contain the IDs needed for matching.
        os.environ["BZZOIRO_POOL_ID_INVENTORY_ENRICHMENT_ENABLED"] = "false"
        os.environ["BZZOIRO_V2_FETCH_ODDS_COMPARISON"] = "false"
        os.environ["BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT"] = "0"
        os.environ["BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS"] = "0"
        os.environ["BZZOIRO_BEST_ODDS_MARKETS"] = "1x2,over_under_15,over_under_25,over_under_35"
        os.environ["BZZOIRO_ODDS_BEST_MAX_PAGES_PER_MARKET"] = "3"
        os.environ["BZZOIRO_ODDS_BEST_PAGE_SIZE"] = "200"

        merge_module.score_event_match = score_event_match_compat
        if not getattr(merge_module.line_from, "_harizon_bzzoiro_market_point_fix", False):
            merge_module.line_from = _fixed_line_from_factory(merge_module.line_from)
        if not getattr(merge_module.add_offer, "_harizon_real_bzzoiro_book_only", False):
            merge_module.add_offer = _real_book_add_offer_factory(merge_module.add_offer)
        if not getattr(merge_module.fetch_bzzoiro, "_harizon_bzzoiro_odds_persisted", False):
            batch_odds = _batch_bzzoiro_odds_factory(merge_module)
            merge_module.fetch_bzzoiro = _persistent_bzzoiro_odds_factory(batch_odds)

        current_context = BzzoiroContextProvider.fetch_context
        if not getattr(current_context, "_harizon_batch_context_persisted", False):
            BzzoiroContextProvider.fetch_context = _persistent_context_factory(fetch_context_batch_predictions)  # type: ignore[assignment]

        current_odds = OddsApiIoProvider.fetch_offers
        if not getattr(current_odds, "_harizon_bzzoiro_cache_injected", False):
            OddsApiIoProvider.fetch_offers = _cached_bzzoiro_offer_factory(current_odds, merge_module.merge)  # type: ignore[assignment]

        result.update(
            {
                "status": "installed",
                "production_native_path": True,
                "batch_context_persisted": True,
                "bzzoiro_odds_persisted": True,
                "bzzoiro_odds_mode": "documented_odds_best_batch_only",
                "cached_bzzoiro_offers_injected": True,
                "score_event_match_compat": True,
                "total_market_points_fixed": _LINE_POINTS,
                "synthetic_bookmaker_quorum_blocked": True,
                "full_cohort_limit": 300,
            }
        )
    except Exception as exc:
        result.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    _write(result)
    return result


def _install_preflight_wrappers() -> dict[str, Any]:
    global _ORIGINAL_APPLY_SAFE_DEFAULTS, _ORIGINAL_RUN_BEFORE_PREDICTION
    from app.services.runtime_preflight import RuntimePreflight

    current_apply = RuntimePreflight.apply_safe_defaults
    if not getattr(current_apply, "_harizon_strict_coverage_native", False):
        _ORIGINAL_APPLY_SAFE_DEFAULTS = current_apply

        def apply_safe_defaults(self: Any) -> int:
            assert callable(_ORIGINAL_APPLY_SAFE_DEFAULTS)
            changed = _ORIGINAL_APPLY_SAFE_DEFAULTS(self)
            self._harizon_strict_coverage_activation = _reassert()
            return changed

        apply_safe_defaults._harizon_strict_coverage_native = True  # type: ignore[attr-defined]
        RuntimePreflight.apply_safe_defaults = apply_safe_defaults  # type: ignore[assignment]

    current_run = RuntimePreflight.run_before_prediction
    if not getattr(current_run, "_harizon_strict_coverage_native", False):
        _ORIGINAL_RUN_BEFORE_PREDICTION = current_run

        def run_before_prediction(self: Any, stage: str = "after_discovery_before_runner"):
            before = _reassert()
            assert callable(_ORIGINAL_RUN_BEFORE_PREDICTION)
            report = _ORIGINAL_RUN_BEFORE_PREDICTION(self, stage)
            after = _reassert()
            try:
                discovery = report.discovery_first if isinstance(report.discovery_first, dict) else {}
                discovery["strict_coverage_native_activation_before"] = before
                discovery["strict_coverage_native_activation_after"] = after
                report.discovery_first = discovery
                self.write_report(report)
            except Exception:
                pass
            return report

        run_before_prediction._harizon_strict_coverage_native = True  # type: ignore[attr-defined]
        RuntimePreflight.run_before_prediction = run_before_prediction  # type: ignore[assignment]

    return {"status": "installed", "apply_safe_defaults_wrapped": True, "run_before_prediction_wrapped": True}


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    try:
        result = {
            "status": "installed",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "wrappers": _install_preflight_wrappers(),
            "publication_contract_relaxed": False,
        }
    except Exception as exc:
        result = {
            "status": "error",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "error": f"{type(exc).__name__}: {exc}",
            "publication_contract_relaxed": False,
        }
    _write(result)
    return result


__all__ = ["_fixed_line_from_factory", "_synthetic_book", "install"]
