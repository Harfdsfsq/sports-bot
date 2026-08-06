from __future__ import annotations

"""HARIZON Telegram run report v5/v6 normalized standalone.

This is the single factual Telegram report. It builds one normalized payload from
fresh run artifacts and renders all sections from that payload, so Telegram
numbers do not contradict each other.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc

try:
    from app.services.publication_lifecycle import is_sent_pick_row
except Exception:
    def is_sent_pick_row(row: Any) -> bool:
        if not isinstance(row, dict):
            return False
        return str(row.get("telegram_sent") or "").strip().lower() in {"1", "true", "yes", "on"}
EXPORT_DIR = Path(".data/exports")
DEBUG_PATH = Path(".logs/debug-last-run.json")
OUT_TXT = EXPORT_DIR / "latest-harizon-telegram-run-report.txt"
OUT_JSON = EXPORT_DIR / "latest-harizon-telegram-run-report.json"
OUT_V5_JSON = EXPORT_DIR / "latest-harizon-telegram-run-report-v5.json"
OUT_V5_TXT = EXPORT_DIR / "latest-harizon-telegram-run-report-v5.txt"



LOW_QUALITY_SAMPLE_PATTERNS = [
    r"\bunknown\b", r"\bu[- ]?17\b", r"\bu[- ]?18\b", r"\bu[- ]?19\b", r"\bu[- ]?20\b", r"\bu[- ]?21\b", r"\bu[- ]?23\b",
    r"\bunder[- ]?17\b", r"\bunder[- ]?18\b", r"\bunder[- ]?19\b", r"\bunder[- ]?20\b", r"\bunder[- ]?21\b", r"\bunder[- ]?23\b",
    r"\breserves?\b", r"\byouth\b", r"\bacademy\b", r"\bdevelopment\b", r"\bwomen(?:s)?\b", r"\bamateur\b", r"\bregional\b",
    r"\brussia\s*-?\s*2\.?\s*liga\b", r"\bvtoraya\s+liga\b", r"\bsecond\s+league\b", r"\bthird\s+league\b",
    r"\bii\b", r"\biii\b", r"\b2nd\b", r"\bsecond team\b", r"\bb team\b",
]


def is_low_quality_team_name(name: Any) -> bool:
    text = str(name or "").strip().lower()
    if not text:
        return False
    if re.search(r"\b(?:u[- ]?17|u[- ]?18|u[- ]?19|u[- ]?20|u[- ]?21|u[- ]?23|under[- ]?17|under[- ]?18|under[- ]?19|under[- ]?20|under[- ]?21|under[- ]?23|reserves?|youth|academy|development|women(?:s)?|2nd|second team|b team)\b", text):
        return True
    if re.search(r"(?:^|[\s\-_.])(?:2|ii|iii)$", text):
        return True
    return False



def is_low_quality_sample(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    haystack = " ".join([
        str(row.get("league_name") or row.get("league") or ""),
        str(row.get("home_team") or row.get("home") or ""),
        str(row.get("away_team") or row.get("away") or ""),
    ]).lower()
    if is_low_quality_team_name(row.get("home_team") or row.get("home")) or is_low_quality_team_name(row.get("away_team") or row.get("away")):
        return True
    return any(re.search(pattern, haystack) for pattern in LOW_QUALITY_SAMPLE_PATTERNS)


def clean_top_priority_samples(rows: Any, limit: int = 6) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    cleaned = [row for row in rows if isinstance(row, dict) and not is_low_quality_sample(row)]
    return cleaned[:limit]

def load_json(path: str | Path, default: Any = None) -> Any:
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size <= 0:
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value)))
    except Exception:
        return default


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "ok", "sent"}


def sent_count_from_rows(rows: Any) -> int:
    if not isinstance(rows, list):
        return 0
    return sum(1 for row in rows if is_sent_pick_row(row))


def fallback_sent_count(fallback: dict[str, Any]) -> int:
    """Return only Telegram-confirmed fallback sends.

    A fallback report can legitimately say skipped_existing_pick after the main
    pipeline has already published a pick.  That status must never be counted
    as a new fallback publication and must not become the final top reason.
    """
    if not isinstance(fallback, dict) or not bool(fallback.get("published")):
        return 0
    status = str(fallback.get("status") or "").strip().lower()
    if status.startswith("skipped"):
        return 0
    explicit = first_positive(
        fallback.get("telegram_messages_sent"),
        fallback.get("telegram_sent_count"),
        fallback.get("published_to_telegram"),
        fallback.get("selected_count"),
    )
    if explicit > 0:
        return explicit
    if truthy(fallback.get("telegram_sent")) or truthy(fallback.get("sent")):
        return 1
    return 0


def main_pipeline_sent_count(
    *,
    summary: dict[str, Any],
    publishable: int,
    sent_picks_count: int,
    sent_pending_count: int,
) -> tuple[int, dict[str, Any]]:
    """Return fresh main-pipeline Telegram sends for this run only."""
    summary_has_publication_counters = any(
        key in summary
        for key in ("published_to_telegram", "telegram_picks_sent", "published")
    )
    diagnostics = {
        "summary_has_publication_counters": summary_has_publication_counters,
        "sent_picks_count": int(sent_picks_count or 0),
        "sent_pending_count": int(sent_pending_count or 0),
        "ignored_ledger_sent_pending_count": 0,
        "counter_inconsistent": False,
    }
    if summary_has_publication_counters:
        count = max(
            as_int(summary.get("published_to_telegram"), 0),
            as_int(summary.get("telegram_picks_sent"), 0),
            as_int(summary.get("published"), 0),
        )
        if count > 0 and publishable <= 0 and sent_picks_count <= 0:
            diagnostics["counter_inconsistent"] = True
            diagnostics["ignored_summary_published_count"] = count
            return 0, diagnostics
        return count, diagnostics

    if sent_picks_count > 0:
        return int(sent_picks_count), diagnostics

    # latest-pending-bets/latest-bets are cumulative ledger exports.  They can
    # contain earlier Telegram-confirmed rows for the same day, so they must not
    # make a later no-pick run look like it published again.
    diagnostics["ignored_ledger_sent_pending_count"] = int(sent_pending_count or 0)
    return 0, diagnostics


def final_publish_status(
    *,
    main_pipeline_count: int,
    fallback_count: int,
    fallback_status: str,
) -> dict[str, Any]:
    published_count = max(int(main_pipeline_count or 0), int(fallback_count or 0))
    main_published = int(main_pipeline_count or 0) > 0
    fallback_published = int(fallback_count or 0) > 0
    if main_published:
        top_reason = "main_pipeline_published"
    elif fallback_published:
        top_reason = "fallback_published"
    else:
        top_reason = fallback_status or "n/a"
    return {
        "published_count": published_count,
        "main_pipeline_published": main_published,
        "fallback_published": fallback_published,
        "final_status": "published" if published_count > 0 else "not_published",
        "top_reason_when_published": top_reason,
    }


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def first_positive(*values: Any) -> int:
    for value in values:
        number = as_int(value)
        if number > 0:
            return number
    return 0


def line_guard_waiting_next_run_count(line_guard: dict[str, Any]) -> int:
    dropped = as_int(line_guard.get("candidates_dropped"))
    if dropped <= 0:
        return 0
    waiting = 0
    samples = []
    for file_row in line_guard.get("files") or []:
        if isinstance(file_row, dict):
            value = file_row.get("dropped_sample")
            if isinstance(value, list):
                samples.extend(value)
    for item in samples:
        if not isinstance(item, dict):
            continue
        guard = first_dict(item.get("guard"))
        reasons = [str(reason) for reason in guard.get("reasons") or []]
        status = str(guard.get("line_movement_lifecycle_status") or "")
        if status == "awaiting_next_run" or "needs_next_cron_line_movement_recheck" in reasons:
            waiting += 1
    if waiting:
        return min(dropped, waiting)
    return 0


def has_non_line_candidate_rejections(evaluated: list[Any]) -> bool:
    """Return True when candidates are blocked by safety/quality, not just line lifecycle."""
    line_reasons = {
        "line_movement_guard_waiting_next_run",
        "line_movement_guard_dropped",
        "needs_next_cron_line_movement_recheck",
    }
    for row in evaluated:
        if not isinstance(row, dict):
            continue
        for reason in row.get("reject_reasons") or []:
            text = str(reason or "").strip()
            if not text:
                continue
            if text in line_reasons or "line_movement" in text:
                continue
            return True
        metrics = first_dict(row.get("metrics"))
        for reason in metrics.get("quality_reasons") or []:
            text = str(reason or "").strip()
            if text:
                return True
    return False


def freshness_minutes(path: str | Path) -> float | None:
    try:
        p = Path(path)
        if not p.exists():
            return None
        return max(0.0, (datetime.now(UTC).timestamp() - p.stat().st_mtime) / 60.0)
    except Exception:
        return None


def reason_ru(reason: str) -> str:
    mapping = {
        "canonical_negative_value": "отрицательная canonical value после пересчёта по выбранному коэффициенту",
        "xg_direction_conflict": "направление ставки конфликтует с xG",
        "xg_probability_gap_hard_reject": "модель слишком оптимистична относительно xG-ориентира",
        "quality_xg_probability_gap_hard_reject": "quality: модель слишком оптимистична относительно xG-ориентира",
        "post_calibration_probability_guard": "вероятность после калибровки ниже порога",
        "quality_post_calibration_probability_guard": "quality: вероятность после калибровки ниже порога",
        "quality_market_sanity_totals_xg_contradiction": "quality: market sanity totals/xG contradiction",
        "odds_sources_below_2": "меньше 2 независимых источников линий",
        "context_sources_below_2": "меньше 2 источников контекста",
        "core_api_coverage_below_2_of_3": "меньше 2 из 3 core API",
        "needs_next_cron_line_movement_recheck": "нужен следующий cron для проверки движения линии",
        "controlled_rescue_no_candidate": "controlled reserve не нашёл безопасного кандидата",
        "fallback_publish_no_candidate": "fallback-публикация: нет кандидата",
        "line_movement_guard_dropped": "line movement guard снял кандидата",
        "line_movement_guard_waiting_next_run": "кандидат ждёт следующий cron для второго снимка линии",
        "odds_api_io_auth_failed": "odds-api.io auth failed: invalid API key",
        "odds_api_io_plan_restricted": "odds-api.io: выбранные букмекеры недоступны на текущем тарифе",
        "main_pipeline_published": "опубликовано основным пайплайном",
        "fallback_published": "опубликовано fallback-пайплайном",
        "telegram_sent": "Telegram подтвердил отправку",
        "publication_counter_inconsistent": "счётчик публикации противоречит fresh-воронке",
    }
    return mapping.get(str(reason), str(reason).replace("_", " "))


def artifacts() -> dict[str, Any]:
    return {
        "debug": load_json(DEBUG_PATH, {}),
        "fallback": load_json(EXPORT_DIR / "latest-controlled-fallback-report.json", {}),
        "rescue": load_json(EXPORT_DIR / "latest-rescue-candidates.json", {}),
        "refresh": load_json(EXPORT_DIR / "latest-day-inventory-refresh-plan.json", {}),
        "priority": load_json(EXPORT_DIR / "latest-day-inventory-priority-and-line-state.json", {}),
        "line_guard": load_json(EXPORT_DIR / "latest-line-movement-guard-report.json", {}),
        "day_inventory_summary": load_json(EXPORT_DIR / "latest-day-inventory-summary.json", {}),
        "signal": load_json(EXPORT_DIR / "latest-signal-stack-runtime.json", {}),
        "secondary": load_json(EXPORT_DIR / "latest-secondary-provider-matching.json", {}),
        "windowed_audit": load_json(EXPORT_DIR / "latest-windowed-core-candidate-audit.json", {}),
        "windowed_filter": load_json(EXPORT_DIR / "latest-windowed-core-publication-filter.json", {}),
        "sportlogic_debug": load_json(EXPORT_DIR / "latest-sportlogic-debug.json", {}),
        "sportlogic_final": load_json(EXPORT_DIR / "latest-windowed-core-report-and-sportlogic-final-guard.json", {}),
        "picks": load_json(EXPORT_DIR / "latest-picks.json", []),
        "pending": load_json(EXPORT_DIR / "latest-pending-bets.json", []),
    }


def source_stats(data: dict[str, Any]) -> dict[str, Any]:
    debug = first_dict(data.get("debug"))
    summary = first_dict(debug.get("summary"))
    stats = first_dict(summary.get("source_stats"), debug.get("source_stats"))
    normalized = {str(k): dict(v) for k, v in stats.items() if isinstance(v, dict)}
    signal = first_dict(data.get("signal"))
    secondary = first_dict(data.get("secondary"), signal.get("secondary_matching"))
    if signal:
        bzz = dict(normalized.get("bzzoiro", {}))
        bzz["secondary_offers_added"] = as_int(signal.get("bzzoiro_secondary_offers_added"))
        bzz["metric_contexts_enhanced"] = as_int(signal.get("metric_contexts_enhanced"))
        bzz["secondary_offer_matches"] = as_int(first_dict(secondary.get("offer_sources_match_counts")).get("bzzoiro"))
        bzz["combo_with_odds_api_io"] = as_int(first_dict(secondary.get("offer_source_combinations")).get("bzzoiro+odds_api_io"))
        normalized["bzzoiro"] = bzz
    if secondary:
        normalized["secondary_matching"] = {
            "matches_with_offers": as_int(secondary.get("matches_with_offers")),
            "source_combinations": secondary.get("offer_source_combinations") or {},
            "source_match_counts": secondary.get("offer_sources_match_counts") or {},
        }
    sport_debug = first_dict(data.get("sportlogic_debug"))
    sport_stats = first_dict(sport_debug.get("stats"))
    if sport_stats:
        row = dict(normalized.get("sportlogic", {}))
        row.update(sport_stats)
        normalized["sportlogic"] = row
    return normalized


def provider_plan_restricted(row: dict[str, Any]) -> bool:
    if bool(row.get("plan_restriction")):
        return True
    accounts = row.get("accounts")
    if isinstance(accounts, dict) and any(
        bool(account.get("plan_restriction"))
        for account in accounts.values()
        if isinstance(account, dict)
    ):
        return True
    if bool(row.get("plan_restriction_recovered")):
        return False
    preview = str(row.get("last_body_preview") or "").lower()
    markers = (
        "only available on our paid plan",
        "only available on our paid plans",
        "sharp or exchange book",
        "sharp/exchange",
        "not included in your plan",
    )
    return any(marker in preview for marker in markers)


def provider_auth_failed(row: dict[str, Any]) -> bool:
    if provider_plan_restricted(row):
        return False
    useful_counts = (
        as_int(row.get("offers_parsed")),
        as_int(row.get("events_matched")),
        as_int(row.get("events_fetched")),
        as_int(row.get("matches_built")),
        as_int(row.get("matches_with_2plus_books")),
    )
    if any(value > 0 for value in useful_counts):
        return False
    if bool(row.get("auth_error")):
        return True
    statuses: list[int] = []
    for key in ("event_http_statuses", "odds_http_statuses", "http_statuses"):
        value = row.get(key)
        if isinstance(value, list):
            statuses.extend(as_int(item) for item in value)
    accounts = row.get("accounts")
    if isinstance(accounts, dict):
        for account in accounts.values():
            if not isinstance(account, dict):
                continue
            if bool(account.get("auth_error")):
                return True
            value = account.get("http_statuses")
            if isinstance(value, list):
                statuses.extend(as_int(item) for item in value)
    if any(status == 401 for status in statuses):
        return True
    if any(status == 403 for status in statuses) and not bool(
        row.get("plan_restriction_recovered")
    ):
        return True
    preview = str(row.get("last_body_preview") or "").lower()
    return "valid apikey" in preview or "api key" in preview and "invalid" in preview


def build_payload() -> dict[str, Any]:
    data = artifacts()
    debug = first_dict(data.get("debug"))
    summary = first_dict(debug.get("summary"))
    day_summary = first_dict(data.get("day_inventory_summary"))
    day_counts = first_dict(day_summary.get("counts"))
    fallback = first_dict(data.get("fallback"))
    rescue = first_dict(data.get("rescue"))
    rescue_counts = first_dict(rescue.get("counts"))
    refresh = first_dict(data.get("refresh"))
    priority = first_dict(data.get("priority"))
    line_guard = first_dict(data.get("line_guard"))
    windowed = first_dict(data.get("windowed_audit"))
    windowed_filter = first_dict(data.get("windowed_filter"))
    pool_counts = first_dict(fallback.get("pool_counts"))
    stats = source_stats(data)
    odds = first_dict(stats.get("odds_api_io"))
    odds_plan_restricted = provider_plan_restricted(odds)
    odds_auth_failed = provider_auth_failed(odds)
    sstats = first_dict(stats.get("sstats"))
    bzz = first_dict(stats.get("bzzoiro"))
    sport = first_dict(stats.get("sportlogic"))
    secondary = first_dict(stats.get("secondary_matching"))
    picks = data.get("picks") if isinstance(data.get("picks"), list) else []
    pending = data.get("pending") if isinstance(data.get("pending"), list) else []
    evaluated = fallback.get("evaluated") if isinstance(fallback.get("evaluated"), list) else []

    raw_candidates = first_positive(summary.get("candidates_raw"), rescue_counts.get("candidates_before_quality"), pool_counts.get("debug_candidates_before_quality"))
    candidates_before_quality = first_positive(summary.get("candidates_before_quality"), rescue_counts.get("candidates_before_quality"), pool_counts.get("debug_candidates_before_quality"), raw_candidates)
    publishable = first_positive(summary.get("publishable_candidates"), rescue_counts.get("publishable_candidates"), windowed_filter.get("kept"))
    sent_picks = [x for x in picks if is_sent_pick_row(x)]
    sent_pending = [x for x in pending if is_sent_pick_row(x)]
    main_pipeline_published_count, publication_counter_diagnostics = main_pipeline_sent_count(
        summary=summary,
        publishable=publishable,
        sent_picks_count=len(sent_picks),
        sent_pending_count=len(sent_pending),
    )
    fallback_status = str(fallback.get("status") or "").strip()
    fallback_published_count = fallback_sent_count(fallback)
    publish_status = final_publish_status(
        main_pipeline_count=main_pipeline_published_count,
        fallback_count=fallback_published_count,
        fallback_status=fallback_status,
    )
    published_count = as_int(publish_status.get("published_count"), 0)

    day_inventory_total = first_positive(
        day_counts.get("matches_total"), day_counts.get("matches_total_high_watermark"),
        day_summary.get("day_inventory_total"), day_summary.get("matches_total"),
        summary.get("day_inventory_total"), summary.get("day_inventory_matches"), summary.get("inventory_total"), summary.get("matches_total"),
        refresh.get("active_matches"), refresh.get("day_inventory_total"), refresh.get("matches_total"),
        priority.get("active_matches"), priority.get("day_inventory_total"), priority.get("matches_total"),
        summary.get("matches_seen"),
    )
    ready_for_model = first_positive(
        day_counts.get("matches_ready_for_model"), day_summary.get("matches_ready_for_model"),
        summary.get("ready_for_model"), summary.get("matches_ready_for_model"), summary.get("ready_for_model_count"),
        summary.get("model_matches"), summary.get("model_debug_matches"), candidates_before_quality, raw_candidates,
    )
    day_with_odds = first_positive(day_counts.get("matches_with_odds"), day_summary.get("matches_with_odds"))
    day_with_context = first_positive(day_counts.get("matches_with_context"), day_summary.get("matches_with_context"))

    reasons: Counter[str] = Counter()
    for row in evaluated:
        if not isinstance(row, dict):
            continue
        for reason in row.get("reject_reasons") or []:
            reasons[str(reason)] += 1
        metrics = first_dict(row.get("metrics"))
        for reason in metrics.get("quality_reasons") or []:
            reasons["quality_" + str(reason)] += 1
    for src in (debug.get("rejections"), fallback.get("reason_counts"), fallback.get("reject_reasons"), fallback.get("top_reject_reasons")):
        if isinstance(src, dict):
            for key, value in src.items():
                reasons[str(key)] += as_int(value)
    line_waiting_next_run = line_guard_waiting_next_run_count(line_guard)
    line_dropped = max(0, as_int(line_guard.get("candidates_dropped")) - line_waiting_next_run)
    if line_waiting_next_run > 0:
        reasons["line_movement_guard_waiting_next_run"] += line_waiting_next_run
    if line_dropped > 0:
        reasons["line_movement_guard_dropped"] += line_dropped
    if odds_auth_failed:
        reasons["odds_api_io_auth_failed"] += 1
    elif odds_plan_restricted:
        reasons["odds_api_io_plan_restricted"] += 1
    if bool(publication_counter_diagnostics.get("counter_inconsistent")):
        reasons["publication_counter_inconsistent"] += 1

    coverage = {
        "matches_seen": as_int(summary.get("matches_seen")),
        "day_inventory_total": day_inventory_total,
        "matches_with_offers": as_int(summary.get("matches_with_offers")),
        "matches_with_context": as_int(summary.get("contexts_built")),
        "ready_for_model": ready_for_model,
        "day_inventory_with_odds": day_with_odds,
        "day_inventory_with_context": day_with_context,
        "odds_offers_primary": as_int(odds.get("offers_parsed")),
        "bzzoiro_secondary_offers_added": as_int(bzz.get("secondary_offers_added")),
        "matches_with_2plus_books": as_int(odds.get("matches_with_2plus_books")),
        "bzzoiro_odds_overlap_with_odds_api_io": as_int(bzz.get("combo_with_odds_api_io")),
        "secondary_combinations": secondary.get("source_combinations") or {},
    }
    funnel = {
        "raw_candidates": raw_candidates,
        "candidates_before_quality": candidates_before_quality,
        "passed_candidates": first_positive(rescue_counts.get("passed_candidates"), summary.get("candidates_raw"), raw_candidates),
        "publishable_candidates": publishable,
        "published_count": published_count,
        "main_pipeline_published_count": main_pipeline_published_count,
        "main_pipeline_published": bool(publish_status.get("main_pipeline_published")),
        "main_pipeline_publication_counter_diagnostics": publication_counter_diagnostics,
        "fallback_candidates_seen": as_int(fallback.get("candidates_seen")),
        "fallback_evaluated": len(evaluated),
        "fallback_status": fallback_status or "n/a",
        "fallback_published_count": fallback_published_count,
        "fallback_published": bool(publish_status.get("fallback_published")),
        "final_publication_status": publish_status.get("final_status"),
        "windowed_audit_candidates": as_int(windowed.get("candidates_in")),
        "windowed_publish_allowed": as_int(windowed.get("publish_allowed_by_coverage")),
        "windowed_publish_blocked": as_int(windowed.get("publish_blocked_by_coverage")),
        "publish_filter_input": as_int(windowed_filter.get("input")),
        "publish_filter_kept": as_int(windowed_filter.get("kept")),
        "publish_filter_blocked": as_int(windowed_filter.get("blocked")),
    }
    api = {
        "odds_api_io": {"events_req": as_int(odds.get("event_requests")), "odds_req": as_int(odds.get("odds_requests")), "matched": as_int(odds.get("events_matched")), "offers": as_int(odds.get("offers_parsed")), "books_2plus": as_int(odds.get("matches_with_2plus_books")), "errors": as_int(odds.get("response_errors")), "auth_failed": odds_auth_failed, "plan_restricted": odds_plan_restricted},
        "sstats": {"requests": as_int(sstats.get("requests")), "contexts": as_int(sstats.get("contexts_built")), "rows": as_int(sstats.get("rows_fetched")), "errors": as_int(sstats.get("response_errors")), "deep_enriched": as_int(first_dict(sstats.get("sstats_deep")).get("contexts_enriched"))},
        "bzzoiro": {"requests": as_int(bzz.get("requests")), "contexts": as_int(bzz.get("contexts_built")), "events": as_int(bzz.get("events_fetched"), as_int(bzz.get("rows_fetched"))), "secondary_offers_added": as_int(bzz.get("secondary_offers_added")), "overlap": as_int(bzz.get("combo_with_odds_api_io")), "errors": as_int(bzz.get("response_errors"))},
        "sportlogic": {"enabled": bool(first_dict(data.get("sportlogic_final")).get("sportlogic", {}).get("enabled") if isinstance(first_dict(data.get("sportlogic_final")).get("sportlogic"), dict) else sport.get("enabled")), "requests": as_int(sport.get("requests")), "fixtures": max(as_int(sport.get("fixtures_fetched")), as_int(sport.get("games_fetched"))), "odds_requests": as_int(sport.get("odds_requests")), "matched": as_int(sport.get("events_matched")), "offers": as_int(sport.get("offers_parsed")), "errors": as_int(sport.get("response_errors")), "diagnosis": str(sport.get("diagnosis") or ""), "runtime_error": str(sport.get("runtime_error") or "")},
    }
    line = {"final_pre_kickoff_checks": as_int(refresh.get("final_pre_kickoff_checks")), "no_more_regular_run_before_kickoff": as_int(refresh.get("no_more_regular_run_before_kickoff")), "seen": as_int(line_guard.get("candidates_seen")), "kept": as_int(line_guard.get("candidates_kept")), "dropped": as_int(line_guard.get("candidates_dropped")), "waiting_next_run": line_waiting_next_run, "dropped_final": line_dropped}

    if published_count > 0:
        status, status_ru = "published", "✅ прогноз опубликован"
    elif coverage["matches_with_offers"] <= 0:
        status, status_ru = "no_lines", "🔴 нет свежих линий"
    elif raw_candidates <= 0:
        status, status_ru = "lines_but_no_raw_candidates", "🟠 линии есть, raw-кандидатов нет"
    elif publishable <= 0:
        status, status_ru = "candidates_but_quality_rejected", "🟡 кандидаты есть, quality/value не пропустили"
    else:
        status, status_ru = "coverage_guard_blocked", "🟡 кандидаты есть, coverage/movement guard заблокировал публикацию"

    if published_count > 0:
        top_reason = str(publish_status.get("top_reason_when_published") or "telegram_sent")
    elif odds_auth_failed and raw_candidates <= 0:
        top_reason = "odds_api_io_auth_failed"
    elif line_dropped > 0:
        top_reason = "line_movement_guard_dropped"
    elif line_waiting_next_run > 0 and not has_non_line_candidate_rejections(evaluated):
        top_reason = "line_movement_guard_waiting_next_run"
    else:
        top_reason = reasons.most_common(1)[0][0] if reasons else str(fallback_status or "n/a")
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "version": "harizon-telegram-report-v6-standalone-single-source",
        "status": status,
        "status_ru": status_ru,
        "top_reason": top_reason,
        "coverage": coverage,
        "funnel": funnel,
        "api": api,
        "line_guard": line,
        "reasons": [{"reason": k, "reason_ru": reason_ru(k), "count": int(v)} for k, v in reasons.most_common(12) if int(v) > 0],
        "samples": {"fallback_evaluated": evaluated[:5], "top_priority_matches": clean_top_priority_samples(refresh.get("top_priority_matches"), 6)},
        "artifacts": {"debug_age_min": freshness_minutes(DEBUG_PATH), "run_log_age_min": freshness_minutes(EXPORT_DIR / "latest-run-bot.log"), "fallback_age_min": freshness_minutes(EXPORT_DIR / "latest-controlled-fallback-report.json"), "signal_stack_age_min": freshness_minutes(EXPORT_DIR / "latest-signal-stack-runtime.json")},
    }


def render(payload: dict[str, Any]) -> str:
    c, f, api, lg = payload["coverage"], payload["funnel"], payload["api"], payload["line_guard"]
    lines: list[str] = [
        "🧾 HARIZON run report v6 — единая фактическая сводка",
        f"• Итог: {payload['status_ru']}",
        f"• Главная причина: {reason_ru(payload.get('top_reason'))}",
        "",
        "📦 Покрытие",
        f"• Матчи в run: {c['matches_seen']} | day inventory: {c['day_inventory_total']}",
        f"• С линиями: {c['matches_with_offers']} | с контекстом: {c['matches_with_context']} | ready model: {c['ready_for_model']}",
        f"• odds-api.io offers: {c['odds_offers_primary']} | Bzzoiro secondary offers: {c['bzzoiro_secondary_offers_added']}",
        f"• 2+ букмекера odds-api.io: {c['matches_with_2plus_books']} | 2-source overlap Bzzoiro+odds-api.io: {c['bzzoiro_odds_overlap_with_odds_api_io']}",
    ]
    combos = c.get("secondary_combinations") if isinstance(c.get("secondary_combinations"), dict) else {}
    if combos:
        lines.append("• Source combinations: " + ", ".join(f"{k}: {v}" for k, v in sorted(combos.items(), key=lambda item: -as_int(item[1]))[:6]))
    lines += [
        "",
        "🧪 Воронка кандидатов",
        f"• Raw/candidates before quality: {f['raw_candidates']} / {f['candidates_before_quality']}",
        f"• Passed quality: {f['passed_candidates']} | publishable: {f['publishable_candidates']} | опубликовано: {f['published_count']}",
        f"• Main pipeline: published {f.get('main_pipeline_published')} ({f.get('main_pipeline_published_count', 0)})",
        f"• Controlled fallback: status {f.get('fallback_status', 'n/a')} | seen {f['fallback_candidates_seen']} | evaluated {f['fallback_evaluated']} | published {f['fallback_published']} ({f.get('fallback_published_count', 0)})",
        f"• Windowed coverage: audit {f['windowed_audit_candidates']} | allowed {f['windowed_publish_allowed']} | blocked {f['windowed_publish_blocked']} | publish-filter input {f['publish_filter_input']}",
        "",
        "🛡️ Pre-publish / line movement",
        f"• Final pre-kickoff checks: {lg['final_pre_kickoff_checks']} | no next regular run: {lg['no_more_regular_run_before_kickoff']}",
        f"• Line guard: seen {lg['seen']} | kept {lg['kept']} | dropped {lg['dropped']}",
        "",
        "📡 Core API",
    ]
    odds, sstats, bzz, sport = api["odds_api_io"], api["sstats"], api["bzzoiro"], api["sportlogic"]
    lines += [
        f"• odds_api_io: events req {odds['events_req']}, odds req {odds['odds_req']}, matched {odds['matched']}, offers {odds['offers']}, 2+ books {odds['books_2plus']}, err {odds['errors']}, auth_failed {odds.get('auth_failed')}",
        f"• sstats: req {sstats['requests']}, ctx {sstats['contexts']}, rows {sstats['rows']}, err {sstats['errors']}, deep enriched {sstats['deep_enriched']}",
        f"• bzzoiro: req {bzz['requests']}, ctx {bzz['contexts']}, events {bzz['events']}, secondary offers {bzz['secondary_offers_added']}, overlap odds-api.io {bzz['overlap']}, err {bzz['errors']}",
        f"• sportlogic: enabled {sport['enabled']}, req {sport['requests']}, odds req {sport['odds_requests']}, matched {sport['matched']}, offers {sport['offers']}, err {sport['errors']}",
        "",
        "🚫 Почему не опубликовано" if payload["status"] != "published" else "🚫 Почему не опубликовано: не применимо, Telegram уже подтвердил отправку",
    ]
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    if reasons:
        total = sum(as_int(x.get("count")) for x in reasons) or 1
        for row in reasons[:8]:
            count = as_int(row.get("count"))
            lines.append(f"• {row.get('reason_ru')}: {count} ({round(count * 100.0 / total)}%)")
    else:
        lines.append("• Нет reject reasons в свежих артефактах.")
    evaluated = payload.get("samples", {}).get("fallback_evaluated") if isinstance(payload.get("samples"), dict) else []
    if isinstance(evaluated, list) and evaluated:
        lines += ["", "🔎 Проверенные reserve-кандидаты"]
        for idx, row in enumerate([x for x in evaluated if isinstance(x, dict)][:4], 1):
            metrics = first_dict(row.get("metrics"))
            lines.append(f"{idx}. {row.get('home_team')} — {row.get('away_team')} | {row.get('selection')} @{as_float(metrics.get('odds')):.2f} | EV {as_float(metrics.get('canonical_ev_pct')):+.1f}% | edge {as_float(metrics.get('canonical_edge_pp')):+.1f} п.п. | q {as_float(metrics.get('quality_score')):.1f}")
            reject = ", ".join(reason_ru(str(x)) for x in (row.get("reject_reasons") or [])[:3])
            if reject:
                lines.append(f"   • reject: {reject}")
    lines += ["", "📌 Вывод"]
    if payload["status"] == "published":
        lines.append("• Прогноз реально опубликован; все цифры выше взяты из одного нормализованного payload.")
    elif odds.get("auth_failed"):
        lines.append("• Main technical blocker: odds-api.io rejected the configured API key, so the run had too few usable primary lines for candidate generation.")
    elif c["bzzoiro_odds_overlap_with_odds_api_io"] < 15:
        lines.append("• Главный технический bottleneck: мало матчей с 2 independent odds sources. Нужно добирать SportLogic/Bzzoiro overlap, а не ослаблять guards.")
    elif f["fallback_evaluated"] > 0:
        lines.append("• Candidate pipeline работает: резерв проверял кандидатов, но value/xG/quality не разрешили публикацию.")
    else:
        lines.append("• Нужно смотреть candidate factory/mapping: линии и контекст есть, но кандидаты не дошли до проверки.")
    lines.append("• Отчёт использует один payload, поэтому секции не должны противоречить друг другу.")
    return "\n".join(lines)


def split_message(text: str, soft_limit: int = 3600) -> list[str]:
    if len(text) <= soft_limit:
        return [text]
    chunks, current, length = [], [], 0
    for line in text.splitlines():
        add = len(line) + 1
        if current and length + add > soft_limit:
            chunks.append("\n".join(current)); current, length = [], 0
        current.append(line); length += add
    if current:
        chunks.append("\n".join(current))
    return chunks


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    chunks = split_message(text, int(os.getenv("TELEGRAM_MESSAGE_SOFT_LIMIT") or 3600))
    ok = True
    for idx, part in enumerate(chunks, 1):
        if len(chunks) > 1:
            part = f"🧾 Подробный отчёт run — часть {idx}/{len(chunks)}\n\n" + part
        data = parse.urlencode({"chat_id": chat_id, "text": part, "disable_web_page_preview": "true"}).encode("utf-8")
        try:
            req = request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
            with request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except Exception:
                payload = {}
            result = payload.get("result") if isinstance(payload, dict) and isinstance(payload.get("result"), dict) else {}
            blocked = isinstance(payload, dict) and bool(payload.get("blocked_by_market_family_publication_guard"))
            if not (isinstance(payload, dict) and payload.get("ok") is True and result.get("message_id") and not blocked):
                ok = False
        except Exception:
            ok = False
    return ok


def main() -> int:
    payload = build_payload()
    text = render(payload)
    payload["text_length"] = len(text)
    payload["telegram_sent"] = False
    write_json(OUT_V5_JSON, payload)
    write_text(OUT_V5_TXT, text + "\n")
    write_json(OUT_JSON, payload)
    write_text(OUT_TXT, text + "\n")
    if os.getenv("TELEGRAM_CHAT_ID") and (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")):
        payload["telegram_sent"] = send_telegram(text)
        write_json(OUT_V5_JSON, payload)
        write_json(OUT_JSON, payload)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
