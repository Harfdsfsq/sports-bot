from __future__ import annotations

"""Absolute-final installer for windowed core coverage.

`sitecustomize` can load before dependencies are installed, and `usercustomize`
loads many runtime wrappers after it. This module deliberately forces the
windowed-core policy to be the final PredictionRunner / CandidateFactory wrapper.

Important: the windowed-core policy must not kill raw candidates. Raw candidates
are needed for diagnostics, quality analysis and controlled reserve evaluation.
This finalizer therefore rewires the runtime patch into two stages:

1. CandidateFactory annotation/audit only.
2. PredictionRunner publishable-stage blocking.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / ".data" / "exports" / "latest-windowed-core-finalizer.json"
PUBLISH_FILTER_REPORT = ROOT / ".data" / "exports" / "latest-windowed-core-publication-filter.json"


def _write(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _safe_write_json(path: Path, payload: dict[str, Any] | None) -> None:
    if not isinstance(payload, dict):
        return
    _write(path, payload)


def _set_defaults() -> None:
    defaults = {
        "WINDOWED_CORE_COVERAGE_ENABLED": "true",
        "WINDOWED_CORE_COVERAGE_DROP_RAW_CANDIDATES": "false",
        "CORE_COVERAGE_WINDOW_HOURS": "4",
        "CORE_COVERAGE_CRON_INTERVAL_HOURS": "2",
        "CORE_COVERAGE_MIN_ODDS_SOURCES": "2",
        "CORE_COVERAGE_MIN_CONTEXT_SOURCES": "2",
        "CORE_COVERAGE_MIN_CORE_PROVIDERS": "2",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "ENABLE_ODDS_API_IO": "true",
        "SSTATS_ENABLED": "true",
        "ENABLE_SSTATS_CONTEXT": "true",
        "ENABLE_BZZOIRO_CONTEXT": "true",
        "BZZOIRO_V2_ENRICHMENT_ENABLED": "true",
        "SSTATS_DEEP_ENDPOINTS_ENABLED": "true",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def _find_callable_in_closure(fn: Callable[..., Any] | None) -> Callable[..., Any] | None:
    if fn is None:
        return None
    closure = getattr(fn, "__closure__", None) or []
    for cell in closure:
        try:
            value = cell.cell_contents
        except Exception:
            continue
        if callable(value) and value is not fn:
            # The windowed runtime wrapper closes over the pre-windowed build
            # function as `original_build_candidates`.
            return value
    return None


def _install_candidate_annotation_wrapper(patch: Any, payload: dict[str, Any]) -> None:
    from app.services.model import CandidateFactory

    current = CandidateFactory.build_candidates
    base = _find_callable_in_closure(current) or current

    def build_candidates_windowed_audit_only(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        candidates, rejections, debug = base(
            self,
            matches,
            offers_by_match,
            contexts_by_match,
            market_signals_by_match=market_signals_by_match,
        )
        rejections = dict(rejections or {})
        debug = dict(debug or {})
        if not patch._truthy(os.getenv("WINDOWED_CORE_COVERAGE_ENABLED"), True):
            return candidates, rejections, debug

        now = datetime.now(UTC)
        context_cov = patch._build_context_coverage(contexts_by_match)
        offer_cov = {str(k): patch._offer_sources_from_map(v) for k, v in dict(offers_by_match or {}).items()}
        min_odds = max(1, patch._to_int(os.getenv("CORE_COVERAGE_MIN_ODDS_SOURCES") or 2, 2))
        min_context = max(1, patch._to_int(os.getenv("CORE_COVERAGE_MIN_CONTEXT_SOURCES") or 2, 2))
        min_core = max(1, patch._to_int(os.getenv("CORE_COVERAGE_MIN_CORE_PROVIDERS") or 2, 2))

        accepted = 0
        blocked = 0
        blocked_rows: list[dict[str, Any]] = []
        for candidate in list(candidates or []):
            match_key = str(getattr(candidate, "match_key", "") or "")
            odds_sources = patch._offer_sources_from_candidate(candidate) | offer_cov.get(match_key, set())
            context_sources = set(context_cov.get(match_key, set()))
            summary = dict(getattr(candidate, "source_summary", {}) or {})
            context_sources |= patch._provider_tokens_from_payload(summary.get("context_source"))
            context_sources |= patch._provider_tokens_from_payload(summary.get("context_sources"))
            context_sources |= patch._provider_tokens_from_payload(getattr(candidate, "analysis", {}) or {})
            core_count = len((odds_sources | context_sources) & patch.CORE_PROVIDERS)
            movement = patch._movement_status(candidate, now, getattr(self, "settings", None))
            reject_reasons: list[str] = []
            if len(odds_sources) < min_odds:
                reject_reasons.append("odds_sources_below_2")
            if len(context_sources) < min_context:
                reject_reasons.append("context_sources_below_2")
            if core_count < min_core:
                reject_reasons.append("core_api_coverage_below_2_of_3")
            if not movement.get("ok"):
                reject_reasons.append(str(movement.get("reason") or "line_movement_not_confirmed"))

            coverage = {
                "accepted": not reject_reasons,
                "reject_reasons": reject_reasons,
                "odds_sources": sorted(odds_sources),
                "context_sources": sorted(context_sources),
                "core_provider_count": core_count,
                "movement": movement,
                "stage": "candidate_audit_publish_block_only",
                "window_index": patch._window_index(candidate, now, getattr(self, "settings", None)),
            }
            try:
                candidate.source_summary["windowed_core_coverage"] = coverage
                if reject_reasons:
                    candidate.reasons.append("windowed_core_publish_block=" + "+".join(reject_reasons))
                else:
                    candidate.reasons.append("windowed_core_coverage=publish_allowed")
            except Exception:
                pass

            if reject_reasons:
                blocked += 1
                blocked_rows.append({
                    "match_key": match_key,
                    "home": getattr(candidate, "home_team", ""),
                    "away": getattr(candidate, "away_team", ""),
                    "kickoff": getattr(getattr(candidate, "commence_time", None), "isoformat", lambda: "")(),
                    "family": getattr(candidate, "family", ""),
                    "selection": getattr(candidate, "selection", ""),
                    "reject_reasons": reject_reasons,
                    "odds_sources": sorted(odds_sources),
                    "context_sources": sorted(context_sources),
                    "movement": movement,
                })
            else:
                accepted += 1

        if blocked:
            rejections["windowed_core_publish_block"] = int(rejections.get("windowed_core_publish_block", 0) or 0) + blocked

        report = {
            "created_at_utc": now.isoformat(),
            "enabled": True,
            "stage": "candidate_audit_publish_block_only",
            "window_hours": patch._window_hours(),
            "cron_interval_hours": patch._cron_interval_hours(),
            "next_cron_utc": patch._next_cron_time(now, getattr(self, "settings", None)).isoformat(),
            "candidates_in": len(candidates or []),
            "candidates_kept_for_quality": len(candidates or []),
            "publish_allowed_by_coverage": accepted,
            "publish_blocked_by_coverage": blocked,
            "min_odds_sources": min_odds,
            "min_context_sources": min_context,
            "min_core_providers": min_core,
            "blocked_sample": blocked_rows[:30],
            "context_coverage_matches": len(context_cov),
            "offer_coverage_matches": len(offer_cov),
        }
        debug["windowed_core_coverage_guard"] = report
        patch._write_report(report)
        return candidates, rejections, debug

    build_candidates_windowed_audit_only._harizon_windowed_audit_only = True  # type: ignore[attr-defined]
    CandidateFactory.build_candidates = build_candidates_windowed_audit_only  # type: ignore[assignment]
    CandidateFactory._harizon_windowed_core_guard_patch = True
    CandidateFactory._harizon_windowed_core_audit_only_patch = True
    payload["steps"].append("candidate_factory_windowed_guard_changed_to_audit_only")


def _install_publishable_filter_wrapper(payload: dict[str, Any]) -> None:
    from app.services.runner import PredictionRunner

    current = getattr(PredictionRunner, "_filter_publishable_candidates", None)
    if not callable(current):
        payload["steps"].append("publishable_filter_missing")
        return
    if getattr(current, "_harizon_windowed_publish_filter", False):
        payload["steps"].append("publishable_filter_already_wrapped")
        return

    original = current

    def filter_publishable_windowed(self, candidates):  # type: ignore[no-untyped-def]
        pool = list(original(self, candidates) or [])
        kept = []
        blocked = []
        for candidate in pool:
            coverage = dict((getattr(candidate, "source_summary", {}) or {}).get("windowed_core_coverage") or {})
            if coverage and coverage.get("accepted") is False:
                try:
                    candidate.reasons.append("windowed_core_publish_filter_blocked")
                except Exception:
                    pass
                blocked.append({
                    "match_key": getattr(candidate, "match_key", ""),
                    "home": getattr(candidate, "home_team", ""),
                    "away": getattr(candidate, "away_team", ""),
                    "family": getattr(candidate, "family", ""),
                    "selection": getattr(candidate, "selection", ""),
                    "coverage": coverage,
                })
                continue
            kept.append(candidate)
        _write(PUBLISH_FILTER_REPORT, {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "enabled": True,
            "stage": "publishable_filter",
            "input": len(pool),
            "kept": len(kept),
            "blocked": len(blocked),
            "blocked_sample": blocked[:30],
        })
        return kept

    filter_publishable_windowed._harizon_windowed_publish_filter = True  # type: ignore[attr-defined]
    PredictionRunner._filter_publishable_candidates = filter_publishable_windowed  # type: ignore[assignment]
    payload["steps"].append("prediction_runner_publishable_filter_wrapped")


def install() -> dict[str, Any]:
    _set_defaults()
    payload: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "starting",
        "forced": True,
        "raw_candidate_policy": "audit_only",
        "publication_policy": "block_without_2plus_odds_2plus_context_core_movement",
        "steps": [],
    }
    try:
        from app.services import windowed_core_coverage_runtime_patch as patch
        previous_coverage_report = _safe_read_json(patch.LATEST_REPORT)

        # Reset module/class markers so the windowed wrappers are applied after
        # all other usercustomize wrappers, not before them.
        patch._INSTALLED = False
        try:
            from app.services.runner import PredictionRunner
            for attr in ("_harizon_windowed_targets_patch",):
                if hasattr(PredictionRunner, attr):
                    delattr(PredictionRunner, attr)
        except Exception:
            pass
        try:
            from app.services.model import CandidateFactory
            for attr in ("_harizon_windowed_core_guard_patch", "_harizon_windowed_core_audit_only_patch"):
                if hasattr(CandidateFactory, attr):
                    delattr(CandidateFactory, attr)
        except Exception:
            pass
        try:
            from app.providers.bzzoiro import BzzoiroContextProvider
            for attr in ("_harizon_windowed_v2_patch",):
                if hasattr(BzzoiroContextProvider, attr):
                    delattr(BzzoiroContextProvider, attr)
        except Exception:
            pass
        try:
            from app.providers.sstats import SStatsContextProvider
            for attr in ("_harizon_deep_endpoints_patch",):
                if hasattr(SStatsContextProvider, attr):
                    delattr(SStatsContextProvider, attr)
        except Exception:
            pass
        try:
            from app.providers.odds_api_io import OddsApiIoProvider
            for attr in ("_harizon_windowed_priority_patch",):
                if hasattr(OddsApiIoProvider, attr):
                    delattr(OddsApiIoProvider, attr)
        except Exception:
            pass

        result = patch.install()
        payload["patch_result"] = result
        payload["steps"].append("windowed_core_coverage_runtime_patch_reinstalled_last")
        _install_candidate_annotation_wrapper(patch, payload)
        _install_publishable_filter_wrapper(payload)

        # patch.install() writes an install-only coverage report. If a later
        # post-run script imports usercustomize, do not overwrite the richer
        # run report produced during CandidateFactory execution.
        current_report = _safe_read_json(patch.LATEST_REPORT)
        if isinstance(previous_coverage_report, dict) and "candidates_in" in previous_coverage_report and isinstance(current_report, dict) and "candidates_in" not in current_report:
            _safe_write_json(patch.LATEST_REPORT, previous_coverage_report)
            payload["steps"].append("preserved_existing_windowed_run_report")

        payload["status"] = "installed"
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
    _write(REPORT, payload)
    return payload
