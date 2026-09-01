from __future__ import annotations

"""Guarded controlled fallback publisher v18.

Production safeguards:
- final publish window before kickoff;
- final cron / line recheck lifecycle;
- duplicate sent-index/report protection;
- daily publication cap;
- reserved late-day slot so the first early B-tier picks do not consume all 3
  daily slots before stronger evening candidates appear.

This wrapper only adds hard-reject reasons before the base publisher sends. It does
not relax value, xG, quality, price-integrity, movement or duplicate guards.
"""

import importlib.util
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = Path(__file__).resolve().with_name("publish_controlled_fallback.py")
REPORT_PATH = ROOT / ".data" / "exports" / "latest-controlled-fallback-prepublish-guard.json"

_GUARD_EVENTS: list[dict[str, Any]] = []
MOVEMENT_READY_STATUSES = {
    "movement_confirmed",
    "movement_rechecked_across_cron_windows",
    "publish_now_no_next_cron",
    "movement_ready",
}


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
    if raw in {"0", "false", "no", "off", "none", "null"}:
        return False
    return raw in {"1", "true", "yes", "on", "force"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", ".").strip())
    except Exception:
        return default


def _load_json(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def _write_json(path: str | Path, payload: Any) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
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


def _local_tz() -> ZoneInfo:
    for name in (
        os.getenv("CONTROLLED_FALLBACK_DAILY_LIMIT_TIMEZONE"),
        os.getenv("LINE_MOVEMENT_CRON_TIMEZONE"),
        os.getenv("APP_TIMEZONE"),
        os.getenv("TZ"),
        "Europe/Moscow",
    ):
        try:
            return ZoneInfo(str(name))
        except Exception:
            continue
    return ZoneInfo("Europe/Moscow")


def _canonical_selection(row: dict[str, Any]) -> str:
    explicit = _norm(row.get("selection_key"))
    family = _norm(row.get("family") or row.get("market_family"))
    selection = str(row.get("selection") or "").strip().casefold().replace("ё", "е")
    if explicit in {"under", "over", "home", "away", "draw"}:
        return explicit
    if family in {"totals", "teamtotals", "spreads"} or "тотал" in selection:
        if any(token in selection for token in ("under", "меньше", "тотал меньше", "тм")):
            return "under"
        if any(token in selection for token in ("over", "больше", "тотал больше", "тб")):
            return "over"
    return explicit or _norm(selection)


def _candidate_signature(row: dict[str, Any]) -> dict[str, str]:
    selection_text = str(row.get("selection") or "")
    line = _point(row.get("point") or row.get("line") or row.get("handicap"))
    if not line:
        m = re.search(r"(?<!\d)(\d+(?:[\.,]\d+)?)(?!\d)", selection_text)
        line = _point(m.group(1)) if m else ""
    return {
        "match_key": _norm(row.get("canonical_match_id") or row.get("match_key") or row.get("event_key")),
        "family": _norm(row.get("family") or row.get("market_family")),
        "selection": _canonical_selection(row),
        "point": line,
        "home": _norm(row.get("home_team") or row.get("home")),
        "away": _norm(row.get("away_team") or row.get("away")),
    }


def _same_candidate(candidate: dict[str, Any], row: dict[str, Any]) -> bool:
    cand = _candidate_signature(candidate)
    other = _candidate_signature(row)
    if cand["match_key"] and other["match_key"] and cand["match_key"] != other["match_key"]:
        return False
    if cand["home"] and other["home"] and cand["home"] != other["home"]:
        return False
    if cand["away"] and other["away"] and cand["away"] != other["away"]:
        return False
    if cand["family"] and other["family"] and cand["family"] != other["family"]:
        return False
    if cand["selection"] and other["selection"] and cand["selection"] != other["selection"]:
        return False
    if cand["point"] and other["point"] and cand["point"] != other["point"]:
        return False
    return bool(cand["match_key"] or (cand["home"] and cand["away"]))


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
        row = dict(item)
        coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        row.setdefault("family", coverage.get("family"))
        if not _same_candidate(candidate, row):
            continue
        reject_reasons = list(coverage.get("reject_reasons") or item.get("reject_reasons") or [])
        movement = coverage.get("movement") if isinstance(coverage.get("movement"), dict) else {}
        out: list[str] = []
        if "needs_next_cron_line_movement_recheck" in reject_reasons or movement.get("reason") == "needs_next_cron_line_movement_recheck":
            out.append("controlled_fallback_windowed_line_movement_recheck_required")
        elif reject_reasons and _truthy(os.getenv("CONTROLLED_FALLBACK_RESPECT_ALL_WINDOWED_BLOCKS"), True):
            out.extend(f"controlled_fallback_windowed_block:{reason}" for reason in reject_reasons[:3])
        if out:
            _GUARD_EVENTS.append({"guard": "windowed_publication_filter", "match_key": candidate.get("match_key"), "home_team": candidate.get("home_team"), "away_team": candidate.get("away_team"), "family": candidate.get("family"), "selection": candidate.get("selection"), "point": candidate.get("point"), "reasons": out, "windowed_reject_reasons": reject_reasons, "movement": movement})
        return out
    return []


def _iter_payload_rows(payload: Any, *, report_published: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for val in payload.values():
            if isinstance(val, dict):
                rows.append(val)
        for key in ("selected_all", "bets", "published_candidates", "items", "rows"):
            val = payload.get(key)
            if isinstance(val, list):
                rows.extend([x for x in val if isinstance(x, dict)])
            elif isinstance(val, dict):
                rows.append(val)
        if isinstance(payload.get("selected"), dict) and (report_published or payload.get("published")):
            row = dict(payload["selected"])
            row.setdefault("published", True)
            row.setdefault("created_at", payload.get("created_at"))
            rows.append(row)
    elif isinstance(payload, list):
        rows.extend([x for x in payload if isinstance(x, dict)])
    return rows


def _is_published_pick_row(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or row.get("publication_status") or "").strip().lower()
    if status in {"published", "sent", "pending", "won", "lost", "push", "void", "cancelled", "refunded"}:
        return True
    if bool(row.get("telegram_sent")) or bool(row.get("published")):
        return True
    if row.get("sent_at") or row.get("published_at"):
        return True
    if row.get("commence_time") and row.get("odds") and (row.get("home_team") or row.get("match_key")):
        return True
    return False


def _row_local_day(row: dict[str, Any], tz: ZoneInfo) -> str | None:
    prefer_published = _truthy(os.getenv("CONTROLLED_FALLBACK_DAILY_LIMIT_USE_PUBLISHED_AT"), True)
    keys = ["published_at", "sent_at", "created_at", "telegram_sent_at", "commence_time", "kickoff", "start_time"] if prefer_published else ["commence_time", "kickoff", "start_time", "published_at", "sent_at", "created_at"]
    for key in keys:
        dt = _parse_dt(row.get(key))
        if dt is not None:
            return dt.astimezone(tz).date().isoformat()
    return None


def _daily_existing_fallback_count() -> dict[str, Any]:
    tz = _local_tz()
    today = datetime.now(UTC).astimezone(tz).date().isoformat()
    paths = [
        ROOT / ".data" / "fallback-sent-index.json",
        ROOT / ".data" / "published-candidate-index.json",
        ROOT / ".data" / "state.json",
        ROOT / ".data" / "exports" / "latest-controlled-fallback-report.json",
        ROOT / "artifacts" / "controlled-fallback-report.json",
    ]
    seen: set[str] = set()
    samples: list[dict[str, Any]] = []
    for path in paths:
        payload = _load_json(path, {})
        report_published = isinstance(payload, dict) and bool(payload.get("published"))
        for row in _iter_payload_rows(payload, report_published=report_published):
            if not _is_published_pick_row(row):
                continue
            if _row_local_day(row, tz) != today:
                continue
            sig = _candidate_signature(row)
            key = "|".join([sig.get("match_key") or f"{sig.get('home')}--{sig.get('away')}", sig.get("family"), sig.get("selection"), sig.get("point")])
            if not key.strip("|"):
                key = json.dumps(row, ensure_ascii=False, sort_keys=True)[:300]
            if key in seen:
                continue
            seen.add(key)
            if len(samples) < 10:
                samples.append({"source_path": str(path), "key": key, "home_team": row.get("home_team") or row.get("home"), "away_team": row.get("away_team") or row.get("away"), "selection": row.get("selection"), "point": row.get("point"), "published_at": row.get("published_at") or row.get("sent_at") or row.get("created_at"), "commence_time": row.get("commence_time") or row.get("kickoff") or row.get("start_time")})
    return {"date": today, "count": len(seen), "samples": samples}


def _duplicate_reason(candidate: dict[str, Any]) -> str | None:
    # Active strict duplicate check against sent/published state. This is strict
    # for sent/published rows only and does not block unpublished diagnostics.
    paths = [
        ROOT / ".data" / "fallback-sent-index.json",
        ROOT / ".data" / "published-candidate-index.json",
        ROOT / ".data" / "state.json",
        ROOT / ".data" / "exports" / "latest-controlled-fallback-report.json",
        ROOT / "artifacts" / "controlled-fallback-report.json",
    ]
    now = datetime.now(UTC)
    for path in paths:
        payload = _load_json(path, {})
        report_published = isinstance(payload, dict) and bool(payload.get("published"))
        for row in _iter_payload_rows(payload, report_published=report_published):
            if not _is_published_pick_row(row):
                continue
            kickoff = _parse_dt(row.get("commence_time") or row.get("kickoff") or row.get("start_time"))
            if kickoff is not None and kickoff < now:
                continue
            if _same_candidate(candidate, row):
                if "fallback-sent-index" in str(path):
                    return "duplicate_persisted_fallback_sent_index"
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
    anchor_minute = _as_int(os.getenv("LINE_MOVEMENT_CRON_ANCHOR_MINUTE") or os.getenv("CONTROLLED_FALLBACK_CRON_ANCHOR_MINUTE") or 0, 0)
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
    min_recheck = _as_float(os.getenv("CONTROLLED_FALLBACK_MIN_RECHECK_MINUTES") or os.getenv("LINE_MOVEMENT_MIN_RECHECK_MINUTES") or 60.0, 60.0)
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
    return _candidate_movement_confirmed(candidate)


def _final_cron_recheck_reasons(candidate: dict[str, Any]) -> list[str]:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_REQUIRE_FINAL_CRON_RECHECK"), True):
        return []
    kickoff = _parse_dt(candidate.get("commence_time") or candidate.get("kickoff") or candidate.get("start_time"))
    if kickoff is None:
        return ["controlled_fallback_missing_kickoff_for_final_recheck"]
    now = datetime.now(UTC)
    min_lead = _as_int(os.getenv("LINE_MOVEMENT_MIN_LEAD_MINUTES") or os.getenv("MIN_KICKOFF_LEAD_MINUTES") or 15, 15)
    interval = _as_int(os.getenv("CRON_EXPECTED_INTERVAL_MINUTES") or os.getenv("LINE_MOVEMENT_CRON_INTERVAL_MINUTES") or 120, 120)
    next_run = _next_scheduled_run_at(now, interval)
    latest_useful = kickoff - timedelta(minutes=max(0, min_lead))
    has_next_regular_run = bool(next_run is not None and next_run <= latest_useful)
    has_previous_recheck = _line_state_has_previous_recheck(candidate, now)
    reasons: list[str] = []
    if has_next_regular_run and not has_previous_recheck:
        reasons.append("controlled_fallback_next_regular_run_before_kickoff")
        reasons.append("controlled_fallback_missing_line_recheck")
    elif not has_next_regular_run:
        has_previous_recheck = True
    if not has_previous_recheck and "controlled_fallback_missing_line_recheck" not in reasons:
        reasons.append("controlled_fallback_missing_line_recheck")
    if reasons:
        _GUARD_EVENTS.append({"guard": "final_cron_recheck", "match_key": candidate.get("match_key"), "home_team": candidate.get("home_team"), "away_team": candidate.get("away_team"), "family": candidate.get("family"), "selection": candidate.get("selection"), "point": candidate.get("point"), "kickoff_utc": kickoff.isoformat(), "next_regular_run_at_utc": next_run.isoformat() if next_run else None, "latest_useful_run_at_utc": latest_useful.isoformat(), "reasons": reasons})
    return reasons


def _publish_window_reasons(candidate: dict[str, Any]) -> list[str]:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_ENFORCE_PUBLISH_WINDOW"), True):
        return []
    kickoff = _parse_dt(candidate.get("commence_time") or candidate.get("kickoff") or candidate.get("start_time"))
    if kickoff is None:
        return []
    now = datetime.now(UTC)
    min_lead = _as_int(os.getenv("LINE_MOVEMENT_MIN_LEAD_MINUTES") or os.getenv("MIN_KICKOFF_LEAD_MINUTES") or 15, 15)
    window_hours = _as_float(os.getenv("CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS") or os.getenv("PUBLISH_WINDOW_HOURS") or 2.0, 2.0)
    latest_allowed = now + timedelta(hours=max(0.25, window_hours))
    earliest_allowed = now + timedelta(minutes=max(0, min_lead))
    reasons: list[str] = []
    if kickoff < now:
        reasons.append("match_already_started")
    elif kickoff < earliest_allowed:
        reasons.append("match_time_outside_window")
    elif kickoff > latest_allowed:
        reasons.append("controlled_fallback_publish_window_too_early")
        reasons.append("match_time_too_late")
    if reasons:
        _GUARD_EVENTS.append({"guard": "controlled_fallback_publish_window", "match_key": candidate.get("match_key"), "home_team": candidate.get("home_team"), "away_team": candidate.get("away_team"), "kickoff_utc": kickoff.isoformat(), "now_utc": now.isoformat(), "publish_window_hours": window_hours, "earliest_allowed_utc": earliest_allowed.isoformat(), "latest_allowed_utc": latest_allowed.isoformat(), "reasons": reasons})
    return reasons


def _metric(candidate: dict[str, Any], metrics: dict[str, Any], *names: str) -> float:
    nested = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
    for name in names:
        for src in (metrics, nested, candidate):
            if isinstance(src, dict) and src.get(name) not in (None, ""):
                return _as_float(src.get(name), 0.0)
    return 0.0


def _is_elite_reserved_slot_candidate(candidate: dict[str, Any], metrics: dict[str, Any]) -> bool:
    min_ev = _as_float(os.getenv("CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_EV_PCT") or 12.0, 12.0)
    min_edge = _as_float(os.getenv("CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_EDGE_PP") or 6.5, 6.5)
    min_conf = _as_float(os.getenv("CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_CONFIDENCE") or 73.0, 73.0)
    min_quality = _as_float(os.getenv("CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_QUALITY") or 74.0, 74.0)
    ev = _metric(candidate, metrics, "canonical_ev_pct", "ev_pct", "ev")
    edge = _metric(candidate, metrics, "canonical_edge_pp", "edge_pp", "edge")
    confidence = _metric(candidate, metrics, "confidence")
    quality = _metric(candidate, metrics, "quality_score", "quality", "q")
    return ev >= min_ev and edge >= min_edge and confidence >= min_conf and quality >= min_quality


def _daily_limit_reasons(candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    if not _truthy(os.getenv("CONTROLLED_FALLBACK_DAILY_LIMIT_ENABLED"), True):
        return []
    limit = _as_int(os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED") or os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_B_TIER") or 0, 0)
    if limit <= 0:
        return []
    info = _daily_existing_fallback_count()
    count = int(info.get("count") or 0)
    if count >= limit:
        reason = f"controlled_fallback_daily_limit_reached:{count}/{limit}"
        _GUARD_EVENTS.append({"guard": "controlled_fallback_daily_limit", "match_key": candidate.get("match_key"), "home_team": candidate.get("home_team"), "away_team": candidate.get("away_team"), "family": candidate.get("family"), "selection": candidate.get("selection"), "point": candidate.get("point"), "date": info.get("date"), "existing_count": count, "limit": limit, "samples": info.get("samples") or [], "reasons": [reason]})
        return [reason]

    if not _truthy(os.getenv("CONTROLLED_FALLBACK_RESERVED_DAILY_SLOT_ENABLED"), True):
        return []
    reserved = max(0, min(limit - 1, _as_int(os.getenv("CONTROLLED_FALLBACK_RESERVED_DAILY_SLOTS") or 1, 1)))
    if reserved <= 0:
        return []
    tz = _local_tz()
    now_local = datetime.now(UTC).astimezone(tz)
    release_hour = max(0, min(23, _as_int(os.getenv("CONTROLLED_FALLBACK_RESERVED_SLOT_RELEASE_LOCAL_HOUR") or 18, 18)))
    release_minute = max(0, min(59, _as_int(os.getenv("CONTROLLED_FALLBACK_RESERVED_SLOT_RELEASE_LOCAL_MINUTE") or 0, 0)))
    release_at = now_local.replace(hour=release_hour, minute=release_minute, second=0, microsecond=0)
    pre_release_limit = max(0, limit - reserved)
    elite = _is_elite_reserved_slot_candidate(candidate, metrics)
    if now_local < release_at and count >= pre_release_limit and not elite:
        reason = f"controlled_fallback_reserved_daily_slot_held_until:{count}/{limit}@{release_hour:02d}:{release_minute:02d}"
        _GUARD_EVENTS.append({"guard": "controlled_fallback_reserved_daily_slot", "match_key": candidate.get("match_key"), "home_team": candidate.get("home_team"), "away_team": candidate.get("away_team"), "family": candidate.get("family"), "selection": candidate.get("selection"), "point": candidate.get("point"), "date": info.get("date"), "existing_count": count, "limit": limit, "reserved_slots": reserved, "pre_release_limit": pre_release_limit, "release_at_local": release_at.isoformat(), "elite_override": elite, "metrics": {"ev_pct": _metric(candidate, metrics, "canonical_ev_pct", "ev_pct", "ev"), "edge_pp": _metric(candidate, metrics, "canonical_edge_pp", "edge_pp", "edge"), "confidence": _metric(candidate, metrics, "confidence"), "quality": _metric(candidate, metrics, "quality_score", "quality", "q")}, "reasons": [reason]})
        return [reason]
    return []


_original_hard_reject_reasons = base.hard_reject_reasons


def hard_reject_reasons_guarded(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
    reasons = list(_original_hard_reject_reasons(candidate, metrics, sent_index) or [])
    extra: list[str] = []
    duplicate = _duplicate_reason(candidate)
    if duplicate:
        extra.append(duplicate)
    extra.extend(_publish_window_reasons(candidate))
    extra.extend(_daily_limit_reasons(candidate, metrics))
    extra.extend(_final_cron_recheck_reasons(candidate))
    extra.extend(_windowed_movement_reasons(candidate))
    if extra:
        _GUARD_EVENTS.append({"guard": "controlled_fallback_prepublish", "match_key": candidate.get("match_key"), "home_team": candidate.get("home_team"), "away_team": candidate.get("away_team"), "family": candidate.get("family"), "selection": candidate.get("selection"), "point": candidate.get("point"), "reasons": extra})
    return reasons + extra


base.hard_reject_reasons = hard_reject_reasons_guarded


def main() -> int:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "starting",
        "policy_version": "controlled-fallback-guard-v18-two-hour-window-daily-cap-reserved-slot",
        "publish_window_hours": _as_float(os.getenv("CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS") or os.getenv("PUBLISH_WINDOW_HOURS") or 2.0, 2.0),
        "daily_limit": _as_int(os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED") or os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_B_TIER") or 0, 0),
        "reserved_slot_enabled": _truthy(os.getenv("CONTROLLED_FALLBACK_RESERVED_DAILY_SLOT_ENABLED"), True),
        "reserved_slots": _as_int(os.getenv("CONTROLLED_FALLBACK_RESERVED_DAILY_SLOTS") or 1, 1),
        "reserved_slot_release_local_hour": _as_int(os.getenv("CONTROLLED_FALLBACK_RESERVED_SLOT_RELEASE_LOCAL_HOUR") or 18, 18),
        "daily_existing": _daily_existing_fallback_count(),
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
        payload["events"] = _GUARD_EVENTS[:240]
        payload["blocked_events"] = len(_GUARD_EVENTS)
        _write_json(REPORT_PATH, payload)


if __name__ == "__main__":
    raise SystemExit(main())
