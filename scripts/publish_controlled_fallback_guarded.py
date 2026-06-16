from __future__ import annotations

"""Guarded entrypoint for controlled fallback publication.

The normal model pipeline already has a windowed coverage/movement publication
filter. Live runs showed that the controlled fallback script could still publish
the same candidate after the main publish filter rejected it with
`needs_next_cron_line_movement_recheck`. It also wrote the fallback sent-index to
.data/fallback-sent-index.json, but that file was not committed, so the same
match/market could be sent again in the next run.

This wrapper keeps the original controlled-fallback evaluator but adds two hard
prepublish guards:
1. respect latest-windowed-core-publication-filter movement blocks;
2. dedupe against previous controlled-fallback reports and sent-index.
"""

import importlib.util
import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = Path(__file__).resolve().with_name("publish_controlled_fallback.py")
REPORT_PATH = ROOT / ".data" / "exports" / "latest-controlled-fallback-prepublish-guard.json"

_GUARD_EVENTS: list[dict[str, Any]] = []
MOVEMENT_READY_STATUSES = {"movement_confirmed", "movement_rechecked_across_cron_windows", "publish_now_no_next_cron", "movement_ready"}


def _load_base_module() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_publish_controlled_fallback_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_base_module()


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: str | Path, payload: Any) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _point(value: Any) -> str:
    if value in (None, "", "null"):
        return ""
    try:
        f = float(str(value).replace(",", "."))
        return str(int(f)) if f.is_integer() else f"{f:g}"
    except Exception:
        return _norm(value)


def _parse_dt(value: Any) -> datetime | None:
    try:
        if value in (None, ""):
            return None
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _canonical_selection(row: dict[str, Any]) -> str:
    explicit = _norm(row.get("selection_key"))
    family = _norm(row.get("family") or row.get("market_family"))
    selection = str(row.get("selection") or "").strip().casefold().replace("ё", "е")
    if explicit in {"under", "over", "home", "away", "draw"}:
        return explicit
    if family in {"totals", "teamtotals"}:
        if any(token in selection for token in ("under", "меньше", "тотал меньше", "тм")):
            return "under"
        if any(token in selection for token in ("over", "больше", "тотал больше", "тб")):
            return "over"
    return explicit or _norm(selection)


def _candidate_signature(row: dict[str, Any]) -> dict[str, str]:
    return {
        "match_key": _norm(row.get("canonical_match_id") or row.get("match_key") or row.get("event_key")),
        "family": _norm(row.get("family") or row.get("market_family")),
        "selection": _canonical_selection(row),
        "point": _point(row.get("point") or row.get("line") or row.get("handicap")),
        "home": _norm(row.get("home_team") or row.get("home")),
        "away": _norm(row.get("away_team") or row.get("away")),
    }


def _same_candidate(candidate: dict[str, Any], row: dict[str, Any]) -> bool:
    cand = _candidate_signature(candidate)
    other = _candidate_signature(row)
    if cand["match_key"] and other["match_key"] and cand["match_key"] != other["match_key"]:
        return False
    if cand["family"] and other["family"] and cand["family"] != other["family"]:
        return False
    if cand["selection"] and other["selection"] and cand["selection"] != other["selection"]:
        return False
    if cand["point"] and other["point"] and cand["point"] != other["point"]:
        return False
    if not cand["match_key"] or not other["match_key"]:
        if cand["home"] and other["home"] and cand["home"] != other["home"]:
            return False
        if cand["away"] and other["away"] and cand["away"] != other["away"]:
            return False
    return True


