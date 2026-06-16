from __future__ import annotations

"""Update daily inventory priority and protect publication from stale/value-losing lines.

This script is intended to run after ``run-once`` and before
``publish_controlled_fallback.py``. It does three things:

1. Recomputes a kickoff-aware refresh plan for every match in the daily
   inventory.
2. Records candidate odds snapshots into ``.data/line_history``.
3. Mutates candidate export files to drop candidates whose current line moved
   against the bet or whose current EV/edge is below the configured floor.

The guard is intentionally conservative: it only removes/blocks a candidate
when it can prove the current line is stale, negative-moving, or no longer
valuable. It never creates a pick.
"""

import json
import os
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.day_inventory_aliases import write_current_aliases

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
LINE_HISTORY_DIR = ROOT / ".data" / "line_history"
OUT_PATH = EXPORT_DIR / "latest-day-inventory-priority-and-line-state.json"
REFRESH_PLAN_PATH = EXPORT_DIR / "latest-day-inventory-refresh-plan.json"
LINE_GUARD_REPORT_PATH = EXPORT_DIR / "latest-line-movement-guard-report.json"


LOW_QUALITY_INVENTORY_PATTERNS = [
    r"\bunknown\b", r"\bu[- ]?17\b", r"\bu[- ]?18\b", r"\bu[- ]?19\b", r"\bu[- ]?20\b", r"\bu[- ]?21\b", r"\bu[- ]?23\b",
    r"\bunder[- ]?17\b", r"\bunder[- ]?18\b", r"\bunder[- ]?19\b", r"\bunder[- ]?20\b", r"\bunder[- ]?21\b", r"\bunder[- ]?23\b",
    r"\breserves?\b", r"\breserve team\b", r"\byouth\b", r"\bacademy\b", r"\bdevelopment\b",
    r"\bwomen(?:s)?\b", r"\bamateur\b", r"\bregional\b", r"\brussia\s*-?\s*2\.?\s*liga\b",
    r"\bii\b", r"\biii\b", r"\b2nd\b", r"\bsecond team\b", r"\bb team\b",
    r"\bvtoraya\s+liga\b", r"\bsecond\s+league\b", r"\bthird\s+league\b",
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



def low_quality_inventory_reason(row: dict[str, Any]) -> str | None:
    haystack = " ".join([
        str(row.get("league_name") or row.get("league") or ""),
        str(row.get("home_team") or row.get("home") or ""),
        str(row.get("away_team") or row.get("away") or ""),
        str(row.get("tournament") or row.get("competition") or ""),
    ]).lower()
    if not haystack.strip():
        return "empty_match_text"
    if is_low_quality_team_name(row.get("home_team") or row.get("home")) or is_low_quality_team_name(row.get("away_team") or row.get("away")):
        return "reserve_or_second_team_name"
    for pattern in LOW_QUALITY_INVENTORY_PATTERNS:
        if re.search(pattern, haystack):
            return pattern
    return None

CANDIDATE_PATHS = [
    EXPORT_DIR / "latest-rescue-candidates.json",
    EXPORT_DIR / "latest-candidates-before-quality.json",
    EXPORT_DIR / "latest-candidates-after-quality.json",
    EXPORT_DIR / "latest-candidates.json",
]

# These diagnostic files are not publication queues, but they contain candidates
# that were blocked by coverage/value/movement guards before controlled fallback
# created a candidate file.  We still persist their first line snapshot so the
# next 2-hour run can do a real movement recheck instead of reporting
# candidates_seen=0.
WATCHLIST_SOURCE_PATHS = [
    EXPORT_DIR / "latest-candidate-value-runtime-patch.json",
    EXPORT_DIR / "latest-windowed-core-candidate-audit.json",
    EXPORT_DIR / "latest-candidate-factory-output-dedup.json",
]


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def env_int(name: str, default: int) -> int:
    try:
        raw = env(name)
        return int(float(raw)) if raw else default
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        raw = env(name)
        return float(raw) if raw else default
    except Exception:
        return default


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(env("APP_TIMEZONE") or env("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def next_scheduled_run_at(now: datetime, interval_min: int) -> datetime | None:
    if interval_min <= 0:
        return None
    local_now = now.astimezone(app_tz())
    anchor_minute = max(0, min(env_int("LINE_MOVEMENT_CRON_ANCHOR_MINUTE", 0), 1439))
    anchor = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    anchor = anchor.replace(hour=anchor_minute // 60, minute=anchor_minute % 60)
    while anchor <= local_now:
        anchor += timedelta(minutes=interval_min)
    return anchor.astimezone(UTC)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def now_utc_from_debug() -> datetime:
    for name in ("HARIZON_RUN_NOW_UTC", "RUN_NOW_UTC", "CURRENT_TIME_UTC"):
        dt = parse_dt(env(name))
        if dt is not None:
            return dt
    debug = load_json(ROOT / ".logs" / "debug-last-run.json", {})
    for value in (
        (debug.get("summary") or {}).get("current_time_utc") if isinstance(debug.get("summary"), dict) else None,
        debug.get("current_time_utc") if isinstance(debug, dict) else None,
    ):
        dt = parse_dt(value)
        if dt is not None:
            return dt
    return datetime.now(UTC)


def target_date(now: datetime) -> str:
    explicit = env("DAY_INVENTORY_TARGET_DATE")
    if explicit:
        return explicit
    return now.astimezone(app_tz()).date().isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def norm_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "_", str(value or "").lower()).strip("_")


def candidate_key(candidate: dict[str, Any]) -> str:
    match_key = str(candidate.get("match_key") or candidate.get("canonical_match_id") or "").strip().lower()
    family = str(candidate.get("family") or "").strip().lower()
    selection_key = str(candidate.get("selection_key") or candidate.get("selection") or "").strip().lower()
    point = candidate.get("point")
    bookmaker = str(candidate.get("bookmaker") or (candidate.get("source_summary") or {}).get("selected_bookmaker") or "").strip().lower()
    return "|".join([match_key, family, selection_key, str(point or ""), bookmaker])


def candidate_match_key(candidate: dict[str, Any]) -> str:
    return str(candidate.get("match_key") or candidate.get("canonical_match_id") or "").strip()


def candidate_kickoff(candidate: dict[str, Any]) -> datetime | None:
    for key in ("commence_time", "kickoff_utc", "start_time", "kickoff"):
        dt = parse_dt(candidate.get(key))
        if dt:
            return dt
    return None


def candidate_odds(candidate: dict[str, Any]) -> float:
    for key in ("odds", "price", "selected_odds"):
        value = safe_float(candidate.get(key), 0.0)
        if value > 1.0:
            return value
    bucket = candidate.get("raw_bucket_offers")
    if isinstance(bucket, list):
        prices = [safe_float(row.get("price"), 0.0) for row in bucket if isinstance(row, dict)]
        prices = [p for p in prices if p > 1.0]
        if prices:
            return max(prices)
    return 0.0


def candidate_metric(candidate: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = safe_float(candidate.get(key), None)  # type: ignore[arg-type]
        if value is not None:
            return float(value)
    metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
    diagnostics = candidate.get("diagnostics") if isinstance(candidate.get("diagnostics"), dict) else {}
    for src in (metrics, diagnostics):
        for key in keys:
            if isinstance(src, dict) and key in src:
                value = safe_float(src.get(key), None)  # type: ignore[arg-type]
                if value is not None:
                    return float(value)
    return default


def history_path_for_date(local_date: str) -> Path:
    return LINE_HISTORY_DIR / f"{local_date}.json"


def load_line_history(local_date: str) -> dict[str, Any]:
    payload = load_json(history_path_for_date(local_date), {})
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("date_local", local_date)
    payload.setdefault("updated_at_utc", datetime.now(UTC).isoformat())
    payload.setdefault("lines", {})
    if not isinstance(payload.get("lines"), dict):
        payload["lines"] = {}
    return payload


def snapshot_from_candidate(candidate: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "captured_at_utc": now.isoformat(),
        "match_key": candidate_match_key(candidate),
        "kickoff_utc": candidate_kickoff(candidate).isoformat() if candidate_kickoff(candidate) else None,
        "family": candidate.get("family"),
        "selection": candidate.get("selection"),
        "selection_key": candidate.get("selection_key"),
        "point": candidate.get("point"),
        "bookmaker": candidate.get("bookmaker") or (candidate.get("source_summary") or {}).get("selected_bookmaker"),
        "source": (candidate.get("source_summary") or {}).get("selected_source"),
        "odds": candidate_odds(candidate),
        "ev_pct": candidate_metric(candidate, "ev_pct", "canonical_ev_pct", default=0.0),
        "edge_pp": candidate_metric(candidate, "edge_pct", "edge_pp", "canonical_edge_pp", default=0.0),
        "confidence": candidate_metric(candidate, "confidence", default=0.0),
        "raw_bucket_offers": candidate.get("raw_bucket_offers") if isinstance(candidate.get("raw_bucket_offers"), list) else [],
    }


def line_guard(candidate: dict[str, Any], previous: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    current_odds = candidate_odds(candidate)
    ev_pct = candidate_metric(candidate, "ev_pct", "canonical_ev_pct", default=0.0)
    edge_pp = candidate_metric(candidate, "edge_pct", "edge_pp", "canonical_edge_pp", default=0.0)
    kickoff = candidate_kickoff(candidate)
    lead_min = ((kickoff - now).total_seconds() / 60.0) if kickoff else None
    next_run_min = env_int("CRON_EXPECTED_INTERVAL_MINUTES", 120)
    cron_interval_min = env_int("LINE_MOVEMENT_CRON_INTERVAL_MINUTES", next_run_min)
    min_lead = env_int("MIN_KICKOFF_LEAD_MINUTES", 30)
    final_window_min = env_int("FINAL_PRE_KICKOFF_REFRESH_WINDOW_MINUTES", next_run_min + min_lead)
    max_negative_move_pct = env_float("LINE_MOVEMENT_MAX_NEGATIVE_PRICE_MOVE_PCT", 8.0)
    min_ev = env_float("LINE_MOVEMENT_MIN_CURRENT_EV_PCT", 3.0)
    min_edge = env_float("LINE_MOVEMENT_MIN_CURRENT_EDGE_PP", 1.5)
    max_snapshot_age = env_int("FINAL_PRE_KICKOFF_MAX_LINE_AGE_MINUTES", 18)

    reasons: list[str] = []
    passed = True
    previous_odds = safe_float(previous.get("odds") if previous else None, 0.0)
    previous_time = parse_dt(previous.get("captured_at_utc") if previous else None)
    move_pct = 0.0
    if previous_odds > 1.0 and current_odds > 1.0:
        move_pct = (current_odds - previous_odds) / previous_odds * 100.0
        if move_pct < -abs(max_negative_move_pct):
            passed = False
            reasons.append(f"line_moved_against_candidate:{move_pct:.1f}%")
    if current_odds <= 1.0:
        passed = False
        reasons.append("missing_current_odds")
    if ev_pct < min_ev:
        passed = False
        reasons.append(f"current_ev_below_floor:{ev_pct:.1f}<{min_ev:.1f}")
    if edge_pp < min_edge:
        passed = False
        reasons.append(f"current_edge_below_floor:{edge_pp:.1f}<{min_edge:.1f}")
    final_check = lead_min is not None and min_lead <= lead_min <= final_window_min
    next_run_at = next_scheduled_run_at(now, cron_interval_min) if env_bool("LINE_MOVEMENT_USE_SCHEDULED_CRON", True) else None
    latest_useful_run_at = kickoff - timedelta(minutes=min_lead) if kickoff else None
    has_next_regular_run = (
        bool(kickoff and next_run_at and latest_useful_run_at)
        and next_run_at <= latest_useful_run_at
    )
    if env_bool("LINE_MOVEMENT_USE_SCHEDULED_CRON", True) and kickoff is not None:
        no_more_cron_before_kickoff = not has_next_regular_run
    else:
        no_more_cron_before_kickoff = lead_min is not None and lead_min <= next_run_min + min_lead
    needs_next_cron_recheck = (
        previous is None
        and not no_more_cron_before_kickoff
        and env_bool("LINE_MOVEMENT_REQUIRE_NEXT_CRON_FOR_FUTURE_MATCHES", True)
    )
    if needs_next_cron_recheck:
        passed = False
        reasons.append("needs_next_cron_line_movement_recheck")
    if final_check:
        # The candidate came from the current run. If the run/artifact is stale,
        # block publication because this is the last realistic check before kick-off.
        candidate_ts = parse_dt(candidate.get("created_at_utc") or candidate.get("updated_at_utc")) or now
        age_min = abs((now - candidate_ts).total_seconds()) / 60.0
        if age_min > max_snapshot_age:
            passed = False
            reasons.append(f"final_pre_kickoff_line_too_old:{age_min:.1f}m")

    if needs_next_cron_recheck:
        lifecycle_status = "awaiting_next_run"
    elif previous is None and no_more_cron_before_kickoff and passed:
        lifecycle_status = "publish_now_no_next_cron"
    elif previous is not None and passed:
        lifecycle_status = "movement_confirmed"
    elif previous is not None:
        lifecycle_status = "movement_failed"
    else:
        lifecycle_status = "not_publishable"

    return {
        "passed": passed,
        "reasons": reasons,
        "line_movement_lifecycle_status": lifecycle_status,
        "current_odds": current_odds,
        "previous_odds": previous_odds or None,
        "line_move_pct": round(move_pct, 3),
        "current_ev_pct": ev_pct,
        "current_edge_pp": edge_pp,
        "lead_minutes": round(lead_min, 2) if lead_min is not None else None,
        "final_pre_kickoff_check": final_check,
        "no_more_cron_before_kickoff": no_more_cron_before_kickoff,
        "next_scheduled_run_at_utc": next_run_at.isoformat() if next_run_at else None,
        "latest_useful_run_at_utc": latest_useful_run_at.isoformat() if latest_useful_run_at else None,
        "has_next_regular_run_before_kickoff": has_next_regular_run,
        "cron_interval_minutes": cron_interval_min,
        "previous_snapshot_at_utc": previous_time.isoformat() if previous_time else None,
    }


def apply_line_movement_lifecycle(candidate: dict[str, Any], guard: dict[str, Any], snapshot_count: int) -> None:
    """Normalize all exported movement fields to one final lifecycle status.

    Older artifacts may keep strings like `market_move=insufficient_history` in
    source_summary/reasons even after the next run has confirmed movement.  The
    Telegram report and publication guards should read one coherent status.
    """
    status = str(guard.get("line_movement_lifecycle_status") or "not_publishable")
    movement_label = {
        "awaiting_next_run": "awaiting_next_run",
        "publish_now_no_next_cron": "publish_now_no_next_cron",
        "movement_confirmed": "movement_confirmed",
        "movement_failed": "movement_failed",
    }.get(status, status)
    candidate["line_movement_lifecycle_status"] = status
    candidate["market_move"] = movement_label
    candidate["forecast_market_movement"] = movement_label
    diagnostics = candidate.setdefault("diagnostics", {})
    if isinstance(diagnostics, dict):
        diagnostics["line_movement_guard"] = guard
        diagnostics["line_movement_lifecycle_status"] = status
        diagnostics["market_move"] = movement_label
        diagnostics["movement"] = {
            "status": status,
            "market_move": movement_label,
            "snapshot_count": int(snapshot_count or 0),
            "line_move_pct": guard.get("line_move_pct"),
            "previous_snapshot_at_utc": guard.get("previous_snapshot_at_utc"),
        }
    source_summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    source_summary["line_movement_lifecycle_status"] = status
    source_summary["market_move"] = movement_label
    source_summary["market_movement"] = movement_label
    source_summary["forecast_market_movement"] = movement_label
    if status == "awaiting_next_run":
        source_summary["publication_lifecycle_status"] = "awaiting_next_run"
        candidate["publication_lifecycle_status"] = "awaiting_next_run"
    elif status in {"movement_confirmed", "publish_now_no_next_cron"}:
        # Do not leave an old awaiting/generated marker when the last movement
        # check now allows the candidate to continue to publication guards.
        current_publication_status = str(candidate.get("publication_lifecycle_status") or "")
        if current_publication_status in {"", "awaiting_next_run", "generated_not_sent"}:
            candidate["publication_lifecycle_status"] = "movement_ready"
        if source_summary.get("publication_lifecycle_status") in (None, "", "awaiting_next_run", "generated_not_sent"):
            source_summary["publication_lifecycle_status"] = "movement_ready"
    candidate["source_summary"] = source_summary


def strip_stale_insufficient_history_reasons(candidate: dict[str, Any], guard: dict[str, Any]) -> None:
    status = str(guard.get("line_movement_lifecycle_status") or "")
    if status not in {"movement_confirmed", "publish_now_no_next_cron"}:
        return
    reasons = candidate.get("reasons") if isinstance(candidate.get("reasons"), list) else []
    candidate["reasons"] = [r for r in reasons if "insufficient_history" not in str(r)]
    reject_reasons = candidate.get("reject_reasons") if isinstance(candidate.get("reject_reasons"), list) else []
    candidate["reject_reasons"] = [r for r in reject_reasons if "insufficient_history" not in str(r)]


def candidates_from_payload(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)], None
    if isinstance(payload, dict):
        for key in ("candidates", "rows", "data", "top_candidates", "selected_candidates", "published_candidates"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)], key
    return [], None


def replace_candidates(payload: Any, key: str | None, candidates: list[dict[str, Any]]) -> Any:
    if isinstance(payload, list):
        return candidates
    if isinstance(payload, dict) and key:
        new_payload = deepcopy(payload)
        new_payload[key] = candidates
        return new_payload
    return payload




def normalize_watchlist_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    match_key = str(row.get("match_key") or row.get("canonical_match_id") or row.get("kept_match_key") or row.get("dropped_match_key") or "").strip()
    family = str(row.get("family") or "").strip()
    selection = str(row.get("selection") or row.get("selection_key") or "").strip()
    if not match_key or not family or not selection:
        return None
    candidate = dict(row)
    candidate.setdefault("match_key", match_key)
    candidate.setdefault("family", family)
    candidate.setdefault("selection", selection)
    candidate.setdefault("kickoff_utc", row.get("kickoff") or row.get("commence_time") or row.get("start_time"))
    if "odds" not in candidate and "price" in row:
        candidate["odds"] = row.get("price")
    if "ev_pct" not in candidate and "canonical_ev_pct" in row:
        candidate["ev_pct"] = row.get("canonical_ev_pct")
    if "edge_pp" not in candidate and "canonical_edge_pp" in row:
        candidate["edge_pp"] = row.get("canonical_edge_pp")
    candidate.setdefault("source_summary", {})
    if isinstance(candidate.get("source_summary"), dict):
        if row.get("odds_sources"):
            candidate["source_summary"].setdefault("sources", row.get("odds_sources"))
        if row.get("bookmaker"):
            candidate["source_summary"].setdefault("selected_bookmaker", row.get("bookmaker"))
        if row.get("source"):
            candidate["source_summary"].setdefault("selected_source", row.get("source"))
    return candidate


def watchlist_candidates_from_payload(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("candidates", "rows", "sample", "blocked_sample", "returned_candidates", "top_candidates"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend([row for row in value if isinstance(row, dict)])
    elif isinstance(payload, list):
        rows.extend([row for row in payload if isinstance(row, dict)])
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        candidate = normalize_watchlist_candidate(row)
        if not candidate:
            continue
        key = candidate_key(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def ingest_watchlist_sources(lines: dict[str, Any], now: datetime) -> tuple[list[dict[str, Any]], int, int]:
    source_reports: list[dict[str, Any]] = []
    seen_total = 0
    snapshots_written = 0
    for path in WATCHLIST_SOURCE_PATHS:
        payload = load_json(path, None)
        if payload is None:
            continue
        candidates = watchlist_candidates_from_payload(payload)
        if not candidates:
            continue
        seen_total += len(candidates)
        written_here = 0
        for candidate in candidates:
            key = "watchlist|" + candidate_key(candidate)
            entry = lines.setdefault(key, {"snapshots": []})
            snapshots = entry.get("snapshots") if isinstance(entry.get("snapshots"), list) else []
            snap = snapshot_from_candidate(candidate, now)
            # Do not duplicate the same run snapshot if the hook is called twice.
            if not snapshots or snapshots[-1].get("captured_at_utc") != snap.get("captured_at_utc"):
                snapshots.append(snap)
                written_here += 1
            entry["snapshots"] = snapshots[-env_int("LINE_HISTORY_MAX_SNAPSHOTS_PER_LINE", 12):]
            entry["last_snapshot"] = snap
            entry["watchlist_source"] = str(path)
            entry["watchlist_only"] = True
        snapshots_written += written_here
        source_reports.append({"path": str(path), "seen": len(candidates), "snapshots_written": written_here})
    return source_reports, seen_total, snapshots_written

def mutate_candidate_files(local_date: str, now: datetime) -> dict[str, Any]:
    history = load_line_history(local_date)
    lines: dict[str, Any] = history["lines"]
    drop_bad = env_bool("LINE_MOVEMENT_DROP_BAD_CANDIDATES", True)
    files_report: list[dict[str, Any]] = []
    watchlist_report: list[dict[str, Any]] = []
    total_seen = total_kept = total_dropped = 0
    watchlist_seen = 0
    watchlist_snapshots_written = 0
    for path in CANDIDATE_PATHS:
        payload = load_json(path, None)
        if payload is None:
            continue
        candidates, container_key = candidates_from_payload(payload)
        if not candidates:
            continue
        total_seen += len(candidates)
        kept: list[dict[str, Any]] = []
        dropped_rows: list[dict[str, Any]] = []
        for candidate in candidates:
            key = candidate_key(candidate)
            entry = lines.setdefault(key, {"snapshots": []})
            snapshots = entry.get("snapshots") if isinstance(entry.get("snapshots"), list) else []
            previous = snapshots[-1] if snapshots else None
            guard = line_guard(candidate, previous, now)
            snap = snapshot_from_candidate(candidate, now)
            snapshots.append(snap)
            entry["snapshots"] = snapshots[-env_int("LINE_HISTORY_MAX_SNAPSHOTS_PER_LINE", 12):]
            entry["last_snapshot"] = snap
            entry["last_guard"] = guard
            candidate["line_movement_guard"] = guard
            apply_line_movement_lifecycle(candidate, guard, len(snapshots))
            strip_stale_insufficient_history_reasons(candidate, guard)
            if not guard["passed"]:
                reasons = candidate.get("reject_reasons") if isinstance(candidate.get("reject_reasons"), list) else []
                reasons = list(reasons) + [f"line_guard:{reason}" for reason in guard["reasons"]]
                candidate["reject_reasons"] = reasons
                if drop_bad:
                    dropped_rows.append({
                        "match_key": candidate_match_key(candidate),
                        "selection": candidate.get("selection"),
                        "odds": candidate_odds(candidate),
                        "guard": guard,
                    })
                    continue
            kept.append(candidate)
        if drop_bad:
            new_payload = replace_candidates(payload, container_key, kept)
            write_json(path, new_payload)
        total_kept += len(kept)
        total_dropped += len(dropped_rows)
        files_report.append({
            "path": str(path),
            "container_key": container_key,
            "seen": len(candidates),
            "kept": len(kept),
            "dropped": len(dropped_rows),
            "dropped_sample": dropped_rows[:20],
        })
    watchlist_report, watchlist_seen, watchlist_snapshots_written = ingest_watchlist_sources(lines, now)
    total_seen += watchlist_seen
    history["updated_at_utc"] = now.isoformat()
    write_json(history_path_for_date(local_date), history)
    write_json(LINE_HISTORY_DIR / "latest.json", history)
    report = {
        "status": "ok",
        "date_local": local_date,
        "updated_at_utc": now.isoformat(),
        "candidate_files_seen": len(files_report),
        "watchlist_source_files_seen": len(watchlist_report),
        "candidates_seen": total_seen,
        "candidate_file_candidates_seen": total_seen - watchlist_seen,
        "watchlist_candidates_seen": watchlist_seen,
        "watchlist_snapshots_written": watchlist_snapshots_written,
        "candidates_kept": total_kept,
        "candidates_dropped": total_dropped,
        "drop_bad_candidates": drop_bad,
        "files": files_report,
        "watchlist_sources": watchlist_report,
    }
    write_json(LINE_GUARD_REPORT_PATH, report)
    return report


def update_inventory_priority(local_date: str, now: datetime) -> dict[str, Any]:
    paths = [DAY_INV_DIR / f"{local_date}.json", DAY_INV_DIR / "today.json", DAY_INV_DIR / "latest.json"]
    inventory_path = next((p for p in paths if p.exists()), DAY_INV_DIR / f"{local_date}.json")
    inventory = load_json(inventory_path, {})
    if not isinstance(inventory, dict):
        inventory = {"date_local": local_date, "matches": []}
    rows = [row for row in inventory.get("matches", []) if isinstance(row, dict)]
    next_run_min = env_int("CRON_EXPECTED_INTERVAL_MINUTES", 120)
    min_lead = env_int("MIN_KICKOFF_LEAD_MINUTES", 30)
    final_window_min = env_int("FINAL_PRE_KICKOFF_REFRESH_WINDOW_MINUTES", next_run_min + min_lead)
    urgent_window_min = env_int("URGENT_KICKOFF_WINDOW_MINUTES", 180)
    needs_odds = 0
    final_checks = 0
    no_more_runs = 0
    active_rows = 0
    low_quality_rows = 0
    allow_low_tier = env_bool("DAY_INVENTORY_ALLOW_LOW_TIER", False)
    for row in rows:
        kickoff = parse_dt(row.get("kickoff_utc") or row.get("commence_time"))
        coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        minutes = None if kickoff is None else (kickoff - now).total_seconds() / 60.0
        status = "unknown"
        priority = 0.0
        low_quality_reason = None if allow_low_tier else low_quality_inventory_reason(row)
        if low_quality_reason:
            low_quality_rows += 1
            row["inventory_quality_status"] = "excluded_low_quality"
            row["inventory_quality_reason"] = low_quality_reason
            row["minutes_to_kickoff"] = round(minutes, 2) if minutes is not None else None
            row["pre_kickoff_status"] = "low_quality_excluded"
            row["priority"] = -500.0
            row["refresh_plan"] = {
                "needs_odds_refresh": False,
                "needs_context_refresh": False,
                "final_pre_kickoff_check_required": False,
                "no_more_regular_run_before_kickoff": False,
                "expected_next_run_interval_minutes": next_run_min,
                "min_kickoff_lead_minutes": min_lead,
                "max_current_line_age_minutes": 0,
                "last_odds_refresh_utc": None,
                "blocked_reason": "low_quality_inventory_match",
            }
            continue
        row.pop("inventory_quality_status", None)
        row.pop("inventory_quality_reason", None)
        if minutes is None:
            status = "unknown_kickoff"
        elif minutes < 0:
            status = "started"
            priority = -100.0
        elif minutes < min_lead:
            status = "too_soon"
            priority = 5.0
        elif minutes <= urgent_window_min:
            status = "urgent_near_kickoff"
            priority = 100.0 + max(0.0, urgent_window_min - minutes) / 3.0
        elif minutes <= 720:
            status = "near_window"
            priority = 60.0 + max(0.0, 720.0 - minutes) / 24.0
        else:
            status = "future_today"
            priority = 20.0
        has_odds = bool(coverage.get("odds"))
        has_context = bool(coverage.get("context"))
        if not has_odds:
            priority += 16.0
            needs_odds += 1
        if has_odds and not has_context:
            priority += 8.0
        final_check = minutes is not None and min_lead <= minutes <= final_window_min
        next_run_at = next_scheduled_run_at(now, env_int("LINE_MOVEMENT_CRON_INTERVAL_MINUTES", next_run_min)) if env_bool("LINE_MOVEMENT_USE_SCHEDULED_CRON", True) else None
        latest_useful_run_at = kickoff - timedelta(minutes=min_lead) if kickoff else None
        has_next_regular_run = (
            bool(kickoff and next_run_at and latest_useful_run_at)
            and next_run_at <= latest_useful_run_at
        )
        if env_bool("LINE_MOVEMENT_USE_SCHEDULED_CRON", True) and kickoff is not None:
            no_more = not has_next_regular_run
        else:
            no_more = minutes is not None and minutes <= next_run_min + min_lead
        final_checks += int(bool(final_check))
        no_more_runs += int(bool(no_more and minutes >= min_lead))
        active_rows += int(minutes is not None and minutes >= 0)
        refresh = row.get("refresh") if isinstance(row.get("refresh"), dict) else {}
        last_odds = parse_dt(refresh.get("last_odds_refresh_utc"))
        max_age = env_int("URGENT_ODDS_MAX_AGE_MINUTES", 35 if final_check else 90)
        odds_stale = last_odds is None or (now - last_odds).total_seconds() / 60.0 > max_age
        row["minutes_to_kickoff"] = round(minutes, 2) if minutes is not None else None
        row["pre_kickoff_status"] = status
        row["priority"] = round(priority, 3)
        row["refresh_plan"] = {
            "needs_odds_refresh": bool(odds_stale and minutes is not None and minutes >= min_lead),
            "needs_context_refresh": bool(has_odds and not has_context and minutes is not None and minutes >= min_lead),
            "final_pre_kickoff_check_required": bool(final_check),
            "no_more_regular_run_before_kickoff": bool(no_more and minutes is not None and minutes >= min_lead),
            "next_scheduled_run_at_utc": next_run_at.isoformat() if next_run_at else None,
            "latest_useful_run_at_utc": latest_useful_run_at.isoformat() if latest_useful_run_at else None,
            "has_next_regular_run_before_kickoff": has_next_regular_run,
            "expected_next_run_interval_minutes": next_run_min,
            "min_kickoff_lead_minutes": min_lead,
            "max_current_line_age_minutes": max_age,
            "last_odds_refresh_utc": last_odds.isoformat() if last_odds else None,
        }
    rows.sort(key=lambda row: (-safe_float(row.get("priority"), 0.0), safe_float(row.get("minutes_to_kickoff"), 999999.0), str(row.get("league_name") or "")))
    inventory["matches"] = rows
    inventory["updated_at_utc"] = now.isoformat()
    inventory.setdefault("sources", {})
    if isinstance(inventory["sources"], dict):
        inventory["sources"]["priority_and_refresh_plan"] = {
            "updated_at_utc": now.isoformat(),
            "active_rows": active_rows,
            "needs_odds_refresh": needs_odds,
            "final_pre_kickoff_checks": final_checks,
            "no_more_regular_run_before_kickoff": no_more_runs,
            "low_quality_rows_filtered_from_priority": low_quality_rows,
        }
    write_json(DAY_INV_DIR / f"{local_date}.json", inventory)
    alias_update = write_current_aliases(ROOT, local_date, inventory, write_json)
    refresh_plan = {
        "status": "ok",
        "date_local": local_date,
        "updated_at_utc": now.isoformat(),
        "active_matches": active_rows,
        "alias_update": alias_update,
        "matches_needing_odds_refresh": needs_odds,
        "final_pre_kickoff_checks": final_checks,
        "no_more_regular_run_before_kickoff": no_more_runs,
        "top_priority_matches": [
            {
                "match_key": row.get("match_key"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "league_name": row.get("league_name"),
                "kickoff_utc": row.get("kickoff_utc"),
                "minutes_to_kickoff": row.get("minutes_to_kickoff"),
                "priority": row.get("priority"),
                "pre_kickoff_status": row.get("pre_kickoff_status"),
                "refresh_plan": row.get("refresh_plan"),
            }
            for row in [r for r in rows if str(r.get("inventory_quality_status") or "") != "excluded_low_quality"][:40]
        ],
        "low_quality_rows_filtered_from_priority": low_quality_rows,
    }
    write_json(REFRESH_PLAN_PATH, refresh_plan)
    return refresh_plan


def main() -> int:
    now = now_utc_from_debug()
    local_date = target_date(now)
    refresh_report = update_inventory_priority(local_date, now)
    line_report = mutate_candidate_files(local_date, now)
    report = {
        "status": "ok",
        "date_local": local_date,
        "updated_at_utc": now.isoformat(),
        "refresh_plan": refresh_report,
        "line_movement_guard": line_report,
        "notes": [
            "Inventory rows are now sorted by kickoff urgency and missing coverage so the next run naturally spends quota on matches that need it most.",
            "Candidate files are guarded before Telegram fallback publication. If the current line loses EV/edge or moves sharply against the bet, the candidate is dropped.",
            "If there is still another regular cron before kickoff and no previous line snapshot exists, candidates move to awaiting_next_run instead of being published immediately.",
        ],
    }
    write_json(OUT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
