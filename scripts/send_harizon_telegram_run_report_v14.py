"""HARIZON report v14: authoritative current-run publication and lifecycle truth.

Cumulative ledgers are historical state. Current-run publication count comes from the
fresh runner counters. Ledger rows are only a compatibility fallback when they carry an
explicit Telegram send timestamp; synthetic ``published_at_utc`` is never send evidence.
A timed-out run must never reuse an older ``debug-last-run.json`` as current funnel truth.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts import send_harizon_telegram_run_report_v13 as v13

EXPORT = Path(".data/exports")
DEBUG = Path(".logs/debug-last-run.json")
LIFECYCLE = EXPORT / "latest-main-run-lifecycle.json"
STEP_STATUS = EXPORT / "latest-run-bot-step-status.json"
RUN_LOG = EXPORT / "latest-run-bot.log"


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""


def _runtime_failure_detail() -> str:
    text = _read_text(RUN_LOG)
    if not text:
        return ""
    patterns = (
        r"^(SyntaxError: .+)$",
        r"^((?:[A-Za-z_][\w.]*)(?:Error|Exception): .+)$",
        r"^(Traceback \(most recent call last\):)$",
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for pattern in patterns:
        for line in reversed(lines):
            match = re.match(pattern, line)
            if match:
                return match.group(1)[:240]
    return ""


def _sent_row(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    value = str(row.get("telegram_sent") or "").strip().lower()
    lifecycle = str(
        row.get("publication_lifecycle_status")
        or row.get("publication_lifecycle_stage")
        or ""
    ).strip().lower()
    return value in {"1", "true", "yes", "on"} or lifecycle == "telegram_sent"


def _explicit_send_time(row: dict[str, Any]) -> datetime | None:
    """Return only a transport/send timestamp, never a normalized ledger timestamp."""

    for key in (
        "telegram_sent_at_utc",
        "telegram_sent_at",
        "sent_at_utc",
        "sent_at",
    ):
        parsed = _parse_time(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _debug_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary")
    return summary if isinstance(summary, dict) else {}


def _debug_started_at(payload: Any) -> datetime | None:
    summary = _debug_summary(payload)
    return _parse_time(
        summary.get("started_time_utc")
        or summary.get("started_at_utc")
        or summary.get("started_time")
    )


def _run_lifecycle_truth(now: datetime) -> dict[str, Any]:
    lifecycle = v13._load(LIFECYCLE, {})
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    started = _parse_time(lifecycle.get("started_at_utc"))
    age_minutes = None
    if started is not None:
        age_minutes = max(0.0, (now - started).total_seconds() / 60.0)

    debug = v13._load(DEBUG, {})
    debug_started = _debug_started_at(debug)
    fresh_debug = bool(
        started is not None
        and debug_started is not None
        and started - timedelta(minutes=1) <= debug_started <= now + timedelta(minutes=2)
    )

    step_text = _read_text(STEP_STATUS)
    step_lower = step_text.lower()
    status_match = re.search(r"status\s+(\d+)", step_lower)
    process_status = int(status_match.group(1)) if status_match else None
    timed_out = process_status == 124 or bool(
        re.search(r"\brun bot timed out\b", step_lower)
    )
    failed_step = "run bot failed" in step_lower or (
        process_status is not None and process_status != 0
    )
    step_ok = "run bot ok" in step_lower
    lifecycle_status = str(lifecycle.get("status") or "").strip().lower()
    current_lifecycle = bool(
        started is not None
        and timedelta(0) <= now - started <= timedelta(hours=2)
    )

    failure_reason = ""
    failure_detail = _runtime_failure_detail()
    if timed_out:
        failure_reason = "runner_timeout_status_124"
    elif failed_step:
        failure_reason = f"runner_failed_status_{process_status or 'unknown'}"
    elif lifecycle_status in {"failed", "timed_out", "timeout", "error"}:
        failure_reason = f"runner_{lifecycle_status}"
    elif current_lifecycle and not fresh_debug and step_ok:
        failure_reason = "runner_completed_without_fresh_debug"
    elif (
        current_lifecycle
        and not fresh_debug
        and age_minutes is not None
        and age_minutes >= 8.0
    ):
        failure_reason = "runner_incomplete_without_fresh_debug"

    return {
        "present": bool(lifecycle),
        "current": current_lifecycle,
        "status": lifecycle_status,
        "started_at_utc": started.isoformat() if started is not None else None,
        "age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        "github_run_id": lifecycle.get("github_run_id"),
        "github_run_attempt": lifecycle.get("github_run_attempt"),
        "stale_debug_removed_at_start": bool(lifecycle.get("stale_debug_removed")),
        "debug_started_at_utc": debug_started.isoformat() if debug_started is not None else None,
        "fresh_debug": fresh_debug,
        "step_status_text": step_text[:300],
        "process_status": process_status,
        "timed_out": timed_out,
        "failed": bool(failure_reason),
        "failure_reason": failure_reason,
        "failure_detail": failure_detail,
    }


def _current_run_anchor(now: datetime) -> tuple[datetime, str]:
    lifecycle = _run_lifecycle_truth(now)
    lifecycle_started = _parse_time(lifecycle.get("started_at_utc"))
    if lifecycle.get("current") and lifecycle_started is not None:
        return lifecycle_started - timedelta(minutes=1), "latest-main-run-lifecycle.json"

    debug = v13._load(DEBUG, {})
    summary = _debug_summary(debug)
    parsed = _parse_time(summary.get("started_time_utc") or summary.get("started_at_utc"))
    if parsed is not None and timedelta(0) <= now - parsed <= timedelta(hours=2):
        return parsed - timedelta(minutes=1), "debug.summary.started_time_utc"
    for filename in (
        "latest-runbot-discovery-first-prepare.json",
        "latest-focused-alpha-cohort.json",
    ):
        payload = v13._load(EXPORT / filename, {})
        if not isinstance(payload, dict):
            continue
        parsed = _parse_time(
            payload.get("created_at_utc")
            or payload.get("started_at_utc")
            or payload.get("created_at")
        )
        if parsed is not None and timedelta(0) <= now - parsed <= timedelta(hours=2):
            return parsed - timedelta(minutes=1), filename
    return now - timedelta(minutes=45), "bounded_45_minute_fallback"


def current_run_sent_rows(
    rows: Any,
    *,
    anchor: datetime,
    now: datetime,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not _sent_row(row):
            continue
        sent_at = _explicit_send_time(row)
        if sent_at is None:
            continue
        if anchor <= sent_at <= now + timedelta(minutes=2):
            result.append(row)
    return result


def _debug_truth(now: datetime | None = None) -> tuple[dict[str, Any], dict[str, Any], str]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    payload = v13._load(DEBUG, {})
    lifecycle = _run_lifecycle_truth(current)
    if lifecycle.get("current") and not lifecycle.get("fresh_debug"):
        return {}, {}, str(lifecycle.get("failure_reason") or "stale_debug_ignored")
    if not isinstance(payload, dict):
        return {}, {}, ""
    summary = _debug_summary(payload)
    return payload, summary, str(payload.get("error") or "").strip()


def _counter(value: Any) -> int:
    try:
        return max(0, int(float(str(value))))
    except Exception:
        return 0


def _debug_main_publication_count(summary: dict[str, Any]) -> tuple[int, bool]:
    keys = (
        "telegram_messages_sent",
        "published_to_telegram",
        "telegram_picks_sent",
        "published_current_run",
    )
    declared = any(key in summary for key in keys)
    return max((_counter(summary.get(key)) for key in keys), default=0), declared


def _non_publish_status(payload: dict[str, Any]) -> tuple[str, str]:
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
    if int(coverage.get("matches_with_offers") or 0) <= 0:
        return "no_lines", "🔴 нет свежих линий"
    if int(funnel.get("raw_candidates") or 0) <= 0:
        return "lines_but_no_raw_candidates", "🟠 линии есть, raw-кандидатов нет"
    if int(funnel.get("publishable_candidates") or 0) <= 0:
        return (
            "candidates_but_quality_rejected",
            "🟡 кандидаты есть, quality/value не пропустили",
        )
    return (
        "coverage_guard_blocked",
        "🟡 кандидаты есть, coverage/movement guard заблокировал публикацию",
    )


def _zero_stale_main_funnel(funnel: dict[str, Any]) -> None:
    for key in (
        "raw_candidates",
        "candidates_before_quality",
        "candidates_after_quality",
        "quality_passed_count",
        "passed_candidates",
        "publishable_candidates",
        "main_pipeline_published_count",
    ):
        if key in funnel or key in {
            "raw_candidates",
            "candidates_before_quality",
            "candidates_after_quality",
            "publishable_candidates",
        }:
            funnel[key] = 0
    funnel["main_pipeline_published"] = False


def repair_payload(payload: Any, *, now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    now = (now or datetime.now(UTC)).astimezone(UTC)
    lifecycle = _run_lifecycle_truth(now)
    anchor, anchor_source = _current_run_anchor(now)
    debug, summary, debug_error = _debug_truth(now)
    del debug

    picks = v13._load(EXPORT / "latest-picks.json", [])
    pending = v13._load(EXPORT / "latest-pending-bets.json", [])
    fresh_picks = current_run_sent_rows(picks, anchor=anchor, now=now)
    debug_count, debug_counter_declared = _debug_main_publication_count(summary)
    main_count = debug_count if debug_counter_declared else len(fresh_picks)

    cumulative_sent_picks = (
        sum(_sent_row(row) for row in picks if isinstance(row, dict))
        if isinstance(picks, list)
        else 0
    )
    cumulative_sent_pending = (
        sum(_sent_row(row) for row in pending if isinstance(row, dict))
        if isinstance(pending, list)
        else 0
    )

    funnel = payload.setdefault("funnel", {})
    if not isinstance(funnel, dict):
        funnel = {}
        payload["funnel"] = funnel
    old_main = int(funnel.get("main_pipeline_published_count") or 0)
    fallback_count = int(funnel.get("fallback_published_count") or 0)
    if lifecycle.get("failed") and not lifecycle.get("fresh_debug"):
        _zero_stale_main_funnel(funnel)
        main_count = 0
        debug_counter_declared = False
    published_count = max(main_count, fallback_count)
    diagnostics = funnel.get("main_pipeline_publication_counter_diagnostics")
    diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
    diagnostics.update(
        {
            "current_run_anchor_utc": anchor.isoformat(),
            "current_run_anchor_source": anchor_source,
            "debug_counter_declared": debug_counter_declared,
            "debug_current_run_publication_count": debug_count,
            "explicit_send_timestamp_rows": len(fresh_picks),
            "current_run_sent_picks_count": main_count,
            "cumulative_sent_picks_count": cumulative_sent_picks,
            "cumulative_sent_pending_count": cumulative_sent_pending,
            "ignored_cumulative_sent_picks_count": max(
                0, cumulative_sent_picks - main_count
            ),
            "ignored_ledger_sent_pending_count": cumulative_sent_pending,
            "old_main_pipeline_count": old_main,
            "published_at_utc_accepted_as_send_evidence": False,
            "current_run_scope_enforced": True,
            "main_run_lifecycle": lifecycle,
            "stale_debug_accepted": False if lifecycle.get("current") else None,
        }
    )
    funnel.update(
        {
            "main_pipeline_published_count": main_count,
            "main_pipeline_published": main_count > 0,
            "main_pipeline_publication_counter_diagnostics": diagnostics,
            "published_count": published_count,
            "fallback_published": fallback_count > 0,
            "final_publication_status": (
                "published" if published_count > 0 else "not_published"
            ),
        }
    )

    report_diagnostics = payload.setdefault("diagnostics", {})
    if not isinstance(report_diagnostics, dict):
        report_diagnostics = {}
        payload["diagnostics"] = report_diagnostics
    report_diagnostics["main_run_lifecycle"] = lifecycle
    error = str(lifecycle.get("failure_reason") or debug_error).strip()
    if error:
        report_diagnostics["runner_error"] = error
    failure_detail = str(lifecycle.get("failure_detail") or "").strip()
    if failure_detail:
        report_diagnostics["runner_error_detail"] = failure_detail

    if main_count > 0:
        payload.update(
            {
                "status": "published",
                "status_ru": "✅ прогноз опубликован",
                "top_reason": "main_pipeline_published",
            }
        )
    elif fallback_count > 0:
        payload.update(
            {
                "status": "published",
                "status_ru": "✅ прогноз опубликован",
                "top_reason": "fallback_published",
            }
        )
    elif lifecycle.get("timed_out"):
        payload.update(
            {
                "status": "run_failed",
                "status_ru": "🔴 основной run превысил лимит времени",
                "top_reason": "runner_timeout",
            }
        )
    elif lifecycle.get("failed") or error:
        process_status = lifecycle.get("process_status")
        payload.update(
            {
                "status": "run_failed",
                "status_ru": (
                    "🔴 основной run завершился с ошибкой"
                    + (f" (status {process_status})" if process_status is not None else "")
                ),
                "top_reason": "runner_error",
            }
        )
    else:
        status, status_ru = _non_publish_status(payload)
        payload["status"] = status
        payload["status_ru"] = status_ru
        if payload.get("top_reason") in {"main_pipeline_published", "fallback_published"}:
            payload["top_reason"] = str(funnel.get("fallback_status") or status)
    return payload


def _install(module: Any) -> None:
    base = module.v9.v8.v7.v5
    original_build = base.build_payload

    def build_payload() -> dict[str, Any]:
        return repair_payload(original_build())

    base.build_payload = build_payload


def main() -> int:
    try:
        from scripts.repair_synthetic_publication_timestamps import repair_exports

        repair_exports()
    except Exception:
        pass
    v13._refresh_truth()
    module = v13._load_v12()
    v13._install(module)
    _install(module)
    return int(module.v9.v8.v7.v5.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
