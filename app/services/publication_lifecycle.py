from __future__ import annotations

from typing import Any

SENT_LIFECYCLE_STATUSES = {"telegram_sent", "published", "sent"}
NOT_SENT_LIFECYCLE_STATUSES = {
    "generated",
    "generated_not_sent",
    "send_failed",
    "blocked",
    "dry_run_selected",
    "shadow_pending",
}
ACTIVE_BET_STATUSES = {"pending", "open", "published", "new", "active"}
SETTLED_BET_STATUSES = {"won", "lost", "push", "void", "cancelled", "canceled", "settled"}


def truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off", "none", "null"}:
        return False
    return raw in {"1", "true", "yes", "on", "force"}


def _nested_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row.get(key)
    for container_name in ("source_summary", "diagnostics", "publication_lifecycle"):
        container = row.get(container_name)
        if isinstance(container, dict):
            for key in keys:
                if key in container:
                    return container.get(key)
    return None


def is_sent_pick_row(row: Any) -> bool:
    """Return True only for candidates that were actually sent to Telegram.

    Generated/exported candidates are useful for diagnostics and fallback, but they must not block a
    later send and must not be counted as published in HARIZON reports.
    """
    if not isinstance(row, dict):
        return False

    lifecycle = str(
        _nested_value(row, "publication_lifecycle_status", "publication_lifecycle_stage") or ""
    ).strip().lower()
    if lifecycle in SENT_LIFECYCLE_STATUSES:
        return True
    if lifecycle in NOT_SENT_LIFECYCLE_STATUSES:
        return False

    if "telegram_sent" in row or isinstance(row.get("source_summary"), dict):
        telegram_sent = _nested_value(row, "telegram_sent")
        if telegram_sent is not None:
            return truthy(telegram_sent, False)

    status = str(row.get("status") or "").strip().lower()
    if status in SETTLED_BET_STATUSES:
        return False
    if status in ACTIVE_BET_STATUSES:
        return True

    return False


def mark_candidate_lifecycle(candidate: Any, *, telegram_sent: bool, failure_reason: str | None = None) -> None:
    status = "telegram_sent" if telegram_sent else "generated_not_sent"
    if failure_reason and not telegram_sent:
        status = "send_failed"
    summary = getattr(candidate, "source_summary", None)
    if not isinstance(summary, dict):
        if isinstance(candidate, dict):
            summary = dict(candidate.get("source_summary") or {})
        else:
            summary = {}
    summary["telegram_sent"] = bool(telegram_sent)
    summary["publication_lifecycle_status"] = status
    summary["publication_lifecycle_stage"] = status
    if failure_reason:
        summary["publication_failure_reason"] = str(failure_reason)
    if isinstance(candidate, dict):
        candidate["source_summary"] = summary
        candidate["telegram_sent"] = bool(telegram_sent)
        candidate["publication_lifecycle_status"] = status
        candidate["publication_lifecycle_stage"] = status
        candidate["status"] = "pending" if telegram_sent else "generated"
    else:
        try:
            candidate.source_summary = summary
        except Exception:
            pass
        diagnostics = getattr(candidate, "diagnostics", None)
        if isinstance(diagnostics, dict):
            diagnostics["publication_lifecycle"] = {
                "telegram_sent": bool(telegram_sent),
                "status": status,
                "failure_reason": failure_reason,
            }
