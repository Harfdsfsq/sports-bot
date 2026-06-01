from __future__ import annotations

"""Runtime source-matrix amplifier for HARIZON production runs.

The normal pipeline already reaches very good 1+ coverage, but A/B tier stalls
when matches with only one context or one odds provider are not explicitly fed
back into the next enrichment pass.  This patch is intentionally conservative:
it does not relax quality gates, it only makes the enrichment target selection
and provider scoring gap-aware.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas import Match, Offer
from app.utils import ensure_utc

_INSTALLED = False
_ORIGINAL_SELECT = None
_ORIGINAL_PROVIDER_SCORE = None


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _iter_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("matches", "rows", "items", "gap_sample", "coverage", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            return [row for row in value.values() if isinstance(row, dict)]
    return []


def _match_key_from_row(row: dict[str, Any]) -> str:
    for key in ("match_key", "key", "canonical_match_id", "canonical_key"):
        raw = row.get(key)
        if raw:
            return str(raw).strip()
    match = row.get("match") if isinstance(row.get("match"), dict) else {}
    for key in ("match_key", "key", "canonical_match_id", "canonical_key"):
        raw = match.get(key)
        if raw:
            return str(raw).strip()
    return ""


def _count_sources(value: Any) -> int:
    if isinstance(value, (list, tuple, set)):
        return len({str(item).strip().lower() for item in value if str(item).strip()})
    if isinstance(value, dict):
        return len({str(key).strip().lower() for key in value if str(key).strip()})
    return _to_int(value, 0)


def _row_counts(row: dict[str, Any]) -> tuple[int | None, int | None]:
    # Explicit progressive gap plan rows.  latest-progressive-coverage-plan.json
    # stores core_*_needed, not odds_needed/context_needed.  The first version of
    # this patch ignored those rows, so source_matrix_gap_keys stayed at zero and
    # Bzzoiro gap targets were never appended to the provider pass.
    explicit_odds_gap = _to_int(row.get("odds_needed"), 0) or _to_int(row.get("core_odds_needed"), 0)
    explicit_ctx_gap = _to_int(row.get("context_needed"), 0) or _to_int(row.get("core_context_needed"), 0)
    if explicit_odds_gap > 0 or explicit_ctx_gap > 0:
        return max(0, 2 - explicit_odds_gap), max(0, 2 - explicit_ctx_gap)

    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    progressive = row.get("progressive_coverage") if isinstance(row.get("progressive_coverage"), dict) else {}
    source_counts = row.get("source_counts") if isinstance(row.get("source_counts"), dict) else {}

    odds_candidates = [
        row.get("odds_source_count"),
        row.get("odds_sources_count"),
        row.get("independent_odds_source_count"),
        _count_sources(row.get("core_odds_sources")) if isinstance(row.get("core_odds_sources"), (list, dict, set, tuple)) else None,
        _count_sources(row.get("supplemental_odds_sources")) if isinstance(row.get("supplemental_odds_sources"), (list, dict, set, tuple)) else None,
        coverage.get("odds_source_count"),
        coverage.get("odds_sources_count"),
        progressive.get("odds_source_count"),
        progressive.get("odds_sources_count"),
        source_counts.get("odds"),
        source_counts.get("odds_sources"),
    ]
    ctx_candidates = [
        row.get("context_source_count"),
        row.get("context_sources_count"),
        _count_sources(row.get("core_context_sources")) if isinstance(row.get("core_context_sources"), (list, dict, set, tuple)) else None,
        _count_sources(row.get("supplemental_context_sources")) if isinstance(row.get("supplemental_context_sources"), (list, dict, set, tuple)) else None,
        coverage.get("context_source_count"),
        coverage.get("context_sources_count"),
        progressive.get("context_source_count"),
        progressive.get("context_sources_count"),
        source_counts.get("context"),
        source_counts.get("context_sources"),
    ]
    if isinstance(coverage.get("odds_sources"), (list, dict, set, tuple)):
        odds_candidates.append(_count_sources(coverage.get("odds_sources")))
    if isinstance(progressive.get("odds_sources"), (list, dict, set, tuple)):
        odds_candidates.append(_count_sources(progressive.get("odds_sources")))
    if isinstance(coverage.get("context_sources"), (list, dict, set, tuple)):
        ctx_candidates.append(_count_sources(coverage.get("context_sources")))
    if isinstance(progressive.get("context_sources"), (list, dict, set, tuple)):
        ctx_candidates.append(_count_sources(progressive.get("context_sources")))

    odds_values = [_to_int(item, -1) for item in odds_candidates if item not in (None, "")]
    ctx_values = [_to_int(item, -1) for item in ctx_candidates if item not in (None, "")]
    odds_count = max([value for value in odds_values if value >= 0], default=None)
    ctx_count = max([value for value in ctx_values if value >= 0], default=None)
    return odds_count, ctx_count


def _load_gap_map() -> dict[str, dict[str, Any]]:
    """Return match_key -> gap metadata for matches below 2+/2+ coverage."""
    paths = [
        Path(".data/exports/latest-progressive-coverage-plan.json"),
        Path(".data/day_inventory/progressive_coverage_state.json"),
        Path(".data/exports/latest-core-ready-by-window.json"),
        Path(".data/exports/latest-day-inventory-coverage-truth.json"),
    ]
    # Include latest day inventory snapshots without assuming the current date.
    for pattern in (
        ".data/day_inventory/current/latest/**/*.json",
        ".data/day_inventory/current/**/*.json",
        ".data/day_inventory/**/*.json",
    ):
        try:
            paths.extend(Path().glob(pattern))
        except Exception:
            pass

    gaps: dict[str, dict[str, Any]] = {}
    seen_paths: set[str] = set()
    for path in paths:
        marker = str(path)
        if marker in seen_paths or not path.exists() or not path.is_file():
            continue
        seen_paths.add(marker)
        payload = _read_json(path)
        rows = _iter_rows(payload)
        if isinstance(payload, dict) and isinstance(payload.get("matches"), dict):
            rows.extend([row for row in payload["matches"].values() if isinstance(row, dict)])
        for row in rows[:2000]:
            key = _match_key_from_row(row)
            if not key:
                continue
            odds_count, ctx_count = _row_counts(row)
            odds_needed = max(0, 2 - (odds_count if odds_count is not None else 0))
            ctx_needed = max(0, 2 - (ctx_count if ctx_count is not None else 0))
            explicit_gap = (
                _to_int(row.get("odds_needed"), 0) > 0
                or _to_int(row.get("context_needed"), 0) > 0
                or _to_int(row.get("core_odds_needed"), 0) > 0
                or _to_int(row.get("core_context_needed"), 0) > 0
            )
            if not explicit_gap and odds_count is None and ctx_count is None:
                continue
            if odds_needed <= 0 and ctx_needed <= 0 and not explicit_gap:
                continue
            current = gaps.setdefault(key, {"match_key": key, "odds_needed": 0, "context_needed": 0, "paths": []})
            current["odds_needed"] = max(_to_int(current.get("odds_needed"), 0), odds_needed, _to_int(row.get("odds_needed"), 0))
            current["context_needed"] = max(_to_int(current.get("context_needed"), 0), ctx_needed, _to_int(row.get("context_needed"), 0))
            current["paths"].append(str(path))
    return gaps


def _match_sort_key(match: Match, gap: dict[str, Any], now_utc: datetime, has_offers: bool) -> tuple[float, ...]:
    try:
        kickoff_delta = max((ensure_utc(match.commence_time) - now_utc).total_seconds(), 0.0)
    except Exception:
        kickoff_delta = 9999999.0
    soon_bucket = 5.0 if kickoff_delta <= 4 * 3600 else 4.0 if kickoff_delta <= 8 * 3600 else 3.0 if kickoff_delta <= 12 * 3600 else 2.0 if kickoff_delta <= 24 * 3600 else 1.0
    return (
        float(_to_int(gap.get("context_needed"), 0)),
        float(_to_int(gap.get("odds_needed"), 0)),
        soon_bucket,
        1.0 if has_offers else 0.0,
        -kickoff_delta,
    )


def _patched_provider_context_support_score(self: Any, provider_key: str, match: Match) -> float:
    original = _ORIGINAL_PROVIDER_SCORE
    try:
        score = float(original(self, provider_key, match) if callable(original) else 0.0)
    except Exception:
        score = 0.0
    provider = str(provider_key or "").strip().lower()
    try:
        availability = float(self._provider_availability_multiplier(provider))
    except Exception:
        availability = 1.0
    if availability <= 0.0:
        return 0.0
    if str(getattr(match, "sport_key", "") or "") != "soccer":
        return score

    # Keep original positive scores, but prevent important core sources from
    # being zeroed merely because the league is low-priority or supports_match()
    # is too strict.  The request budgets still cap actual API usage.
    floor = 0.0
    if provider == "bzzoiro":
        floor = _to_float(os.getenv("SOURCE_MATRIX_BZZOIRO_SCORE_FLOOR"), 0.56)
    elif provider == "sstats":
        floor = _to_float(os.getenv("SOURCE_MATRIX_SSTATS_SCORE_FLOOR"), 0.76)
    elif provider == "sportlogic":
        floor = _to_float(os.getenv("SOURCE_MATRIX_SPORTLOGIC_SCORE_FLOOR"), 0.34)
    elif provider in {"thesportsdb", "football_data"}:
        floor = _to_float(os.getenv("SOURCE_MATRIX_MAPPING_SCORE_FLOOR"), 0.28)
    if floor > 0.0:
        return max(score, floor * availability)
    return score


def _patched_select_context_enrichment_matches(
    self: Any,
    matches: list[Match],
    offers_by_match: dict[str, list[Offer]],
    now_utc: datetime,
    market_signals_by_match: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[Match], dict[str, Any]]:
    original = _ORIGINAL_SELECT
    if not callable(original):
        return [], {"source_matrix_amplifier_error": "original_missing"}
    selected, summary = original(self, matches, offers_by_match, now_utc, market_signals_by_match)
    if not _truthy(os.getenv("SOURCE_MATRIX_AMPLIFIER_ENABLED"), True):
        return selected, summary

    gap_map = _load_gap_map()
    if not gap_map:
        if isinstance(summary, dict):
            summary["source_matrix_gap_keys"] = 0
            summary["source_matrix_gap_appended"] = 0
        return selected, summary

    selected_keys = {getattr(match, "match_key", "") for match in selected}
    match_index = {getattr(match, "match_key", ""): match for match in matches or [] if getattr(match, "match_key", "")}
    append_candidates: list[tuple[tuple[float, ...], Match]] = []
    for key, gap in gap_map.items():
        if key in selected_keys:
            continue
        match = match_index.get(key)
        if match is None:
            continue
        append_candidates.append((_match_sort_key(match, gap, now_utc or datetime.now(UTC), bool(offers_by_match.get(key))), match))
    append_candidates.sort(key=lambda item: item[0], reverse=True)

    hard_limit = _to_int(os.getenv("SOURCE_MATRIX_CONTEXT_TARGET_LIMIT"), 300)
    if hard_limit <= 0:
        hard_limit = len(matches or [])
    append_limit = _to_int(os.getenv("SOURCE_MATRIX_GAP_APPEND_LIMIT"), 180)
    room = max(0, min(hard_limit - len(selected), append_limit))
    appended = [match for _, match in append_candidates[:room]]
    if appended:
        selected = list(selected) + appended

    if isinstance(summary, dict):
        summary.update({
            "source_matrix_gap_keys": len(gap_map),
            "source_matrix_gap_candidates": len(append_candidates),
            "source_matrix_gap_appended": len(appended),
            "source_matrix_context_target_limit": hard_limit,
            "source_matrix_append_limit": append_limit,
            "source_matrix_appended_sample": [getattr(match, "match_key", "") for match in appended[:20]],
        })
    _write_json(Path(".data/exports/latest-source-matrix-amplifier-selection.json"), {
        "installed": True,
        "gap_keys": len(gap_map),
        "selected_before_gap_append": len(selected) - len(appended),
        "appended": len(appended),
        "selected_after_gap_append": len(selected),
        "sample": [getattr(match, "match_key", "") for match in appended[:50]],
    })
    return selected, summary


def _apply_env_defaults() -> dict[str, str]:
    defaults = {
        "SOURCE_MATRIX_AMPLIFIER_ENABLED": "true",
        "SOURCE_MATRIX_CONTEXT_TARGET_LIMIT": "300",
        "SOURCE_MATRIX_GAP_APPEND_LIMIT": "180",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "BZZOIRO_DIRECT_CONTEXT_FETCH_ENABLED": "true",
        "BZZOIRO_CONTEXT_GAP_PASS_ENABLED": "true",
        "BZZOIRO_CONTEXT_GAP_INCLUDE_PROGRESSIVE_STATE": "true",
        "BZZOIRO_CONTEXT_GAP_MATCH_LIMIT": "160",
        "BZZOIRO_CONTEXT_GAP_MAX_REQUESTS": "180",
        "BZZOIRO_FORCE_GAP_PLAN_TARGETS": "true",
        "BZZOIRO_FORCE_GAP_TARGET_LIMIT": "180",
        "BZZOIRO_ODDS_COMPARISON_ENABLED": "true",
        "BZZOIRO_EXACT_OFFER_BRIDGE_ENABLED": "true",
        "SSTATS_CONTEXT_MATCH_LIMIT": "300",
        "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "80",
        "SPORTLOGIC_MATCH_LIMIT": "160",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": "60",
    }
    changed: dict[str, str] = {}
    for key, value in defaults.items():
        if not str(os.getenv(key, "")).strip():
            os.environ[key] = value
            changed[key] = value
    return changed


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_SELECT, _ORIGINAL_PROVIDER_SCORE
    env_changed = _apply_env_defaults()
    if _INSTALLED:
        return {"installed": True, "already_installed": True, "env_defaults_applied": env_changed}
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {"installed": False, "error": f"import_runner:{type(exc).__name__}: {exc}", "env_defaults_applied": env_changed}

    _ORIGINAL_SELECT = getattr(PredictionRunner, "_select_context_enrichment_matches", None)
    _ORIGINAL_PROVIDER_SCORE = getattr(PredictionRunner, "_provider_context_support_score", None)
    if callable(_ORIGINAL_SELECT):
        PredictionRunner._select_context_enrichment_matches = _patched_select_context_enrichment_matches  # type: ignore[method-assign]
    if callable(_ORIGINAL_PROVIDER_SCORE):
        PredictionRunner._provider_context_support_score = _patched_provider_context_support_score  # type: ignore[method-assign]
    _INSTALLED = True
    report = {
        "installed": True,
        "patched_select_context_enrichment_matches": callable(_ORIGINAL_SELECT),
        "patched_provider_context_support_score": callable(_ORIGINAL_PROVIDER_SCORE),
        "env_defaults_applied": env_changed,
    }
    _write_json(Path(".data/exports/latest-source-matrix-amplifier-install.json"), report)
    return report
