"""Stable Focused Alpha runtime contract and run lifecycle ownership."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".data" / "exports" / "latest-focused-alpha-runtime-policy.json"
RUN_LIFECYCLE = ROOT / ".data" / "exports" / "latest-main-run-lifecycle.json"
DEBUG_PATH = ROOT / ".logs" / "debug-last-run.json"

POLICY: dict[str, str] = {
    "FOCUSED_ALPHA_ENABLED": "true",
    "FOCUSED_ALPHA_MAX_MATCHES": "100",
    "FOCUSED_ALPHA_PHASE_TARGETS": "40,70,100",
    "FOCUSED_ALPHA_MIN_MATCH_SCORE": "44",
    "FOCUSED_ALPHA_MAX_PER_LEAGUE": "10",
    "FOCUSED_ALPHA_EXPLORATION_SLOTS": "6",
    "FOCUSED_ALPHA_BOOTSTRAP_MATCHES": "24",
    "FOCUSED_ALPHA_BOOTSTRAP_MAX_HOURS": "36",
    "FOCUSED_ALPHA_DAILY_MAX_DECISIONS": "3",
    "FOCUSED_ALPHA_LIVE_ENABLED": "false",
    "FOCUSED_ALPHA_MIN_CONSERVATIVE_EV_PCT": "2.0",
    "FOCUSED_ALPHA_MIN_EDGE_PP": "2.0",
    "FOCUSED_ALPHA_MIN_QUALITY": "68",
    "FOCUSED_ALPHA_MIN_CONFIDENCE": "68",
    "RUNBOT_DISCOVERY_FIRST_FORCE_FULL_REFRESH": "false",
    "RUNBOT_DISCOVERY_FIRST_FULL_REFRESH_INTERVAL_MINUTES": "360",
    "RUNBOT_DISCOVERY_FIRST_REUSE_MIN_INVENTORY_ROWS": "260",
    "RUNBOT_DISCOVERY_FIRST_MAX_SECONDS": "210",
    "RUNBOT_INCREMENTAL_DEEP_ENRICHMENT_ENABLED": "true",
    "RUNBOT_INCREMENTAL_BZZOIRO_GAP_ENRICHMENT_ENABLED": "false",
    "RUNBOT_FULL_BZZOIRO_GAP_ENRICHMENT_ENABLED": "false",
    "HARIZON_DATA_COLLECTION_WINDOW_HOURS": "36",
    "HARIZON_DISABLE_MAIN_PUBLICATION_FOR_DATA_WINDOW": "false",
    "PUBLISH_WINDOW_HOURS": "2",
    "PREDICTION_PUBLICATION_ENABLED": "true",
    "NIGHTLY_REVIEW_REPORT_ONLY_ENABLED": "false",
    "CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS": "2",
    "MIN_KICKOFF_LEAD_MINUTES": "20",
    "MAX_PICKS_PER_RUN": "2",
    "CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED": "3",
    "CONTROLLED_FALLBACK_DAILY_MAX_B_TIER": "3",
    "REPUBLISH_SEEN_CANDIDATES_WHEN_EMPTY": "false",
    "BANKROLL_FORCE_MIN_STAKE_WHEN_EMPTY_ENABLED": "false",
    "PUBLISH_ALLOW_B_TIER": "true",
    "PUBLISH_B_TIER_WATCH_ONLY": "false",
    "PUBLISH_COVERAGE_TIER_MODE": "hybrid",
    "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B": "true",
    "CONTROLLED_FALLBACK_TIER_B_PUBLISH_ENABLED": "true",
    "CONTROLLED_FALLBACK_TIER_B_WATCH_ONLY": "false",
    "MIN_BOOKS_PUBLISH": "2",
    "PUBLISH_MIN_BOOKS": "2",
    "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2",
    "PUBLISH_TIER_A_MIN_BOOKS": "2",
    "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "1",
    "PUBLISH_TIER_B_MIN_BOOKS": "2",
    "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "MIN_SOURCES_PUBLISH": "1",
    "PUBLISH_MIN_ODDS_SOURCES": "1",
    "PUBLISH_MIN_CONTEXT_SOURCES": "1",
    "MIN_CONTEXT_SOURCES_PUBLISH": "1",
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "1",
    "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES": "true",
    "CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_CONFIRMATION_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_REQUIRE_RAW_QUALITY": "true",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_SINGLE_LINE_ENABLED": "true",
    "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_MIN_CONFIDENCE": "70.0",
    "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_MIN_QUALITY": "0.0",
    "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_MIN_EDGE_PP": "2.3",
    "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_MIN_EV_PCT": "4.0",
    "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_REQUIRE_XG_HARD_CONFIRMATION": "false",
    "CONTROLLED_FALLBACK_USE_QUALITY_PROXY": "false",
    "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
    "CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES": "false",
    "CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM": "true",
    "CONTROLLED_FALLBACK_REQUIRE_LINE_RECHECK": "true",
    "CONTROLLED_FALLBACK_REQUIRE_FINAL_CRON_RECHECK": "true",
    "LINE_MOVEMENT_GUARD_ENABLED": "true",
    "LINE_MOVEMENT_MIN_SNAPSHOTS": "2",
    "ODDS_SOURCE_INDEPENDENCE_ENABLED": "true",
    "BOOKMAKER_QUORUM_ENABLED": "true",
    "DAY_INVENTORY_ENABLE_SPORTLOGIC": "false",
    "ENABLE_SPORTLOGIC": "false",
    "SPORTLOGIC_ENABLED": "false",
    "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "false",
    "SPORTLOGIC_PER_RUN_MAX": "0",
    "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "0",
    "SPORTLOGIC_REQUESTS_MAX_PER_RUN": "0",
    "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "0",
    "SPORTLOGIC_MATCH_LIMIT": "0",
    "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "0",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
    "SPORTLOGIC_DISABLED_ZERO_ROWS_GUARD": "true",
}


def _enabled() -> bool:
    return str(os.getenv("FOCUSED_ALPHA_POLICY_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on", "force"}


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _same_run(payload: dict[str, Any]) -> bool:
    run_id = str(os.getenv("GITHUB_RUN_ID") or "").strip()
    attempt = str(os.getenv("GITHUB_RUN_ATTEMPT") or "").strip()
    if not run_id or str(payload.get("github_run_id") or "").strip() != run_id:
        return False
    saved_attempt = str(payload.get("github_run_attempt") or "").strip()
    return not attempt or not saved_attempt or saved_attempt == attempt


def _start_lifecycle_once() -> dict[str, Any]:
    if not (os.getenv("GITHUB_RUN_ID") or os.getenv("HARIZON_FORCE_RUN_LIFECYCLE")):
        return {"status": "not_github_run"}
    existing = _read(RUN_LIFECYCLE)
    if existing and _same_run(existing):
        started = str(existing.get("started_at_utc") or "").strip()
        if started:
            os.environ.setdefault("HARIZON_MAIN_RUN_LIFECYCLE_STARTED_AT", started)
        return {"status": "already_started_same_github_run", "started_at_utc": started or None, "github_run_id": existing.get("github_run_id"), "github_run_attempt": existing.get("github_run_attempt"), "lifecycle_status": existing.get("status"), "stale_debug_removed": bool(existing.get("stale_debug_removed"))}
    marker = os.getenv("HARIZON_MAIN_RUN_LIFECYCLE_STARTED_AT")
    if marker:
        return {"status": "already_started_process", "started_at_utc": marker}
    started = datetime.now(UTC).isoformat()
    os.environ["HARIZON_MAIN_RUN_LIFECYCLE_STARTED_AT"] = started
    removed = False
    try:
        if DEBUG_PATH.exists():
            DEBUG_PATH.unlink()
            removed = True
    except Exception:
        pass
    payload = {"status": "running", "started_at_utc": started, "github_run_id": os.getenv("GITHUB_RUN_ID"), "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"), "stale_debug_removed": removed, "fresh_debug_required": True, "owner": "focused_alpha_runtime_contract"}
    _write(RUN_LIFECYCLE, payload)
    return payload


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _relax_discovery_reuse(target: Any, result: Any) -> dict[str, Any]:
    result = dict(result) if isinstance(result, dict) else {}
    if result.get("reusable"):
        return result
    mode = str(result.get("mode") or "").lower()
    status = str(result.get("status") or "").lower()
    age = _as_float(result.get("age_minutes"), -1.0)
    refresh = max(1.0, _as_float(result.get("refresh_interval_minutes"), 360.0))
    if not mode or "incremental" in mode or not status.startswith("ok") or age < 0 or age > refresh:
        return result
    expected_day = str(target._target_date() or "")[:10]
    checkpoint_day = str(result.get("target_date") or "")[:10]
    if expected_day and checkpoint_day and expected_day != checkpoint_day:
        return result
    nominal = max(1, int(target.env_int("DAY_INVENTORY_TARGET_SIZE", 300)))
    floor = max(1, min(nominal, int(target.env_int("RUNBOT_DISCOVERY_FIRST_REUSE_MIN_INVENTORY_ROWS", min(260, nominal)))))
    current = max(0, int(target.inventory_matches()))
    result.update(current_inventory_matches=current, reuse_min_inventory_rows=floor, nominal_inventory_target=nominal)
    if current >= floor:
        result.update(reusable=True, reuse_reason="fresh_full_checkpoint_inventory_floor", inventory_topup_required=current < nominal)
    return result


def _install_discovery_reuse_floor() -> dict[str, Any]:
    try:
        from scripts import runbot_discovery_first_prepare as target
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    current = target.previous_full_prepare
    if getattr(current, "_focused_alpha_reuse_floor", False):
        return {"status": "already_installed"}

    def wrapped(now=None):
        return _relax_discovery_reuse(target, current(now))

    wrapped._focused_alpha_reuse_floor = True  # type: ignore[attr-defined]
    target.previous_full_prepare = wrapped
    return {"status": "installed", "reuse_min_inventory_rows": int(POLICY["RUNBOT_DISCOVERY_FIRST_REUSE_MIN_INVENTORY_ROWS"])}


def _reassert_preflight_policy() -> bool:
    try:
        from app.services import runtime_preflight
        runtime_preflight.AUTONOMOUS_ACCUMULATION_POLICY.update(POLICY)
        runtime_preflight.DISCOVERY_FIRST_DEFAULTS.update({key: value for key, value in POLICY.items() if key.startswith("RUNBOT_")})
        return True
    except Exception:
        return False


def apply(*, force: bool = True) -> dict[str, Any]:
    if not _enabled():
        payload = {"status": "disabled", "publication_contract_relaxed": False}
        _write(OUT, payload)
        return payload
    lifecycle = _start_lifecycle_once()
    before = {key: os.getenv(key) for key in POLICY}
    for key, value in POLICY.items():
        if force or os.getenv(key) in (None, ""):
            os.environ[key] = value
    reuse_patch = _install_discovery_reuse_floor()
    preflight_reasserted = _reassert_preflight_policy()
    after = {key: os.getenv(key) for key in POLICY}
    payload = {"status": "applied", "created_at_utc": datetime.now(UTC).isoformat(), "mode": "focused_alpha_rules_ab_publish", "force_operator_contract": force, "changed": {key: {"before": before[key], "after": after[key]} for key in POLICY if before[key] != after[key]}, "effective": after, "publication_minimum_count": 0, "daily_max_published": int(POLICY["CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED"]), "provider_focus_max_matches": 100, "main_publication_disabled_for_wide_data_window": False, "live_learning_auto_tuning": False, "preflight_policy_reasserted": preflight_reasserted, "discovery_reuse_floor_patch": reuse_patch, "run_lifecycle": lifecycle, "publication_contract_relaxed": False}
    _write(OUT, payload)
    return payload


def complete_lifecycle(status: str, summary: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    existing = _read(RUN_LIFECYCLE)
    if existing and not _same_run(existing):
        return {"status": "skipped_different_github_run"}
    payload = dict(existing)
    payload.update({"status": str(status or "unknown"), "finished_at_utc": datetime.now(UTC).isoformat(), "github_run_id": os.getenv("GITHUB_RUN_ID") or payload.get("github_run_id"), "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT") or payload.get("github_run_attempt"), "owner": "focused_alpha_runtime_contract"})
    if isinstance(summary, dict):
        payload["summary"] = {"matches_seen": int(summary.get("matches_seen") or 0), "matches_with_offers": int(summary.get("matches_with_offers") or 0), "contexts_built": int(summary.get("contexts_built") or 0), "candidates_raw": int(summary.get("candidates_raw") or 0), "published_to_telegram": int(summary.get("published_to_telegram") or 0)}
    if error:
        payload["error"] = str(error)
    _write(RUN_LIFECYCLE, payload)
    return payload


__all__ = ["POLICY", "apply", "complete_lifecycle"]