def _row_from_windowed_block(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    if "family" not in row and isinstance(coverage, dict):
        row["family"] = coverage.get("family")
    return row


def _candidate_movement_confirmed(candidate: dict[str, Any]) -> bool:
    guards = [
        candidate.get("line_movement_guard"),
        candidate.get("line_movement"),
        (candidate.get("diagnostics") or {}).get("line_movement_guard") if isinstance(candidate.get("diagnostics"), dict) else None,
    ]
    for guard in guards:
        if not isinstance(guard, dict):
            continue
        status = str(guard.get("status") or guard.get("line_movement_lifecycle_status") or "").strip()
        if status in MOVEMENT_READY_STATUSES and bool(guard.get("passed", True)):
            return True
    source_summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    for key in ("publication_lifecycle_status", "line_movement_lifecycle_status", "movement_status"):
        if str(source_summary.get(key) or candidate.get(key) or "").strip() in MOVEMENT_READY_STATUSES:
            return True
    return False


def controlled_line_movement_report_guarded(candidate: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    if _candidate_movement_confirmed(candidate):
        report = {"passed": True, "status": "movement_confirmed", "reasons": []}
        metrics["line_movement"] = report
        return report
    report = candidate.get("line_movement_guard") if isinstance(candidate.get("line_movement_guard"), dict) else {}
    metrics["line_movement"] = report
    return report


def _windowed_movement_reasons(candidate: dict[str, Any]) -> list[str]:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_RESPECT_WINDOWED_MOVEMENT_GUARD"), True):
        return []
    if _candidate_movement_confirmed(candidate):
        return []
    payload = _load_json(ROOT / ".data" / "exports" / "latest-windowed-core-publication-filter.json", {})
    blocked = payload.get("blocked_sample") if isinstance(payload, dict) else []
    if not isinstance(blocked, list):
        return []
    for item in blocked:
        if not isinstance(item, dict):
            continue
        row = _row_from_windowed_block(item)
        if not _same_candidate(candidate, row):
            continue
        coverage = item.get("coverage") if isinstance(item.get("coverage"), dict) else {}
        reject_reasons = list(coverage.get("reject_reasons") or item.get("reject_reasons") or [])
        movement = coverage.get("movement") if isinstance(coverage.get("movement"), dict) else {}
        out: list[str] = []
        if "needs_next_cron_line_movement_recheck" in reject_reasons or movement.get("reason") == "needs_next_cron_line_movement_recheck":
            out.append("controlled_fallback_windowed_line_movement_recheck_required")
        elif reject_reasons and _truthy(os.getenv("CONTROLLED_FALLBACK_RESPECT_ALL_WINDOWED_BLOCKS"), True):
            out.extend(f"controlled_fallback_windowed_block:{reason}" for reason in reject_reasons[:3])
        if out:
            _GUARD_EVENTS.append({
                "guard": "windowed_publication_filter",
                "match_key": candidate.get("match_key"),
                "home_team": candidate.get("home_team"),
                "away_team": candidate.get("away_team"),
                "family": candidate.get("family"),
                "selection": candidate.get("selection"),
                "point": candidate.get("point"),
                "reasons": out,
                "windowed_reject_reasons": reject_reasons,
                "movement": movement,
            })
        return out
    return []


def _duplicate_previous_report_reason(candidate: dict[str, Any]) -> str | None:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_DEDUPE_PREVIOUS_REPORT"), True):
        return None
    report = _load_json(ROOT / ".data" / "exports" / "latest-controlled-fallback-report.json", {})
    if not isinstance(report, dict) or not report.get("published"):
        return None
    rows = report.get("selected_all") or ([report.get("selected")] if isinstance(report.get("selected"), dict) else [])
    if not isinstance(rows, list):
        return None
    max_hours = int(float(os.getenv("CONTROLLED_FALLBACK_PREVIOUS_REPORT_DEDUPE_HOURS") or 72))
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, max_hours))
    for row in rows:
        if not isinstance(row, dict):
            continue
        kickoff = _parse_dt(row.get("commence_time") or row.get("kickoff"))
        if kickoff is not None and kickoff < datetime.now(UTC):
            continue
        sent_at = _parse_dt(report.get("created_at") or row.get("sent_at"))
        if sent_at is not None and sent_at < cutoff:
            continue
        if _same_candidate(candidate, row):
            return "duplicate_previous_controlled_fallback_report"
    return None


def _duplicate_sent_index_reason(candidate: dict[str, Any]) -> str | None:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_DEDUPE_SENT_INDEX_STRICT"), True):
        return None
    payload = _load_json(ROOT / ".data" / "fallback-sent-index.json", {})
    if not isinstance(payload, dict):
        return None
    for row in payload.values():
        if not isinstance(row, dict):
            continue
        kickoff = _parse_dt(row.get("commence_time") or row.get("kickoff"))
        if kickoff is not None and kickoff < datetime.now(UTC):
            continue
        if _same_candidate(candidate, row):
            return "duplicate_persisted_fallback_sent_index"
    return None

