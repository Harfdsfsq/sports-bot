from __future__ import annotations

"""Source-matrix bridge for the *actual* Bzzoiro v2 provider used by runner.

The runner instantiates ``app.providers.bzzoiro_v2.BzzoiroContextProvider``.
Older gap-finalizer wrappers patched ``app.providers.bzzoiro.BzzoiroContextProvider``;
those install reports can look green while the live v2 provider never receives
progressive gap targets.  This patch works at two live points:

* PredictionRunner._select_provider_context_matches: append progressive 2+/2+
  gap matches to the Bzzoiro provider target list.
* app.providers.bzzoiro_v2.BzzoiroContextProvider.fetch_context: enhance returned
  contexts with current-event odds hints and write the same gap report path that
  the Telegram v8 report already reads.

No quality/publication threshold is relaxed.  It only makes Bzzoiro v2 see the
matches that already have source-matrix gaps.
"""

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas import Match, MatchContext

ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
PLAN_PATH = EXPORT_DIR / "latest-progressive-coverage-plan.json"
INSTALL_REPORT = EXPORT_DIR / "latest-bzzoiro-v2-source-matrix-install.json"
SELECT_REPORT = EXPORT_DIR / "latest-bzzoiro-v2-source-matrix-selection.json"
RUNTIME_REPORT = EXPORT_DIR / "latest-bzzoiro-v2-source-matrix-runtime.json"
# Keep the legacy report name because scripts/send_harizon_telegram_run_report_v8.py reads it.
LEGACY_GAP_REPORT = EXPORT_DIR / "latest-bzzoiro-context-gap-finalizer.json"

_INSTALLED = False
_ORIGINAL_SELECT = None
_ORIGINAL_V2_FETCH_CONTEXT = None


