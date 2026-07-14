from __future__ import annotations

"""Normalize exported pick/bet payloads after publication.

This is a post-run safety pass.  The prediction model may create a candidate with
``market_move=insufficient_history`` before the later line-movement hook confirms
that the line was rechecked or that no later regular run is available before
kick-off.  The Telegram report and persisted exports should not keep that stale
reason after a real Telegram send was confirmed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
OUT_PATH = EXPORT_DIR / "latest-normalized-publication-payloads.json"

TARGET_FILES = [
    EXPORT_DIR / "latest-picks.json",
    EXPORT_DIR / "latest-bets.json",
    EXPORT_DIR / "latest-pending-bets.json",
    EXPORT_DIR / "latest-publication-status.json",
]

READY_STATUSES = {
    "movement_confirmed",
    "publish_now_no_next_cron",
    "movement_rechecked_across_cron_windows",
    "telegram_sent",
    "published",
    "sent",
}

SENT_STATUSES = {"telegram_sent", "published", "sent", "pending", "open", "active"}
NOT_SENT_STATUSES = {"generated", "generated_not_sent", "send_failed", "blocked", "dry_run_selected"}


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def truthy(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in {"1", "true", "yes", "on", "ok", "sent"}


def nested_dict(row: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        value = row.get(name)
        if isinstance(value, dict):
            return value
    return {}


def is_sent(row: dict[str, Any]) -> bool:
    summary = nested_dict(row, "source_summary")
    diag = nested_dict(row, "diagnostics")
    lifecycle = str(
        row.get("publication_lifecycle_status")
        or row.get("publication_lifecycle_stage")
        or summary.get("publication_lifecycle_status")
        or summary.get("publication_lifecycle_stage")
        or nested_dict(diag, "publication_lifecycle").get("status")
        or ""
    ).strip().lower()
    if lifecycle in NOT_SENT_STATUSES:
        return False
    if lifecycle in SENT_STATUSES:
        return True
    for value in (row.get("telegram_sent"), summary.get("telegram_sent"), diag.get("telegram_sent")):
        if value is not None:
            return truthy(value)
    return str(row.get("status") or "").strip().lower() in SENT_STATUSES


def movement_status_from(row: dict[str, Any]) -> str:
    summary = nested_dict(row, "source_summary")
    diag = nested_dict(row, "diagnostics")
    guard = nested_dict(row, "line_movement_guard") or nested_dict(diag, "line_movement_guard")
    movement = nested_dict(summary, "movement") or nested_dict(diag, "movement")
    windowed = nested_dict(summary, "windowed_core_coverage") or nested_dict(diag, "windowed_core_coverage")
    windowed_movement = nested_dict(windowed, "movement")

    for source in (guard, movement, windowed_movement, summary, diag, row):
        if not isinstance(source, dict):
            continue
        raw = str(
            source.get("line_movement_lifecycle_status")
            or source.get("market_move")
            or source.get("market_movement")
            or source.get("forecast_market_movement")
            or source.get("status")
            or ""
        ).strip().lower()
        if raw in READY_STATUSES or raw in {"awaiting_next_run", "movement_failed", "not_publishable"}:
            return raw

    snapshot_count = 0
    for source in (windowed_movement, movement, guard):
        try:
            snapshot_count = max(snapshot_count, int(float(str(source.get("snapshot_count") or source.get("snapshots") or 0))))
        except Exception:
            pass
    if snapshot_count >= 2:
        return "movement_confirmed"
    if truthy(guard.get("no_more_cron_before_kickoff") or guard.get("no_more_regular_run_before_kickoff")):
        return "publish_now_no_next_cron"
    return ""


def clean_reason_list(value: Any) -> tuple[list[Any], int]:
    if not isinstance(value, list):
        return value if isinstance(value, list) else [], 0
    clean = [item for item in value if "insufficient_history" not in str(item).lower()]
    return clean, len(value) - len(clean)


def normalize_row(row: dict[str, Any]) -> int:
    changed = 0
    summary = row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {}
    diag = row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else {}
    sent = is_sent(row)
    status = movement_status_from(row)
    ready = status in {"movement_confirmed", "publish_now_no_next_cron", "movement_rechecked_across_cron_windows"}

    if sent:
        for obj in (row, summary):
            if obj.get("telegram_sent") is not True:
                obj["telegram_sent"] = True
                changed += 1
            if obj.get("publication_lifecycle_status") not in {"telegram_sent", "published"}:
                obj["publication_lifecycle_status"] = "telegram_sent"
                changed += 1
            obj["publication_lifecycle_stage"] = "telegram_sent"
        row["status"] = "pending"

    if ready or (sent and status):
        movement_label = "movement_confirmed" if status == "movement_rechecked_across_cron_windows" else (status or "movement_confirmed")
        if movement_label in {"telegram_sent", "published", "sent"}:
            movement_label = "movement_confirmed"
        for obj in (row, summary, diag):
            obj["line_movement_lifecycle_status"] = movement_label
            obj["market_move"] = movement_label
            obj["market_movement"] = movement_label
            obj["forecast_market_movement"] = movement_label
        for key in ("reasons", "reject_reasons"):
            clean, removed = clean_reason_list(row.get(key))
            if removed:
                row[key] = clean
                changed += removed
        for container in (summary, diag):
            for key in ("reasons", "reject_reasons"):
                if isinstance(container.get(key), list):
                    clean, removed = clean_reason_list(container.get(key))
                    if removed:
                        container[key] = clean
                        changed += removed

    row["source_summary"] = summary
    if diag:
        row["diagnostics"] = diag
    return changed


def walk_and_normalize(value: Any) -> tuple[Any, int, int]:
    changed = 0
    rows = 0
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                rows += 1
                changed += normalize_row(item)
        return value, rows, changed
    if isinstance(value, dict):
        for key in ("picks", "bets", "pending", "published", "published_candidates", "selected", "candidates", "data", "rows"):
            if isinstance(value.get(key), list):
                _, r, c = walk_and_normalize(value[key])
                rows += r
                changed += c
        if any(k in value for k in ("match_key", "home_team", "away_team", "selection", "telegram_sent", "source_summary")):
            rows += 1
            changed += normalize_row(value)
        return value, rows, changed
    return value, rows, changed


def run_post_quality_reports(report: dict[str, Any]) -> None:
    for name in (
        "repair_bzzoiro_v2_report_metrics",
        "sync_prediction_calibration_ledger",
        "build_line_decision_cards",
        "build_live_source_quorum_report",
        "build_publication_readiness_report",
        "build_two_plus_coverage_report",
    ):
        try:
            module = __import__(f"scripts.{name}", fromlist=["main"])
            main_func = getattr(module, "main", None)
            if callable(main_func):
                main_func()
                report.setdefault("post_quality_reports", []).append({"script": name, "status": "ok"})
        except Exception as exc:
            report.setdefault("post_quality_reports", []).append({"script": name, "status": "error", "error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    report: dict[str, Any] = {
        "status": "ok",
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "files": [],
        "total_rows_seen": 0,
        "total_changes": 0,
    }
    for path in TARGET_FILES:
        payload = load_json(path, None)
        if payload is None:
            continue
        payload, rows, changes = walk_and_normalize(payload)
        if changes:
            write_json(path, payload)
        report["files"].append({"path": str(path), "rows_seen": rows, "changes": changes})
        report["total_rows_seen"] += rows
        report["total_changes"] += changes
    run_post_quality_reports(report)
    write_json(OUT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