def _strict_duplicate_reason(candidate: dict[str, Any]) -> str | None:
    """Do not resend the same match/market/side/line until kickoff/settlement.

    The base fallback index used localized selection text in the hash, so the same
    Under 3.5 could be sent again when the row was regenerated as promotion data
    or when the bookmaker price changed.
    """
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_STRICT_MATCH_MARKET_DEDUPE"), True):
        return None
    places = [
        ROOT / ".data" / "fallback-sent-index.json",
        ROOT / ".data" / "published-candidate-index.json",
        ROOT / ".data" / "state.json",
        ROOT / ".data" / "exports" / "latest-controlled-fallback-report.json",
        ROOT / "artifacts" / "controlled-fallback-report.json",
    ]
    rows: list[dict[str, Any]] = []
    for path in places:
        payload = _load_json(path, {})
        if isinstance(payload, dict):
            rows.extend([v for v in payload.values() if isinstance(v, dict)])
            for key in ("selected_all", "published", "bets", "published_candidates", "items", "rows"):
                val = payload.get(key)
                if isinstance(val, list):
                    rows.extend([x for x in val if isinstance(x, dict)])
                elif isinstance(val, dict):
                    rows.append(val)
            if isinstance(payload.get("selected"), dict):
                rows.append(payload["selected"])
        elif isinstance(payload, list):
            rows.extend([x for x in payload if isinstance(x, dict)])
    now = datetime.now(UTC)
    cand_sig = _candidate_signature(candidate)
    for row in rows:
        # Keep active/upcoming rows only.  Unknown kickoff rows are still deduped for the safety window.
        kickoff = _parse_dt(row.get("commence_time") or row.get("kickoff") or row.get("start_time"))
        sent_at = _parse_dt(row.get("sent_at") or row.get("created_at") or row.get("published_at"))
        if kickoff is not None and kickoff < now:
            continue
        if kickoff is None and sent_at is not None and (now - sent_at).total_seconds() > 36 * 3600:
            continue
        if _same_candidate(candidate, row):
            return "duplicate_match_market_selection_line"
        other_sig = _candidate_signature(row)
        if cand_sig["match_key"] and other_sig["match_key"] and cand_sig["match_key"] == other_sig["match_key"]:
            if cand_sig["family"] == other_sig["family"] and cand_sig["selection"] == other_sig["selection"] and cand_sig["point"] == other_sig["point"]:
                return "duplicate_match_market_selection_line"
    return None


def _cron_local_tz() -> Any:
    for name in (os.getenv("LINE_MOVEMENT_CRON_TIMEZONE"), os.getenv("APP_TIMEZONE"), os.getenv("TZ"), "Europe/Moscow"):
        try:
            return ZoneInfo(str(name))
        except Exception:
            continue
    return UTC