def _truthy(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force", "y"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _future(row: dict[str, Any]) -> bool:
    raw = row.get("kickoff_utc") or row.get("commence_time") or row.get("event_date")
    if raw in (None, ""):
        return True
    try:
        text = str(raw).strip().replace(" ", "T")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if "T" in text and "+" not in text and text.count("-") >= 2:
            text += "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (dt.astimezone(UTC) - datetime.now(UTC)).total_seconds() >= -240
    except Exception:
        return True


def _gap_rows() -> list[dict[str, Any]]:
    plan = _read_json(PLAN_PATH)
    rows = plan.get("core_gap_sample") or plan.get("gap_sample") or []
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not _future(row):
            continue
        context_needed = _to_int(row.get("core_context_needed") or row.get("context_needed"), 0)
        odds_needed = _to_int(row.get("core_odds_needed") or row.get("odds_needed"), 0)
        context_sources = {str(x).strip().lower() for x in (row.get("core_context_sources") or []) if str(x).strip()}
        odds_sources = {str(x).strip().lower() for x in (row.get("core_odds_sources") or []) if str(x).strip()}
        # Bzzoiro can help either as context source or as current-event odds hints.
        needs_bzz_context = context_needed > 0 and "bzzoiro" not in context_sources and "bzzoiro_v2" not in context_sources
        needs_bzz_odds = odds_needed > 0 and "bzzoiro" not in odds_sources and "bzzoiro_v2" not in odds_sources
        if needs_bzz_context or needs_bzz_odds:
            out.append(row)
    return out


def _gap_key_set() -> set[str]:
    keys: set[str] = set()
    for row in _gap_rows():
        key = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
        if key:
            keys.add(key)
    return keys


def _append_gap_targets(base: list[Match], candidates: list[Match], *, limit: int) -> tuple[list[Match], dict[str, Any]]:
    base = list(base or [])
    candidates = list(candidates or [])
    gap_keys = _gap_key_set()
    selected = list(base)
    seen = {m.match_key for m in selected if getattr(m, "match_key", "")}
    appended: list[Match] = []
    if gap_keys:
        for match in candidates:
            key = getattr(match, "match_key", "")
            if not key or key in seen or key not in gap_keys:
                continue
            if getattr(match, "sport_key", "") != "soccer":
                continue
            selected.append(match)
            appended.append(match)
            seen.add(key)
            if limit > 0 and len(selected) >= limit:
                break
    return selected, {
        "gap_rows": len(gap_keys),
        "selected_before": len(base),
        "candidate_pool": len(candidates),
        "appended": len(appended),
        "selected_after": len(selected),
        "limit": limit,
        "sample": [m.match_key for m in appended[:25]],
    }


def _patched_select_provider_context_matches(self: Any, matches: list[Match], provider_name: str, *, fallback_matches: list[Match] | None = None, offers_by_match: dict[str, list[Any]] | None = None) -> list[Match]:
    original = _ORIGINAL_SELECT
    if not callable(original):
        return list(matches or [])
    selected = original(self, matches, provider_name, fallback_matches=fallback_matches, offers_by_match=offers_by_match)
    if not _truthy(os.getenv("BZZOIRO_V2_SOURCE_MATRIX_TARGETS_ENABLED"), True):
        return selected
    provider_key = str(provider_name or "").strip().lower()
    if provider_key != "bzzoiro":
        return selected
    limit = max(1, _to_int(os.getenv("BZZOIRO_V2_SOURCE_MATRIX_TARGET_LIMIT") or os.getenv("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT") or 180, 180))
    pool: list[Match] = []
    pool.extend(list(matches or []))
    pool.extend(list(fallback_matches or []))
    expanded, report = _append_gap_targets(list(selected or []), pool, limit=limit)
    _write_json(SELECT_REPORT, {"created_at_utc": datetime.now(UTC).isoformat(), **report})
    return expanded


def _existing_hints(context: MatchContext) -> list[dict[str, Any]]:
    details = getattr(context, "details", None)
    if isinstance(details, dict) and isinstance(details.get("provider_odds_hints"), list):
        return [x for x in details.get("provider_odds_hints") if isinstance(x, dict)]
    return []


def _enhance_bzzoiro_context(context: MatchContext, match: Match | None = None) -> int:
    before = len(_existing_hints(context))
    # Prefer the newer miner; it understands Bzzoiro v2 payloads and keeps SStats historical odds blocked.
    try:
        from app.services.provider_payload_mining_runtime_patch_v2 import _enhance_context  # type: ignore
        _enhance_context(context, "bzzoiro", match)
    except Exception:
        try:
            from app.services.signal_stack_runtime_patch import _enhance_context as _signal_enhance  # type: ignore
            _signal_enhance(context)
        except Exception:
            pass
    hints = _existing_hints(context)
    details = dict(getattr(context, "details", {}) or {})
    details["bzzoiro_v2_source_matrix_patch"] = True
    details["provider_odds_hints_count"] = len(hints)
    if "source_tokens" not in details:
        details["source_tokens"] = ["bzzoiro"]
    elif isinstance(details.get("source_tokens"), list) and "bzzoiro" not in {str(x).lower() for x in details.get("source_tokens") or []}:
        details["source_tokens"] = list(details.get("source_tokens") or []) + ["bzzoiro"]
    try:
        # Canonicalize the context source token for source-matrix counters.
        context.source = "bzzoiro"  # type: ignore[misc]
    except Exception:
        pass
    context.details = details
    return max(0, len(hints) - before) if len(hints) >= before else len(hints)


async def _patched_v2_fetch_context(self: Any, matches: list[Match]):  # type: ignore[no-untyped-def]
    original = _ORIGINAL_V2_FETCH_CONTEXT
    if not callable(original):
        return {}, {"enabled": False, "runtime_error": "original_v2_fetch_context_missing"}, {}
    input_matches = list(matches or [])
    input_by_key = {m.match_key: m for m in input_matches if getattr(m, "match_key", "")}
    gap_keys = _gap_key_set()
    target_keys = {key for key in input_by_key if key in gap_keys}
    contexts, stats, preview = await original(self, input_matches)
    contexts = dict(contexts or {})
    stats = dict(stats or {})
    preview = dict(preview or {})

    hint_count = 0
    hinted_contexts = 0
    forced_matched = 0
    for key, context in list(contexts.items()):
        if not isinstance(context, MatchContext):
            continue
        added_hints = _enhance_bzzoiro_context(context, input_by_key.get(str(key)))
        hints_now = len(_existing_hints(context))
        hint_count += hints_now
        if hints_now > 0:
            hinted_contexts += 1
        if str(key) in target_keys:
            forced_matched += 1

    stats["bzzoiro_v2_source_matrix_patch"] = True
    stats["source_matrix_gap_targets"] = len(target_keys)
    stats["source_matrix_gap_matched"] = forced_matched
    stats["provider_odds_hints"] = max(_to_int(stats.get("provider_odds_hints"), 0), hint_count)
    stats["provider_odds_hinted_contexts"] = hinted_contexts
    preview["bzzoiro_v2_source_matrix_patch"] = {
        "target_matches": len(target_keys),
        "matched": forced_matched,
        "provider_odds_hints": hint_count,
        "sample_targets": sorted(target_keys)[:25],
    }

    legacy_stats = {
        "enabled": bool(getattr(self, "api_key", None)),
        "requests": 0,
        "target_matches": len(target_keys),
        "matched": forced_matched,
        "contexts_added": forced_matched,
        "contexts_added_total": forced_matched,
        "odds_resources": _to_int(stats.get("event_odds_fetched"), 0),
        "stats_resources": _to_int(stats.get("event_stats_fetched"), 0),
        "metadata_resources": _to_int(stats.get("event_metadata_fetched"), 0),
        "lineups_resources": _to_int(stats.get("event_lineups_fetched"), 0),
        "odds_hints": hint_count,
        "hinted_contexts": hinted_contexts,
        "v2_events_fetched": _to_int(stats.get("events_fetched"), 0),
        "errors": _to_int(stats.get("response_errors"), 0),
        "provider": "bzzoiro_v2",
        "note": "patched actual app.providers.bzzoiro_v2 provider; legacy report path kept for Telegram v8",
    }
    runtime_payload = {
        "status": "ran",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "stats": legacy_stats,
        "preview": preview.get("bzzoiro_v2_source_matrix_patch", {}),
    }
    _write_json(RUNTIME_REPORT, runtime_payload)
    _write_json(LEGACY_GAP_REPORT, runtime_payload)
    return contexts, stats, preview


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_SELECT, _ORIGINAL_V2_FETCH_CONTEXT
    if _INSTALLED:
        return {"installed": True, "already_installed": True}
    report: dict[str, Any] = {"created_at_utc": datetime.now(UTC).isoformat(), "installed": False}
    try:
        from app.services.runner import PredictionRunner
        from app.providers.bzzoiro_v2 import BzzoiroContextProvider as BzzoiroV2ContextProvider
    except Exception as exc:
        report.update({"error": f"import:{type(exc).__name__}: {exc}"})
        _write_json(INSTALL_REPORT, report)
        return report

    _ORIGINAL_SELECT = getattr(PredictionRunner, "_select_provider_context_matches", None)
    if callable(_ORIGINAL_SELECT) and not getattr(_ORIGINAL_SELECT, "_harizon_bzzoiro_v2_source_matrix_select", False):
        _patched_select_provider_context_matches._harizon_bzzoiro_v2_source_matrix_select = True  # type: ignore[attr-defined]
        PredictionRunner._select_provider_context_matches = _patched_select_provider_context_matches  # type: ignore[method-assign]

    current = getattr(BzzoiroV2ContextProvider, "fetch_context", None)
    _ORIGINAL_V2_FETCH_CONTEXT = current
    if callable(current) and not getattr(current, "_harizon_bzzoiro_v2_source_matrix_fetch", False):
        _patched_v2_fetch_context._harizon_bzzoiro_v2_source_matrix_fetch = True  # type: ignore[attr-defined]
        BzzoiroV2ContextProvider.fetch_context = _patched_v2_fetch_context  # type: ignore[assignment]

    os.environ.setdefault("BZZOIRO_V2_SOURCE_MATRIX_TARGETS_ENABLED", "true")
    os.environ.setdefault("BZZOIRO_V2_SOURCE_MATRIX_TARGET_LIMIT", os.getenv("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT", "180"))
    _INSTALLED = True
    report.update({
        "installed": True,
        "patched_runner_provider_selection": callable(_ORIGINAL_SELECT),
        "patched_bzzoiro_v2_fetch_context": callable(_ORIGINAL_V2_FETCH_CONTEXT),
        "target_limit": _to_int(os.getenv("BZZOIRO_V2_SOURCE_MATRIX_TARGET_LIMIT"), 180),
        "legacy_gap_report": str(LEGACY_GAP_REPORT),
        "why": "runner uses app.providers.bzzoiro_v2, not app.providers.bzzoiro",
    })
    _write_json(INSTALL_REPORT, report)
    return report
