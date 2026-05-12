from __future__ import annotations

"""Provider alias finalizer for progressive coverage.

Runtime provider names may be more specific than the core contract, for example
`bzzoiro_predictions_v2`. The core contract is expressed as `bzzoiro`, so those
successful rows must be normalized before coverage counters are calculated.

Without this, a match can have:
  last_success_utc_by_provider.bzzoiro_predictions_v2 = ...
but still miss:
  context_sources += bzzoiro
which keeps `core_context_2+` artificially low.
"""

import atexit
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-progressive-provider-alias-finalizer.json"

BZZOIRO_ALIASES = {
    "bzzoiro",
    "bzzoiro_v1",
    "bzzoiro_v2",
    "bzzoiro_predictions",
    "bzzoiro_predictions_v2",
    "bzzoiro_context_gap_pass",
    "bzzoiro_v2_gap_pass",
    "bsd",
    "bsd_v2",
}
SSTATS_ALIASES = {"sstats", "sstats_deep", "sstats_crosswalk"}
ODDS_API_IO_ALIASES = {"odds_api_io", "oddsapiio", "odds-api.io"}


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _norm_provider(name: Any) -> str:
    value = str(name or "").strip().lower()
    if value in BZZOIRO_ALIASES or value.startswith("bzzoiro") or value.startswith("bsd"):
        return "bzzoiro"
    if value in SSTATS_ALIASES or value.startswith("sstats"):
        return "sstats"
    if value in ODDS_API_IO_ALIASES:
        return "odds_api_io"
    return value


def _token_set(value: Any) -> set[str]:
    tokens: set[str] = set()
    if value in (None, ""):
        return tokens
    if isinstance(value, str):
        raw = value.replace(";", ",").replace("|", ",").split(",")
        tokens.update(_norm_provider(x) for x in raw if str(x).strip())
    elif isinstance(value, dict):
        for k, v in value.items():
            if v not in (None, "", False, [], {}):
                tokens.add(_norm_provider(k))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            tokens |= _token_set(item)
    return {t for t in tokens if t}


def _recompute_gap(row: dict[str, Any], p: Any) -> None:
    odds_sources = _token_set(row.get("odds_sources"))
    context_sources = _token_set(row.get("context_sources"))
    core_odds = odds_sources & {"odds_api_io", "bzzoiro", "sstats"}
    core_context = context_sources & {"bzzoiro", "sstats"}
    min_odds = _to_int(__import__("os").getenv("PROGRESSIVE_COVERAGE_MIN_ODDS_SOURCES") or 2, 2)
    min_context = _to_int(__import__("os").getenv("PROGRESSIVE_COVERAGE_MIN_CONTEXT_SOURCES") or 2, 2)
    row["odds_sources"] = sorted(odds_sources)
    row["context_sources"] = sorted(context_sources)
    gap = row.get("coverage_gap") if isinstance(row.get("coverage_gap"), dict) else {}
    gap.update({
        "core_odds_sources": len(core_odds),
        "core_context_sources": len(core_context),
        "all_odds_sources": len(odds_sources),
        "all_context_sources": len(context_sources),
        "core_odds_needed": max(0, min_odds - len(core_odds)),
        "core_context_needed": max(0, min_context - len(core_context)),
        "odds_sources": len(core_odds),
        "context_sources": len(core_context),
        "odds_needed": max(0, min_odds - len(core_odds)),
        "context_needed": max(0, min_context - len(core_context)),
        "core_contract": "odds_api_io+bzzoiro+sstats lines; sstats+bzzoiro context",
    })
    row["coverage_gap"] = gap


def _normalize_state(p: Any) -> dict[str, int]:
    state = p._load_state()
    matches = state.get("matches") if isinstance(state.get("matches"), dict) else {}
    changed = 0
    bzzoiro_success_promoted = 0
    sstats_success_promoted = 0
    for row in matches.values():
        if not isinstance(row, dict):
            continue
        before = json.dumps({"odds": row.get("odds_sources"), "ctx": row.get("context_sources"), "gap": row.get("coverage_gap")}, sort_keys=True, ensure_ascii=False)
        odds = _token_set(row.get("odds_sources"))
        ctx = _token_set(row.get("context_sources"))
        success = row.get("last_success_utc_by_provider") if isinstance(row.get("last_success_utc_by_provider"), dict) else {}
        success_norm = {_norm_provider(k) for k, v in success.items() if v not in (None, "", False, [], {})}
        # A successful Bzzoiro provider, regardless of v1/v2/prediction-specific
        # name, satisfies both core context and line/model source by user contract.
        if "bzzoiro" in success_norm:
            if "bzzoiro" not in ctx:
                bzzoiro_success_promoted += 1
            ctx.add("bzzoiro")
            odds.add("bzzoiro")
        if "sstats" in success_norm:
            if "sstats" not in ctx:
                sstats_success_promoted += 1
            ctx.add("sstats")
            odds.add("sstats")
        if "odds_api_io" in success_norm:
            odds.add("odds_api_io")
        row["odds_sources"] = sorted(odds)
        row["context_sources"] = sorted(ctx)
        _recompute_gap(row, p)
        after = json.dumps({"odds": row.get("odds_sources"), "ctx": row.get("context_sources"), "gap": row.get("coverage_gap")}, sort_keys=True, ensure_ascii=False)
        if before != after:
            changed += 1
    if changed:
        p._save_state(state)
    return {"matches_changed": changed, "bzzoiro_success_promoted": bzzoiro_success_promoted, "sstats_success_promoted": sstats_success_promoted}


def install() -> dict[str, Any]:
    payload: dict[str, Any] = {"created_at_utc": datetime.now(UTC).isoformat(), "status": "starting"}
    try:
        from app.services import progressive_coverage_runtime_patch as p
    except Exception as exc:
        payload.update({"status": "error", "error": f"import:{type(exc).__name__}: {exc}"})
        _write_json(REPORT_PATH, payload)
        return payload

    # Patch token parser first, because all later plan/state writers use it.
    old_provider_tokens = getattr(p, "_provider_tokens", None)
    if not getattr(old_provider_tokens, "_harizon_alias_normalized", False):
        def provider_tokens_alias_normalized(value: Any) -> set[str]:
            return _token_set(value)
        provider_tokens_alias_normalized._harizon_alias_normalized = True  # type: ignore[attr-defined]
        p._provider_tokens = provider_tokens_alias_normalized

    old_record = getattr(p, "_record_provider_success", None)
    if callable(old_record) and not getattr(old_record, "_harizon_alias_normalized", False):
        def record_provider_success_alias_normalized(data: Any, provider: str, method_name: str, stats: Any | None = None) -> None:
            provider_norm = _norm_provider(provider)
            old_record(data, provider_norm, method_name, stats)
            _normalize_state(p)
            try:
                p._write_plan_report()
            except Exception:
                pass
        record_provider_success_alias_normalized._harizon_alias_normalized = True  # type: ignore[attr-defined]
        p._record_provider_success = record_provider_success_alias_normalized

    normalized = _normalize_state(p)
    try:
        p._write_plan_report()
        p._sync_inventory_rows_from_state()
    except Exception:
        pass
    atexit.register(lambda: (_normalize_state(p), p._write_plan_report()))
    payload.update({"status": "installed", **normalized})
    _write_json(REPORT_PATH, payload)
    return payload
