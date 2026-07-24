"""HARIZON report v14: current-run publication truth and runner errors.

Cumulative ledgers such as latest-picks.json are historical state, not evidence that the
current run sent a Telegram prediction. This layer keeps the v13 rendering repairs but
recomputes publication status from rows timestamped inside the current run and surfaces
the main runner exception when present.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts import send_harizon_telegram_run_report_v13 as v13

EXPORT = Path(".data/exports")
DEBUG = Path(".logs/debug-last-run.json")


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


def _row_sent_time(row: dict[str, Any]) -> datetime | None:
    for key in (
        "sent_at",
        "sent_at_utc",
        "published_at_utc",
        "published_at",
        "created_at_utc",
        "created_at",
    ):
        parsed = _parse_time(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _current_run_anchor(now: datetime) -> tuple[datetime, str]:
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
            return parsed - timedelta(minutes=2), filename
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
        sent_at = _row_sent_time(row)
        if sent_at is None:
            continue
        if anchor <= sent_at <= now + timedelta(minutes=2):
            result.append(row)
    return result


def _runner_error() -> str:
    payload = v13._load(DEBUG, {})
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("error") or "").strip()


def _non_publish_status(payload: dict[str, Any]) -> tuple[str, str]:
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
    if int(coverage.get("matches_with_offers") or 0) <= 0:
        return "no_lines", "🔴 нет свежих линий"
    if int(funnel.get("raw_candidates") or 0) <= 0:
        return "lines_but_no_raw_candidates", "🟠 линии есть, raw-кандидатов нет"
    if int(funnel.get("publishable_candidates") or 0) <= 0:
        return "candidates_but_quality_rejected", "🟡 кандидаты есть, quality/value не пропустили"
    return "coverage_guard_blocked", "🟡 кандидаты есть, coverage/movement guard заблокировал публикацию"


def repair_payload(payload: Any, *, now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    now = (now or datetime.now(UTC)).astimezone(UTC)
    anchor, anchor_source = _current_run_anchor(now)
    picks = v13._load(EXPORT / "latest-picks.json", [])
    pending = v13._load(EXPORT / "latest-pending-bets.json", [])
    fresh_picks = current_run_sent_rows(picks, anchor=anchor, now=now)
    cumulative_sent_picks = sum(_sent_row(row) for row in picks if isinstance(row, dict)) if isinstance(picks, list) else 0
    cumulative_sent_pending = sum(_sent_row(row) for row in pending if isinstance(row, dict)) if isinstance(pending, list) else 0

    funnel = payload.setdefault("funnel", {})
    if not isinstance(funnel, dict):
        funnel = {}
        payload["funnel"] = funnel
    old_main = int(funnel.get("main_pipeline_published_count") or 0)
    fallback_count = int(funnel.get("fallback_published_count") or 0)
    main_count = len(fresh_picks)
    published_count = max(main_count, fallback_count)
    diagnostics = funnel.get("main_pipeline_publication_counter_diagnostics")
    diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
    diagnostics.update(
        {
            "current_run_anchor_utc": anchor.isoformat(),
            "current_run_anchor_source": anchor_source,
            "current_run_sent_picks_count": main_count,
            "cumulative_sent_picks_count": cumulative_sent_picks,
            "cumulative_sent_pending_count": cumulative_sent_pending,
            "ignored_cumulative_sent_picks_count": max(0, cumulative_sent_picks - main_count),
            "ignored_ledger_sent_pending_count": cumulative_sent_pending,
            "old_main_pipeline_count": old_main,
            "current_run_scope_enforced": True,
        }
    )
    funnel.update(
        {
            "main_pipeline_published_count": main_count,
            "main_pipeline_published": main_count > 0,
            "main_pipeline_publication_counter_diagnostics": diagnostics,
            "published_count": published_count,
            "fallback_published": fallback_count > 0,
            "final_publication_status": "published" if published_count > 0 else "not_published",
        }
    )

    error = _runner_error()
    report_diagnostics = payload.setdefault("diagnostics", {})
    if not isinstance(report_diagnostics, dict):
        report_diagnostics = {}
        payload["diagnostics"] = report_diagnostics
    if error:
        report_diagnostics["runner_error"] = error

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
    elif error:
        payload.update(
            {
                "status": "run_failed",
                "status_ru": "🔴 основной run завершился ошибкой",
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
    v13._refresh_truth()
    module = v13._load_v12()
    v13._install(module)
    _install(module)
    return int(module.v9.v8.v7.v5.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