def _next_scheduled_run_at(now: datetime, interval_min: int) -> datetime | None:
    if interval_min <= 0:
        return None
    local_tz = _cron_local_tz()
    now_local = now.astimezone(local_tz)
    anchor_minute = int(float(os.getenv("LINE_MOVEMENT_CRON_ANCHOR_MINUTE") or os.getenv("CONTROLLED_FALLBACK_CRON_ANCHOR_MINUTE") or 0))
    anchor_minute = max(0, min(anchor_minute, 1439))
    anchor = now_local.replace(hour=0, minute=0, second=0, microsecond=0).replace(hour=anchor_minute // 60, minute=anchor_minute % 60)
    while anchor <= now_local:
        anchor += timedelta(minutes=interval_min)
    return anchor.astimezone(UTC)


def _line_state_has_previous_recheck(candidate: dict[str, Any], now: datetime) -> bool:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_REQUIRE_LINE_RECHECK"), True):
        return True
    try:
        import importlib
        lm = importlib.import_module("app.services.line_movement_state")
        key = lm._line_key(candidate)  # type: ignore[attr-defined]
    except Exception:
        key = ""
    kickoff = _parse_dt(candidate.get("commence_time") or candidate.get("kickoff") or candidate.get("start_time"))
    day = (kickoff or now).date().isoformat()
    paths = [ROOT / ".data" / "line_history" / f"{day}.json", ROOT / ".data" / "line_history" / "latest.json"]
    min_recheck = float(os.getenv("CONTROLLED_FALLBACK_MIN_RECHECK_MINUTES") or os.getenv("LINE_MOVEMENT_MIN_RECHECK_MINUTES") or 60.0)
    current_run_id = os.getenv("GITHUB_RUN_ID") or os.getenv("HARIZON_RUN_ID") or ""
    for path in paths:
        payload = _load_json(path, {})
        lines = payload.get("lines") if isinstance(payload, dict) else {}
        if not isinstance(lines, dict):
            continue
        entries = []
        if key and isinstance(lines.get(key), dict):
            entries.append(lines.get(key))
        else:
            # Fall back to signature comparison by scanning entries.
            entries.extend([v for v in lines.values() if isinstance(v, dict)])
        for entry in entries:
            snaps = entry.get("snapshots") if isinstance(entry, dict) else []
            if not isinstance(snaps, list):
                continue
            for snap in snaps:
                if not isinstance(snap, dict):
                    continue
                captured = _parse_dt(snap.get("captured_at_utc"))
                if not captured:
                    continue
                if current_run_id and str(snap.get("run_id") or "") == current_run_id:
                    continue
                if (now - captured).total_seconds() / 60.0 >= min_recheck:
                    return True
    # Already annotated candidates from the main pipeline can pass.
    return _candidate_movement_confirmed(candidate)


def _final_cron_recheck_reasons(candidate: dict[str, Any]) -> list[str]:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_REQUIRE_FINAL_CRON_RECHECK"), True):
        return []
    kickoff = _parse_dt(candidate.get("commence_time") or candidate.get("kickoff") or candidate.get("start_time"))
    if kickoff is None:
        return ["controlled_fallback_missing_kickoff_for_final_recheck"]
    now = datetime.now(UTC)
    min_lead = int(float(os.getenv("LINE_MOVEMENT_MIN_LEAD_MINUTES") or os.getenv("MIN_KICKOFF_LEAD_MINUTES") or 15))
    interval = int(float(os.getenv("CRON_EXPECTED_INTERVAL_MINUTES") or os.getenv("LINE_MOVEMENT_CRON_INTERVAL_MINUTES") or 120))
    next_run = _next_scheduled_run_at(now, interval)
    latest_useful = kickoff - timedelta(minutes=max(0, min_lead))
    has_next_regular_run = bool(next_run is not None and next_run <= latest_useful)
    has_previous_recheck = _line_state_has_previous_recheck(candidate, now)
    reasons: list[str] = []
    if has_next_regular_run and not has_previous_recheck:
        reasons.append("controlled_fallback_next_regular_run_before_kickoff")
        reasons.append("controlled_fallback_missing_line_recheck")
    elif not has_next_regular_run:
        # Final window: no regular cron remains before kickoff, so the current
        # run is allowed to be the last movement/value check.
        has_previous_recheck = True
    if not has_previous_recheck and "controlled_fallback_missing_line_recheck" not in reasons:
        reasons.append("controlled_fallback_missing_line_recheck")
    if reasons:
        _GUARD_EVENTS.append({
            "guard": "final_cron_recheck",
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "kickoff_utc": kickoff.isoformat(),
            "next_regular_run_at_utc": next_run.isoformat() if next_run else None,
            "latest_useful_run_at_utc": latest_useful.isoformat(),
            "reasons": reasons,
        })
    return reasons



_original_hard_reject_reasons = base.hard_reject_reasons


def hard_reject_reasons_guarded(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
    reasons = list(_original_hard_reject_reasons(candidate, metrics, sent_index) or [])
    extra = []
    duplicate = _strict_duplicate_reason(candidate) or _duplicate_sent_index_reason(candidate) or _duplicate_previous_report_reason(candidate)
    if duplicate:
        extra.append(duplicate)
    extra.extend(_final_cron_recheck_reasons(candidate))
    extra.extend(_windowed_movement_reasons(candidate))
    if extra:
        _GUARD_EVENTS.append({
            "guard": "controlled_fallback_prepublish",
            "match_key": candidate.get("match_key"),
            "home_team": candidate.get("home_team"),
            "away_team": candidate.get("away_team"),
            "family": candidate.get("family"),
            "selection": candidate.get("selection"),
            "point": candidate.get("point"),
            "reasons": extra,
        })
    return reasons + extra


base.hard_reject_reasons = hard_reject_reasons_guarded


def main() -> int:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "starting",
        "windowed_filter_path": str(ROOT / ".data" / "exports" / "latest-windowed-core-publication-filter.json"),
        "events": [],
    }
    try:
        code = int(base.main() or 0)
        payload["status"] = "ok" if code == 0 else "base_returned_nonzero"
        payload["base_exit_code"] = code
        return code
    except SystemExit as exc:
        code = int(exc.code or 0) if isinstance(exc.code, int) else 1
        payload["status"] = "system_exit"
        payload["base_exit_code"] = code
        return code
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        payload["events"] = _GUARD_EVENTS[:100]
        payload["blocked_events"] = len(_GUARD_EVENTS)
        _write_json(REPORT_PATH, payload)


if __name__ == "__main__":
    raise SystemExit(main())
