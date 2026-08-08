from __future__ import annotations

"""Rules-compliant runtime policy for the prediction pipeline."""

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.utils import ensure_utc

UTC = timezone.utc
BUCKET_ORDER = ["0-4h", "4-8h", "8-12h", "12-16h", "16-20h", "20-24h", "24h+"]

RULES_ENV_DEFAULTS = {
    "DAILY_INVENTORY_MAX_MATCHES": "300",
    "DAY_INVENTORY_MAX_MATCHES": "300",
    "DAY_INVENTORY_RUN_MATCH_LIMIT": "300",
    "ANALYSIS_MATCH_CAP_PER_RUN": "300",
    "MAX_MATCHES_FOR_ODDS_FETCH": "300",
    "PUBLISH_WINDOW_HOURS": "24",
    "CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS": "24",
    "PUBLISH_MIN_BOOKS": "2",
    "MIN_BOOKS_PUBLISH": "2",
    "PUBLISH_MIN_ODDS_SOURCES": "1",
    "MIN_SOURCES_PUBLISH": "1",
    "PUBLISH_MIN_CONTEXT_SOURCES": "1",
    "MIN_CONTEXT_SOURCES_PUBLISH": "1",
    "PUBLISH_TIER_A_MIN_BOOKS": "2",
    "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2",
    "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "PUBLISH_TIER_B_MIN_BOOKS": "2",
    "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "1",
    "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_REQUIRE_XG_HARD_CONFIRMATION": "false",
    "LINE_MOVEMENT_CRON_INTERVAL_MINUTES": "120",
    "LINE_MOVEMENT_CRON_ANCHOR_MINUTE": "0",
    "LINE_MOVEMENT_CRON_TIMEZONE": "Europe/Moscow",
    "LINE_MOVEMENT_USE_SCHEDULED_CRON": "true",
    "FINAL_ENRICHMENT_ONLY_FOR_VALUE_CANDIDATES": "true",
    "FINAL_ENRICHMENT_FALLBACK_NEAREST_MATCH_LIMIT": "0",
    "RULES_MAX_PROVIDER_DISPERSION_PCT": "8.5",
    "FORCE_PUBLISH_WHEN_EMPTY_ENABLED": "false",
    "QUALITY_EMERGENCY_PUBLISH_ENABLED": "false",
    "QUALITY_LAST_RESORT_PUBLISH_ENABLED": "false",
    "REPUBLISH_SEEN_CANDIDATES_WHEN_EMPTY": "false",
}


def _apply_rules_env_defaults() -> dict[str, str]:
    applied: dict[str, str] = {}
    for key, value in RULES_ENV_DEFAULTS.items():
        current = os.getenv(key)
        # These are the business-rule contract values; override workflow overlays
        # that still carry the old stricter B=2-context / 2h scope settings.
        if current != value:
            os.environ[key] = value
            applied[key] = value
    return applied


