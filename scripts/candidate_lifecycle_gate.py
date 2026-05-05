from __future__ import annotations

"""Candidate lifecycle gate for controlled publication.

This gate accumulates candidate observations across runs, prioritizes near-kickoff
matches, requires repeated value confirmation, and allows controlled publication
only in the final safe window before kickoff.
"""

import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
STATE_PATH = Path(".data/candidate-lifecycle-state.json")
REPORT_PATH = Path(".data/exports/latest-candidate-lifecycle-report.json")
REPORT_MD_PATH = Path(".data/exports/latest-candidate-lifecycle-report.md")

SOURCE_PATHS = [
    Path(".data/exports/latest-detailed-run-report.json"),
    Path(".logs/debug-last-run.json"),
    Path(".data/exports/latest-run-summary.json"),
    Path(".data/exports/latest-near-miss-enrichment-queue.json"),
    Path("artifacts/controlled-fallback-report.json"),
    Path(".data/exports/latest-controlled-fallback-report.json"),
]


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return int(float(str(raw)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return float(str(raw))
    except Exception:
        return default


def now_utc() -> datetime:
    fixed = os.getenv("CANDIDATE_LIFECYCLE_NOW_UTC")
    if fixed:
        parsed = parse_dt(fixed)
        if parsed:
            return parsed
    return datetime.now(UTC)


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[key] = str(value)
    github_env = os.getenv("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def first_float(*values: Any, default: float = 0.0) -> float:
    for value in values:
        try:
            if value is None or value == "":
                continue
            parsed = float(str(value).replace(",", "."))
            if not math.isnan(parsed):
                return parsed
        except Exception:
            continue
    return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value)))
    except Exception:
        return default


def text_norm(value: Any) -> str:
    return str(value or "").strip().lower()


