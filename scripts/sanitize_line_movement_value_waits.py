from __future__ import annotations

"""Remove false awaiting-next-cron labels from non-value candidates.

A candidate must not be stored as awaiting the next line snapshot when the same
line-guard result already says current EV/edge/odds failed.  Waiting for cron is
only valid for otherwise-clean candidates that need movement history.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
EXPORT = ROOT / ".data" / "exports"
LINE_REPORT = EXPORT / "latest-line-movement-guard-report.json"
AWAITING_REPORT = EXPORT / "latest-awaiting-movement-candidates.json"
STATE = ROOT / ".data" / "candidate-lifecycle-state.json"
OUT = EXPORT / "latest-line-movement-value-wait-sanitizer.json"
UTC = timezone.utc

AWAIT_REASON = "needs_next_cron_line_movement_recheck"
VALUE_FAIL_PREFIXES = (
    "current_ev_below_floor",
    "current_edge_below_floor",
    "missing_current_odds",
    "line_moved_against_candidate",
)


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def point(value: Any) -> str:
    try:
        f = float(str(value).replace(",", "."))
        if f >= 10 and f % 5 == 0:
            f = f / 10.0
        return str(int(f)) if f.is_integer() else f"{f:g}"
    except Exception:
        return norm(value)


def row_sig(row: dict[str, Any]) -> str:
    match = norm(row.get("match_key") or row.get("canonical_match_id") or row.get("event_key"))
    family = norm(row.get("family") or row.get("market_family"))
    selection = norm(row.get("selection_key") or row.get("selection"))
    if "under" in selection or "меньше" in selection or "тм" in selection:
        selection = "under"
    elif "over" in selection or "больше" in selection or "тб" in selection:
        selection = "over"
    return "|".join([match, family, selection, point(row.get("point") or row.get("line") or row.get("handicap"))])


def guard_has_false_wait(guard: dict[str, Any]) -> bool:
    reasons = [str(x) for x in guard.get("reasons") or []]
    has_wait = any(AWAIT_REASON in reason for reason in reasons)
    has_value_fail = any(any(reason.startswith(prefix) for prefix in VALUE_FAIL_PREFIXES) for reason in reasons)
    return bool(has_wait and has_value_fail)


def sanitize_guard(guard: dict[str, Any]) -> bool:
    if not guard_has_false_wait(guard):
        return False
    reasons = [str(x) for x in guard.get("reasons") or []]
    guard["reasons"] = [reason for reason in reasons if AWAIT_REASON not in reason]
    guard["line_movement_lifecycle_status"] = "not_publishable_value_guard"
    guard["awaiting_next_cron_suppressed"] = True
    guard["awaiting_next_cron_suppressed_reason"] = "candidate_already_failed_current_value_or_price_guard"
    guard["passed"] = False
    return True


def main() -> int:
    changed_guards = 0
    affected: set[str] = set()

    line_report = load_json(LINE_REPORT, {})
    if isinstance(line_report, dict):
        for file_item in line_report.get("files") or []:
            if not isinstance(file_item, dict):
                continue
            for dropped in file_item.get("dropped_sample") or []:
                if not isinstance(dropped, dict):
                    continue
                guard = dropped.get("guard") if isinstance(dropped.get("guard"), dict) else {}
                if sanitize_guard(guard):
                    changed_guards += 1
                    affected.add(row_sig(dropped))
        if changed_guards:
            line_report["value_failed_awaiting_suppressed"] = changed_guards
            line_report["updated_at_utc"] = datetime.now(UTC).isoformat()
            write_json(LINE_REPORT, line_report)

    state = load_json(STATE, {})
    removed_state = 0
    if isinstance(state, dict):
        rows = state.get("awaiting_movement_candidates") if isinstance(state.get("awaiting_movement_candidates"), list) else []
        kept = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            guard = row.get("line_movement_guard") if isinstance(row.get("line_movement_guard"), dict) else (row.get("diagnostics") or {}).get("line_movement_guard") if isinstance(row.get("diagnostics"), dict) else {}
            sig = row.get("lifecycle_signature") or row_sig(row)
            if guard_has_false_wait(guard if isinstance(guard, dict) else {}) or sig in affected:
                removed_state += 1
                continue
            kept.append(row)
        if removed_state:
            state["awaiting_movement_candidates"] = kept
            state["awaiting_movement_updated_at_utc"] = datetime.now(UTC).isoformat()
            state["awaiting_movement_value_failed_removed"] = removed_state
            write_json(STATE, state)

    awaiting_report = load_json(AWAITING_REPORT, {})
    removed_report = 0
    if isinstance(awaiting_report, dict):
        rows = awaiting_report.get("sample") if isinstance(awaiting_report.get("sample"), list) else []
        kept_sample = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            guard = row.get("line_movement_guard") if isinstance(row.get("line_movement_guard"), dict) else {}
            sig = row.get("lifecycle_signature") or row_sig(row)
            if guard_has_false_wait(guard) or sig in affected:
                removed_report += 1
                continue
            kept_sample.append(row)
        if removed_report:
            awaiting_report["sample"] = kept_sample
            awaiting_report["awaiting_total"] = max(0, int(awaiting_report.get("awaiting_total") or 0) - removed_report)
            awaiting_report["removed_value_failed_awaiting"] = removed_report
            write_json(AWAITING_REPORT, awaiting_report)

    payload = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "changed_line_guard_rows": changed_guards,
        "removed_state_rows": removed_state,
        "removed_awaiting_report_rows": removed_report,
        "affected_signatures": sorted(affected)[:20],
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