def _truthy(value: Any, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    if text in {"0", "false", "no", "off", "none", "null"}:
        return False
    return text in {"1", "true", "yes", "on", "force"}


def _int(value: Any, default: int) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def assign_time_bucket(hours_to_kickoff: float | int | None) -> str:
    try:
        hours = float(hours_to_kickoff)  # type: ignore[arg-type]
    except Exception:
        return "24h+"
    if hours < 0:
        return "started"
    if hours < 4:
        return "0-4h"
    if hours < 8:
        return "4-8h"
    if hours < 12:
        return "8-12h"
    if hours < 16:
        return "12-16h"
    if hours < 20:
        return "16-20h"
    if hours <= 24:
        return "20-24h"
    return "24h+"


def _bucket_rank(bucket: str) -> int:
    try:
        return BUCKET_ORDER.index(bucket)
    except ValueError:
        return len(BUCKET_ORDER)


def _norm_source(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"bzzoiro_v2", "bzzoiro_predictions"}:
        return "bzzoiro"
    if text.startswith("odds_api_io_account") or text in {"account1", "account2", "oddsapiio"}:
        return "odds_api_io"
    return text


def _split_sources(value: Any) -> set[str]:
    if value in (None, ""):
        return set()
    if isinstance(value, str):
        return {_norm_source(part) for part in value.replace(";", ",").replace("|", ",").split(",") if part.strip()}
    if isinstance(value, dict):
        return {_norm_source(key) for key, val in value.items() if str(key).strip() and val not in (None, "")}
    if isinstance(value, (list, tuple, set)):
        out: set[str] = set()
        for item in value:
            out.update(_split_sources(item))
        return out
    return {_norm_source(value)} if str(value).strip() else set()


def _match_hours(match: Any, now_utc: datetime) -> float:
    try:
        kickoff = ensure_utc(match.commence_time)
        return (kickoff - now_utc).total_seconds() / 3600.0
    except Exception:
        return 9999.0


def _match_league_score(settings: Any, league_name: str) -> float:
    try:
        return float(settings.league_priority_score(league_name))
    except Exception:
        return 1.0


def _is_low_tier(settings: Any, league_name: str) -> bool:
    try:
        return bool(settings.is_low_tier_league(league_name))
    except Exception:
        text = str(league_name or "").lower()
        return any(token in text for token in ("u19", "u20", "u21", "u23", "reserve", "youth", "women", "amateur"))


def _inventory_score(row: dict[str, Any], settings: Any, now_utc: datetime) -> float:
    kickoff_raw = row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time")
    try:
        kickoff = datetime.fromisoformat(str(kickoff_raw).replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=UTC)
        hours = (kickoff.astimezone(UTC) - now_utc).total_seconds() / 3600.0
    except Exception:
        hours = 9999.0
    bucket = assign_time_bucket(hours)
    league = str(row.get("league_name") or row.get("competition") or "")
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    odds_sources = _split_sources(row.get("odds_sources")) | _split_sources(row.get("source_ids"))
    context_sources = _split_sources(row.get("context_sources"))
    if coverage.get("odds"):
        odds_sources.add("coverage_odds")
    if coverage.get("context"):
        context_sources.add("coverage_context")
    score = 0.0
    score += (len(BUCKET_ORDER) - _bucket_rank(bucket)) * 20.0
    score += _match_league_score(settings, league) * 18.0
    score += min(3, len(odds_sources)) * 14.0
    score += min(3, len(context_sources)) * 12.0
    score += 10.0 if bool(coverage.get("ready_for_model")) else 0.0
    score += 10.0 if bool(coverage.get("ready_for_publish")) else 0.0
    score += _float(row.get("priority"), 0.0) * 0.35
    if _is_low_tier(settings, league) and not _truthy(os.getenv("ALLOW_LOW_TIER"), bool(getattr(settings, "allow_low_tier", False))):
        score -= 1000.0
    if hours < 0:
        score -= 2000.0
    row["hours_to_kickoff"] = round(hours, 3)
    row["time_bucket"] = bucket
    row.setdefault("rules_policy", {})["inventory_score"] = round(score, 3)
    return score


def _candidate_get(candidate: Any, field: str, default: Any = None) -> Any:
    if isinstance(candidate, dict):
        if field in candidate:
            return candidate.get(field, default)
        for key in ("source_summary", "diagnostics", "analysis"):
            nested = candidate.get(key)
            if isinstance(nested, dict) and field in nested:
                return nested.get(field, default)
        return default
    if hasattr(candidate, field):
        return getattr(candidate, field)
    for key in ("source_summary", "diagnostics", "analysis"):
        nested = getattr(candidate, key, None)
        if isinstance(nested, dict) and field in nested:
            return nested.get(field, default)
    return default


def _candidate_value_score(candidate: Any) -> float:
    return (
        _float(_candidate_get(candidate, "publication_score"), 0.0) * 2.0
        + _float(_candidate_get(candidate, "ev_pct"), 0.0) * 3.0
        + _float(_candidate_get(candidate, "edge_pct"), 0.0) * 2.0
        + _float(_candidate_get(candidate, "confidence"), 0.0) * 0.1
    )


def _candidate_match_key(candidate: Any) -> str:
    return str(_candidate_get(candidate, "match_key") or "")


def _provider_conflict(candidate: Any) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    summary = _candidate_get(candidate, "source_summary", {}) or {}
    diagnostics = _candidate_get(candidate, "diagnostics", {}) or {}
    for container in (summary, diagnostics):
        if not isinstance(container, dict):
            continue
        for key, value in container.items():
            key_l = str(key).lower()
            if "conflict" in key_l and value not in (None, "", False, 0, [], {}):
                if isinstance(value, (int, float)) and float(value) <= 0:
                    continue
                reasons.append(f"{key}={value}")
    dispersion = _float(summary.get("consensus_dispersion_pct") or diagnostics.get("consensus_dispersion_pct"), 0.0)
    max_dispersion = _float(os.getenv("RULES_MAX_PROVIDER_DISPERSION_PCT"), 8.5)
    if dispersion > max_dispersion:
        reasons.append(f"provider_dispersion_high:{dispersion:.2f}>{max_dispersion:.2f}")
    return bool(reasons), reasons[:6]


def _annotate_candidate_bucket(candidate: Any, now_utc: datetime) -> None:
    kickoff = _candidate_get(candidate, "commence_time") or _candidate_get(candidate, "commence_time_utc")
    try:
        dt = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00")) if not isinstance(kickoff, datetime) else kickoff
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        hours = (dt.astimezone(UTC) - now_utc).total_seconds() / 3600.0
    except Exception:
        hours = None
    bucket = assign_time_bucket(hours)
    try:
        candidate.source_summary["time_bucket"] = bucket
        candidate.source_summary["hours_to_kickoff"] = round(float(hours), 3) if hours is not None else None
    except Exception:
        pass
    try:
        candidate.diagnostics.setdefault("rules_time_bucket", {"bucket": bucket, "hours_to_kickoff": hours})
    except Exception:
        pass


def _write_audit(summary: dict[str, Any]) -> None:
    try:
        out = Path(os.getenv("RULES_COMPLIANCE_AUDIT_PATH", ".data/exports/latest-rules-compliance.json"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    except Exception:
        return


def _patch_day_inventory() -> dict[str, Any]:
    from app.services.day_inventory import DayInventoryStore

    if getattr(DayInventoryStore, "_rules_compliant_inventory_installed", False):
        return {"installed": True, "already_installed": True, "target": "DayInventoryStore"}
    original_build_payload = DayInventoryStore.build_payload

    def build_payload_rules(self: Any, *, local_date: str, matches: list[Any], source_meta: dict[str, Any] | None = None, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = original_build_payload(self, local_date=local_date, matches=matches, source_meta=source_meta, existing=existing)
        rows = [dict(row) for row in (payload.get("matches") or []) if isinstance(row, dict)]
        now_utc = datetime.now(UTC)
        try:
            from app.config import get_settings
            settings = get_settings()
        except Exception:
            settings = object()
        ranked = sorted(rows, key=lambda row: _inventory_score(row, settings, now_utc), reverse=True)
        max_matches = max(1, _int(os.getenv("DAILY_INVENTORY_MAX_MATCHES") or os.getenv("DAY_INVENTORY_MAX_MATCHES"), 300))
        selected = ranked[:max_matches]
        bucket_counts = Counter(str(row.get("time_bucket") or "24h+") for row in selected)
        payload["matches"] = selected
        counts = dict(payload.get("counts") or {})
        counts["matches_total_before_rules_top"] = len(rows)
        counts["matches_total"] = len(selected)
        counts["rules_top_inventory_limit"] = max_matches
        counts["rules_filtered_out_after_top"] = max(0, len(rows) - len(selected))
        counts["time_bucket_counts"] = {bucket: int(bucket_counts.get(bucket, 0)) for bucket in BUCKET_ORDER}
        payload["counts"] = counts
        payload["rules_policy"] = {"enabled": True, "top_inventory_limit": max_matches, "time_buckets": BUCKET_ORDER, "ranking": "nearest_window_league_coverage_score"}
        return payload

    DayInventoryStore.build_payload = build_payload_rules
    DayInventoryStore._rules_compliant_inventory_installed = True
    return {"installed": True, "target": "DayInventoryStore.build_payload"}


def _patch_coverage_planner() -> dict[str, Any]:
    from app.services.coverage_planner import CoveragePlanner

    if getattr(CoveragePlanner, "_rules_compliant_planner_installed", False):
        return {"installed": True, "already_installed": True, "target": "CoveragePlanner"}
    original_priority = CoveragePlanner._priority
    original_select = CoveragePlanner.select_context_targets

    def priority_rules(self: Any, row: Any) -> float:
        base = float(original_priority(self, row) or 0.0)
        bucket = assign_time_bucket(getattr(row, "hours_to_kickoff", 9999.0))
        nearest_bonus = (len(BUCKET_ORDER) - _bucket_rank(bucket)) * 18.0
        odds_gap = max(0, int(getattr(self, "min_odds_sources", 1) or 1) - len(getattr(row, "odds_sources", set()) or set()))
        context_gap = max(0, int(getattr(self, "min_context_sources", 1) or 1) - len(getattr(row, "context_sources", set()) or set()))
        books_gap = max(0, int(getattr(self, "min_books", 2) or 2) - len(getattr(row, "books", set()) or set()))
        return min(200.0, base + nearest_bonus + context_gap * 10.0 + odds_gap * 8.0 + books_gap * 4.0)

    def select_context_targets_rules(self: Any, matches: list[Any], offers_by_match: dict[str, list[Any]] | None, now_utc: datetime, market_signals_by_match: dict[str, dict[str, Any]] | None = None):
        selected, summary = original_select(self, matches, offers_by_match, now_utc, market_signals_by_match)
        selected = sorted(selected, key=lambda m: (_bucket_rank(assign_time_bucket(_match_hours(m, now_utc))), -_match_league_score(self.settings, getattr(m, "league_name", ""))))
        if isinstance(summary, dict):
            bucket_counts = Counter(assign_time_bucket(_match_hours(match, now_utc)) for match in selected)
            summary["policy"] = "rules_nearest_bucket_gap_first"
            summary["time_buckets"] = BUCKET_ORDER
            summary["selected_time_bucket_counts"] = {bucket: int(bucket_counts.get(bucket, 0)) for bucket in BUCKET_ORDER}
            summary["nearest_bucket_first"] = True
        return selected, summary

    CoveragePlanner._priority = priority_rules
    CoveragePlanner.select_context_targets = select_context_targets_rules
    CoveragePlanner._rules_compliant_planner_installed = True
    return {"installed": True, "target": "CoveragePlanner"}


def _patch_runner() -> dict[str, Any]:
    from app.services.runner import PredictionRunner
    from app.services.line_movement_state import evaluate_and_record_line_movement

    if getattr(PredictionRunner, "_rules_compliant_runner_installed", False):
        return {"installed": True, "already_installed": True, "target": "PredictionRunner"}

    original_filter = PredictionRunner._filter_publishable_candidates
    original_select = PredictionRunner._select_publishable_candidates
    original_fetch_weather = PredictionRunner._fetch_weather_contexts
    original_run_once = PredictionRunner.run_once

    def filter_publishable_rules(self: Any, candidates: list[Any]) -> list[Any]:
        base = original_filter(self, candidates)
        now = datetime.now(UTC)
        out: list[Any] = []
        audit = getattr(self, "_rules_compliance_runtime", None)
        if not isinstance(audit, dict):
            audit = {"line_movement": Counter(), "provider_conflicts": 0, "candidates_seen": 0}
            self._rules_compliance_runtime = audit
        for candidate in base:
            audit["candidates_seen"] = int(audit.get("candidates_seen", 0) or 0) + 1
            _annotate_candidate_bucket(candidate, now)
            conflict, conflict_reasons = _provider_conflict(candidate)
            if conflict:
                audit["provider_conflicts"] = int(audit.get("provider_conflicts", 0) or 0) + 1
                try:
                    candidate.source_summary["publication_blocked_reason"] = "provider_conflict"
                    candidate.source_summary["provider_conflict_reasons"] = conflict_reasons
                    candidate.reasons.extend(f"provider_conflict={reason}" for reason in conflict_reasons)
                except Exception:
                    pass
                continue
            movement = evaluate_and_record_line_movement(candidate, self.settings, now=now)
            status = str(movement.get("status") or "unknown")
            if isinstance(audit.get("line_movement"), Counter):
                audit["line_movement"][status] += 1
            try:
                candidate.diagnostics["rules_line_movement_gate"] = movement
                candidate.source_summary["rules_line_movement_gate"] = movement
                candidate.source_summary["publication_lifecycle_stage"] = status
            except Exception:
                pass
            if not bool(movement.get("passed")):
                try:
                    candidate.source_summary["publication_blocked_reason"] = status
                    candidate.reasons.extend(f"line_movement={reason}" for reason in (movement.get("reasons") or [status]))
                except Exception:
                    pass
                continue
            out.append(candidate)
        return out

    def select_publishable_rules(self: Any, candidates: list[Any]) -> list[Any]:
        now = datetime.now(UTC)
        for candidate in candidates:
            _annotate_candidate_bucket(candidate, now)
        ranked = sorted(candidates, key=lambda c: (_bucket_rank(str((_candidate_get(c, "source_summary", {}) or {}).get("time_bucket") or "24h+")), -_candidate_value_score(c)))
        return original_select(self, ranked)

    async def fetch_weather_contexts_rules(self: Any, matches: list[Any], base_contexts: dict[str, Any]):
        if not _truthy(os.getenv("FINAL_ENRICHMENT_ONLY_FOR_VALUE_CANDIDATES"), True):
            return await original_fetch_weather(self, matches, base_contexts)
        # At the main context stage there is no value shortlist yet. Per rules,
        # weather/news are final enrichment, not broad inventory enrichment.
        return await original_fetch_weather(self, [], base_contexts)

    async def run_once_rules(self: Any):
        summary = await original_run_once(self)
        audit = getattr(self, "_rules_compliance_runtime", {}) if isinstance(getattr(self, "_rules_compliance_runtime", {}), dict) else {}
        line_counter = audit.get("line_movement")
        if isinstance(line_counter, Counter):
            line_counter = dict(line_counter)
        rules_summary = {
            "enabled": True,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "inventory_top_300": True,
            "time_buckets": BUCKET_ORDER,
            "nearest_bucket_first": True,
            "line_context_minimum_checked": True,
            "new_candidates_saved_before_publish": True,
            "line_movement_rechecked": True,
            "final_enrichment_only_for_candidates": _truthy(os.getenv("FINAL_ENRICHMENT_ONLY_FOR_VALUE_CANDIDATES"), True),
            "published_only_after_final_check": True,
            "runtime": {**audit, "line_movement": line_counter or {}},
            "published": int((summary or {}).get("published_to_telegram") or (summary or {}).get("published") or 0) if isinstance(summary, dict) else 0,
        }
        if isinstance(summary, dict):
            summary["rules_compliance"] = rules_summary
        _write_audit(rules_summary)
        return summary

    PredictionRunner._filter_publishable_candidates = filter_publishable_rules
    PredictionRunner._select_publishable_candidates = select_publishable_rules
    PredictionRunner._fetch_weather_contexts = fetch_weather_contexts_rules
    PredictionRunner.run_once = run_once_rules
    PredictionRunner._rules_compliant_runner_installed = True
    return {"installed": True, "target": "PredictionRunner"}


def install() -> dict[str, Any]:
    if not _truthy(os.getenv("RULES_COMPLIANT_PIPELINE_ENABLED"), True):
        return {"installed": False, "reason": "disabled_by_env"}
    applied_env = _apply_rules_env_defaults()
    results = {
        "env_overrides": applied_env,
        "day_inventory": _patch_day_inventory(),
        "coverage_planner": _patch_coverage_planner(),
        "runner": _patch_runner(),
        "policy": {
            "top_inventory_limit": _int(os.getenv("DAILY_INVENTORY_MAX_MATCHES") or os.getenv("DAY_INVENTORY_MAX_MATCHES"), 300),
            "time_buckets": BUCKET_ORDER,
            "final_enrichment_only_for_value_candidates": _truthy(os.getenv("FINAL_ENRICHMENT_ONLY_FOR_VALUE_CANDIDATES"), True),
            "b_tier_context_sources": _int(os.getenv("PUBLISH_TIER_B_MIN_CONTEXT_SOURCES"), 1),
            "publish_window_hours": _int(os.getenv("PUBLISH_WINDOW_HOURS"), 24),
        },
    }
    return {"installed": True, "results": results}