def is_candidate_like(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    keys = set(row)
    has_match = any(k in keys for k in ("match_key", "home_team", "away_team", "commence_time", "kickoff", "start_time"))
    has_market = any(k in keys for k in ("family", "selection", "selection_key", "market", "market_family"))
    has_value = any(k in keys for k in ("ev_pct", "expected_value_pct", "edge_pp", "edge_pct", "confidence", "publication_score", "odds"))
    return bool(has_match and has_market and has_value)


def recursively_collect_candidates(payload: Any, source: str, out: list[dict[str, Any]], depth: int = 0) -> None:
    if depth > 10:
        return
    if isinstance(payload, dict):
        if is_candidate_like(payload):
            item = dict(payload)
            item.setdefault("_candidate_source_file", source)
            out.append(item)
        for _, value in payload.items():
            if isinstance(value, str) and len(value) > 5000:
                continue
            recursively_collect_candidates(value, source, out, depth + 1)
    elif isinstance(payload, list):
        for value in payload[:1500]:
            recursively_collect_candidates(value, source, out, depth + 1)


def source_candidates() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in SOURCE_PATHS:
        if not path.exists():
            continue
        payload = load_json(path, None)
        recursively_collect_candidates(payload, str(path), out)
    deduped: dict[str, dict[str, Any]] = {}
    now = now_utc()
    for candidate in out:
        key = candidate_key(candidate)
        if not key:
            continue
        # Ignore malformed nested fragments; they created noisy rows such as None — None spreads.
        if not (candidate.get("home_team") or candidate.get("home")) or not (candidate.get("away_team") or candidate.get("away")):
            continue
        if get_kickoff(candidate) is None:
            continue
        old = deduped.get(key)
        if old is None or candidate_priority_score(candidate, now) > candidate_priority_score(old, now):
            deduped[key] = candidate
    return list(deduped.values())


def canonical_match_key(candidate: dict[str, Any]) -> str:
    direct = str(candidate.get("match_key") or candidate.get("canonical_match_id") or "").strip().lower()
    if direct:
        return direct
    home = re.sub(r"[^a-zа-яё0-9]+", "_", text_norm(candidate.get("home_team") or candidate.get("home") or "")).strip("_")
    away = re.sub(r"[^a-zа-яё0-9]+", "_", text_norm(candidate.get("away_team") or candidate.get("away") or "")).strip("_")
    dt = get_kickoff(candidate)
    day = dt.date().isoformat() if dt else "unknown_day"
    return f"{day}:{home}:{away}" if home and away else ""


def candidate_key(candidate: dict[str, Any]) -> str:
    match = canonical_match_key(candidate)
    if not match:
        return ""
    family = text_norm(candidate.get("family") or candidate.get("market_family") or candidate.get("market"))
    selection = text_norm(candidate.get("selection") or candidate.get("selection_key") or candidate.get("pick"))
    point = str(candidate.get("point") or candidate.get("line") or "").strip()
    side = text_norm(candidate.get("team_side") or candidate.get("side"))
    raw = "|".join([match, family, selection, point, side])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get_kickoff(candidate: dict[str, Any]) -> datetime | None:
    for key in ("commence_time", "kickoff", "start_time", "match_time", "date"):
        dt = parse_dt(candidate.get(key))
        if dt:
            return dt
    return None


def candidate_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    diag = candidate.get("diagnostics") if isinstance(candidate.get("diagnostics"), dict) else {}
    quality = diag.get("quality") if isinstance(diag.get("quality"), dict) else {}
    summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    integrity = candidate.get("integrity_report") if isinstance(candidate.get("integrity_report"), dict) else {}

    final_probability = first_float(candidate.get("adjusted_probability"), candidate.get("final_probability"), candidate.get("model_probability"), default=0.0)
    odds = first_float(candidate.get("odds"), summary.get("selected_odds"), summary.get("odds"), integrity.get("price"), default=0.0)
    implied_probability = (1.0 / odds) if odds > 1.0 else first_float(candidate.get("implied_probability"), candidate.get("selected_implied_probability"), default=0.0)

    ev = first_float(candidate.get("ev_pct"), candidate.get("expected_value_pct"), candidate.get("canonical_ev_pct"), quality.get("ev_pct"), default=math.nan)
    if math.isnan(ev) and odds > 1.0 and final_probability > 0:
        ev = ((final_probability * odds) - 1.0) * 100.0
    if math.isnan(ev):
        ev = 0.0

    edge = first_float(candidate.get("edge_pp"), candidate.get("edge_pct"), candidate.get("canonical_edge_pp"), candidate.get("canonical_edge_pct"), quality.get("edge_pp"), quality.get("edge_pct"), default=math.nan)
    if math.isnan(edge) and final_probability > 0 and implied_probability > 0:
        edge = (final_probability - implied_probability) * 100.0
    if math.isnan(edge):
        edge = 0.0

    confidence = first_float(candidate.get("confidence"), quality.get("confidence"), candidate.get("publication_score"), default=0.0)
    books = max(as_int(candidate.get("books_count"), 0), as_int(summary.get("books_count"), 0), as_int(integrity.get("books_count"), 0))
    sources = max(as_int(candidate.get("sources_count"), 0), as_int(summary.get("sources_count"), 0), as_int(integrity.get("sources_count"), 0))

    return {
        "ev_pct": round(float(ev), 3),
        "edge_pp": round(float(edge), 3),
        "confidence": round(float(confidence), 3),
        "odds": round(float(odds), 4),
        "books_count": books,
        "sources_count": sources,
        "family": str(candidate.get("family") or candidate.get("market_family") or candidate.get("market") or ""),
        "selection": str(candidate.get("selection") or candidate.get("selection_key") or candidate.get("pick") or ""),
        "point": candidate.get("point") or candidate.get("line"),
        "final_probability": round(float(final_probability), 6),
        "implied_probability": round(float(implied_probability), 6),
    }


def guard_reasons(candidate: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in ("integrity_reasons", "quality_reasons", "reject_reasons", "reasons"):
        value = candidate.get(key)
        if isinstance(value, list):
            reasons.extend(str(item) for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            reasons.append(value.strip())
    diag = candidate.get("diagnostics") if isinstance(candidate.get("diagnostics"), dict) else {}
    quality = diag.get("quality") if isinstance(diag.get("quality"), dict) else {}
    value = quality.get("reasons")
    if isinstance(value, list):
        reasons.extend(str(item) for item in value if str(item).strip())
    return sorted(set(reasons))


def passes_value_thresholds(candidate: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    metrics = candidate_metrics(candidate)
    reasons: list[str] = []
    min_ev = env_float("CANDIDATE_RECHECK_MIN_EV_PCT", 5.0)
    min_edge = env_float("CANDIDATE_RECHECK_MIN_EDGE_PP", 2.2)
    min_conf = env_float("CANDIDATE_RECHECK_MIN_CONFIDENCE", 62.0)
    min_books = env_int("CANDIDATE_RECHECK_MIN_BOOKS", 2)
    min_sources = env_int("CANDIDATE_RECHECK_MIN_SOURCES", 1)

    if metrics["ev_pct"] < min_ev:
        reasons.append(f"ev_below_min:{metrics['ev_pct']}<{min_ev}")
    if metrics["edge_pp"] < min_edge:
        reasons.append(f"edge_below_min:{metrics['edge_pp']}<{min_edge}")
    if metrics["confidence"] < min_conf:
        reasons.append(f"confidence_below_min:{metrics['confidence']}<{min_conf}")
    if metrics["books_count"] < min_books and metrics["sources_count"] < 2:
        reasons.append(f"market_depth_below_min:books={metrics['books_count']},sources={metrics['sources_count']}")
    if metrics["sources_count"] < min_sources:
        reasons.append(f"sources_below_min:{metrics['sources_count']}<{min_sources}")

    hard_guard_words = ("suspicious", "integrity", "conflict", "stale", "corner", "half_time", "family_contains")
    gr = guard_reasons(candidate)
    hard = [r for r in gr if any(word in r.lower() for word in hard_guard_words)]
    if hard and not env_bool("CANDIDATE_RECHECK_IGNORE_EXISTING_REJECT_REASONS", False):
        reasons.append("guard_reasons_present:" + ";".join(hard[:4]))
    return not reasons, reasons, metrics


def price_stable(prev_odds: float, current_odds: float) -> bool:
    if prev_odds <= 1.0 or current_odds <= 1.0:
        return True
    max_move_pct = env_float("CANDIDATE_RECHECK_MAX_ODDS_MOVE_PCT", 12.0)
    move_pct = abs(current_odds - prev_odds) / max(prev_odds, 1e-9) * 100.0
    return move_pct <= max_move_pct


def kickoff_state(candidate: dict[str, Any], now: datetime) -> dict[str, Any]:
    kickoff = get_kickoff(candidate)
    if kickoff is None:
        return {"has_kickoff": False, "minutes_to_kickoff": None, "in_safe_window": False, "in_final_window": False, "too_late": False, "too_early": False}
    minutes = (kickoff - now).total_seconds() / 60.0
    min_lead = env_int("CANDIDATE_RECHECK_MIN_KICKOFF_LEAD_MINUTES", env_int("MIN_KICKOFF_LEAD_MINUTES", 25))
    final_min = env_int("CANDIDATE_RECHECK_FINAL_WINDOW_MINUTES", 90)
    watch_max = env_int("CANDIDATE_RECHECK_WATCH_WINDOW_MINUTES", 720)
    return {
        "has_kickoff": True,
        "kickoff_utc": kickoff.isoformat(),
        "minutes_to_kickoff": round(minutes, 2),
        "in_safe_window": minutes >= min_lead,
        "in_final_window": min_lead <= minutes <= final_min,
        "in_watch_window": min_lead <= minutes <= watch_max,
        "too_late": minutes < min_lead,
        "too_early": minutes > final_min,
        "min_lead_minutes": min_lead,
        "final_window_minutes": final_min,
    }


def candidate_priority_score(candidate: dict[str, Any], now: datetime) -> float:
    metrics = candidate_metrics(candidate)
    ks = kickoff_state(candidate, now)
    minutes = ks.get("minutes_to_kickoff")
    time_score = 0.0
    if isinstance(minutes, (int, float)):
        if ks.get("in_final_window"):
            time_score = 45.0 + max(0.0, 90.0 - float(minutes)) * 0.12
        elif 90.0 < float(minutes) <= 180.0:
            time_score = 25.0 - (float(minutes) - 90.0) * 0.08
        elif 180.0 < float(minutes) <= 720.0:
            time_score = 8.0
        else:
            time_score = -50.0
    return time_score + metrics["ev_pct"] * 2.0 + metrics["edge_pp"] * 2.5 + metrics["confidence"] * 0.35 + metrics["books_count"] * 2.0 + metrics["sources_count"] * 1.5


def load_state() -> dict[str, Any]:
    payload = load_json(STATE_PATH, {})
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("candidates", {})
    return payload


def prune_state(state: dict[str, Any], now: datetime) -> None:
    ttl_hours = env_int("CANDIDATE_LIFECYCLE_STATE_TTL_HOURS", 30)
    cutoff = now - timedelta(hours=max(1, ttl_hours))
    candidates = state.get("candidates") if isinstance(state.get("candidates"), dict) else {}
    kept: dict[str, Any] = {}
    for key, row in candidates.items():
        if not isinstance(row, dict):
            continue
        last = parse_dt(row.get("last_seen_at"))
        kickoff = parse_dt(row.get("kickoff_utc"))
        if last and last >= cutoff:
            if kickoff is None or kickoff >= now - timedelta(minutes=120):
                kept[key] = row
    state["candidates"] = kept


def update_state_with_candidates(state: dict[str, Any], candidates: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    store = state.setdefault("candidates", {})
    for candidate in candidates:
        key = candidate_key(candidate)
        if not key:
            continue
        ok, reasons, metrics = passes_value_thresholds(candidate)
        ks = kickoff_state(candidate, now)
        prev = store.get(key) if isinstance(store.get(key), dict) else {}
        prev_odds = as_float(prev.get("last_odds"), 0.0)
        stable = price_stable(prev_odds, metrics["odds"])
        value_streak = as_int(prev.get("value_streak"), 0)
        if ok and stable and ks.get("in_watch_window", False):
            value_streak += 1
        else:
            value_streak = 0 if env_bool("CANDIDATE_RECHECK_RESET_STREAK_ON_FAIL", True) else value_streak
        seen_count = as_int(prev.get("seen_count"), 0) + 1
        first_seen = prev.get("first_seen_at") or now.isoformat()
        row = {
            **prev,
            "key": key,
            "match_key": canonical_match_key(candidate),
            "home_team": candidate.get("home_team") or candidate.get("home") or prev.get("home_team"),
            "away_team": candidate.get("away_team") or candidate.get("away") or prev.get("away_team"),
            "league_name": candidate.get("league_name") or candidate.get("league") or prev.get("league_name"),
            "family": metrics["family"],
            "selection": metrics["selection"],
            "point": metrics["point"],
            "first_seen_at": first_seen,
            "last_seen_at": now.isoformat(),
            "seen_count": seen_count,
            "value_streak": value_streak,
            "last_value_ok": ok,
            "last_reasons": reasons,
            "last_metrics": metrics,
            "last_odds": metrics["odds"],
            "last_price_stable": stable,
            "kickoff_utc": ks.get("kickoff_utc") or prev.get("kickoff_utc"),
            "kickoff_state": ks,
            "candidate_source_file": candidate.get("_candidate_source_file"),
            "priority_score": round(candidate_priority_score(candidate, now), 3),
        }
        store[key] = row
        rows.append(row)
    return rows


def publication_decision(rows: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    min_confirmations = env_int("CANDIDATE_RECHECK_MIN_CONFIRMATIONS", 2)
    min_seen = env_int("CANDIDATE_RECHECK_MIN_SEEN_COUNT", min_confirmations)
    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for row in rows:
        reasons: list[str] = []
        if not row.get("last_value_ok"):
            reasons.extend(row.get("last_reasons") or ["value_threshold_failed"])
        if not row.get("last_price_stable"):
            reasons.append("odds_moved_too_much_since_previous_check")
        if as_int(row.get("seen_count"), 0) < min_seen:
            reasons.append(f"not_seen_enough:{row.get('seen_count')}/{min_seen}")
        if as_int(row.get("value_streak"), 0) < min_confirmations:
            reasons.append(f"not_rechecked_enough:{row.get('value_streak')}/{min_confirmations}")
        ks = row.get("kickoff_state") if isinstance(row.get("kickoff_state"), dict) else {}
        if not ks.get("in_safe_window"):
            reasons.append("kickoff_too_close_or_missing")
        if not ks.get("in_final_window"):
            reasons.append("not_in_final_publication_window")
        if reasons:
            blocked.append({**row, "block_reasons": reasons})
        else:
            eligible.append(row)
    eligible.sort(key=lambda item: float(item.get("priority_score") or 0.0), reverse=True)
    blocked.sort(key=lambda item: float(item.get("priority_score") or 0.0), reverse=True)
    selected = eligible[0] if eligible else None
    return {"allow_publish": bool(selected), "selected": selected, "eligible_count": len(eligible), "eligible": eligible[:10], "blocked_count": len(blocked), "blocked_top": blocked[:20]}


def write_markdown(report: dict[str, Any]) -> None:
    selected = report.get("decision", {}).get("selected") or {}
    lines = [
        "# Candidate lifecycle gate",
        "",
        f"- Created UTC: `{report.get('created_at_utc')}`",
        f"- Candidates found this run: **{report.get('candidates_found')}**",
        f"- Candidates tracked: **{report.get('state_candidates_total')}**",
        f"- Value-ok candidates this run: **{report.get('value_ok_count')}**",
        f"- Allow publish: **{report.get('decision', {}).get('allow_publish')}**",
        f"- Eligible: **{report.get('decision', {}).get('eligible_count')}**",
        f"- Blocked: **{report.get('decision', {}).get('blocked_count')}**",
        "",
    ]
    if selected:
        lines.extend([
            "## Selected for final publication window", "",
            f"- Match: `{selected.get('home_team')} — {selected.get('away_team')}`",
            f"- League: `{selected.get('league_name')}`",
            f"- Market: `{selected.get('family')} / {selected.get('selection')} / {selected.get('point')}`",
            f"- Kickoff UTC: `{selected.get('kickoff_utc')}`",
            f"- Seen count: `{selected.get('seen_count')}`",
            f"- Value streak: `{selected.get('value_streak')}`",
            f"- Metrics: `{json.dumps(selected.get('last_metrics'), ensure_ascii=False)}`", "",
        ])
    lines.extend(["## Top blocked", "", "| Match | Market | Kickoff min | Seen | Streak | Reasons |", "|---|---|---:|---:|---:|---|"])
    for row in report.get("decision", {}).get("blocked_top", [])[:12]:
        ks = row.get("kickoff_state") or {}
        match = f"{row.get('home_team')} — {row.get('away_team')}"
        market = f"{row.get('family')} {row.get('selection')} {row.get('point') or ''}".strip()
        reasons = "; ".join(row.get("block_reasons") or [])[:300]
        lines.append(f"| `{match}` | `{market}` | {ks.get('minutes_to_kickoff')} | {row.get('seen_count')} | {row.get('value_streak')} | {reasons} |")
    REPORT_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not env_bool("CANDIDATE_LIFECYCLE_ENABLED", True):
        append_env({"CANDIDATE_LIFECYCLE_ALLOW_PUBLISH": "true", "CANDIDATE_LIFECYCLE_REASON": "disabled"})
        return 0
    now = now_utc()
    state = load_state()
    prune_state(state, now)
    candidates = source_candidates()
    rows = update_state_with_candidates(state, candidates, now)
    decision = publication_decision(rows, now)
    state["updated_at_utc"] = now.isoformat()
    state["last_candidates_seen"] = len(candidates)
    write_json(STATE_PATH, state)
    report = {
        "created_at_utc": now.isoformat(),
        "state_path": str(STATE_PATH),
        "removed_publish_blockers": ["bookies_api", "api_football", "oddspapi"],
        "settings": {
            "min_confirmations": env_int("CANDIDATE_RECHECK_MIN_CONFIRMATIONS", 2),
            "min_seen_count": env_int("CANDIDATE_RECHECK_MIN_SEEN_COUNT", env_int("CANDIDATE_RECHECK_MIN_CONFIRMATIONS", 2)),
            "final_window_minutes": env_int("CANDIDATE_RECHECK_FINAL_WINDOW_MINUTES", 90),
            "min_lead_minutes": env_int("CANDIDATE_RECHECK_MIN_KICKOFF_LEAD_MINUTES", env_int("MIN_KICKOFF_LEAD_MINUTES", 25)),
            "min_ev_pct": env_float("CANDIDATE_RECHECK_MIN_EV_PCT", 5.0),
            "min_edge_pp": env_float("CANDIDATE_RECHECK_MIN_EDGE_PP", 2.2),
            "min_confidence": env_float("CANDIDATE_RECHECK_MIN_CONFIDENCE", 62.0),
        },
        "source_paths": [str(path) for path in SOURCE_PATHS if path.exists()],
        "candidates_found": len(candidates),
        "updated_rows": rows[:50],
        "value_ok_count": sum(1 for row in rows if row.get("last_value_ok")),
        "state_candidates_total": len(state.get("candidates") or {}),
        "decision": decision,
    }
    write_json(REPORT_PATH, report)
    write_markdown(report)
    selected = decision.get("selected") or {}
    allow = bool(decision.get("allow_publish"))
    reason = "selected_final_recheck_passed" if allow else "no_candidate_passed_lifecycle_recheck"
    append_env({
        "CANDIDATE_LIFECYCLE_ALLOW_PUBLISH": "true" if allow else "false",
        "CANDIDATE_LIFECYCLE_REASON": reason,
        "CANDIDATE_LIFECYCLE_SELECTED_KEY": str(selected.get("key") or ""),
        "CANDIDATE_LIFECYCLE_SELECTED_MATCH_KEY": str(selected.get("match_key") or ""),
        "CANDIDATE_LIFECYCLE_REPORT_PATH": str(REPORT_PATH),
    })
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
