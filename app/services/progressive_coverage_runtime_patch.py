from __future__ import annotations

"""Progressive coverage planner for the 2-hour run loop.

Goal: coverage accumulates across cron runs instead of every run spending quota
on the same already-covered matches.

For every inventory match we persist:
- line/odds sources already observed;
- context sources already observed;
- provider attempts and timestamps;
- remaining gap to 2+ odds sources and 2+ context sources.

Before provider calls the patch sorts match targets by the current gap, kickoff
urgency and provider usefulness. Providers therefore spend their limited quota on
matches that still need data, especially the nearest 4-hour window.
"""

import atexit
import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
STATE_PATH = DAY_INV_DIR / "progressive_coverage_state.json"
PLAN_PATH = EXPORT_DIR / "latest-progressive-coverage-plan.json"
STATE_EXPORT_PATH = EXPORT_DIR / "latest-progressive-coverage-state.json"
ARCHIVE_DIR = DAY_INV_DIR / "progressive_coverage_archive"

ODDS_PROVIDERS = {"odds_api_io", "bookies_api", "oddspapi", "allsportsapi", "sportlogic", "bzzoiro"}
CONTEXT_PROVIDERS = {"sstats", "bzzoiro", "api_football", "espn", "thesportsdb", "football_data", "openligadb", "futrixmetrics", "openfootball", "newsapi", "gnews", "sportlogic", "weather", "self_history"}
CORE_PROVIDERS = {"odds_api_io", "sstats", "bzzoiro"}

_INSTALLED = False
_RUNTIME_EVENTS: list[dict[str, Any]] = []
_LAST_STATE: dict[str, Any] | None = None


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_dt(value: Any) -> datetime | None:
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


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    if explicit:
        return explicit
    payload = _read_json(DAY_INV_DIR / "latest.json", {})
    if isinstance(payload, dict) and payload.get("date_local"):
        return str(payload.get("date_local"))
    return _now().date().isoformat()



def _app_tz() -> ZoneInfo | timezone:
    name = str(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return UTC


def _empty_state(date_local: str | None = None) -> dict[str, Any]:
    return {
        "version": "progressive_coverage_v2_date_scoped",
        "date_local": str(date_local or _target_date()),
        "matches": {},
        "runs": [],
        "reset_reason": "new_target_date_or_missing_state",
    }


def _row_local_date(row: dict[str, Any]) -> str | None:
    kickoff = _parse_dt(row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time"))
    if kickoff is None:
        return None
    try:
        return kickoff.astimezone(_app_tz()).date().isoformat()
    except Exception:
        return kickoff.date().isoformat()


def _prune_state_to_date(state: dict[str, Any], date_local: str) -> dict[str, Any]:
    matches = state.get("matches") if isinstance(state.get("matches"), dict) else {}
    kept: dict[str, Any] = {}
    dropped = 0
    for key, row in matches.items():
        if not isinstance(row, dict):
            dropped += 1
            continue
        row_date = str(row.get("date_local") or "").strip() or _row_local_date(row)
        # Rows without kickoff are not useful for a pre-match 2-hour lifecycle.
        if row_date == date_local:
            row["date_local"] = date_local
            kept[str(key)] = row
        else:
            dropped += 1
    state["matches"] = kept
    if dropped:
        state["date_scope_pruned_matches"] = int(state.get("date_scope_pruned_matches") or 0) + dropped
        state["last_date_scope_prune_at_utc"] = _now().isoformat()
    return state


def _archive_stale_state(payload: dict[str, Any], target_date: str) -> None:
    if not payload or not _truthy(os.getenv("PROGRESSIVE_COVERAGE_ARCHIVE_STALE_STATE"), True):
        return
    try:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        old_date = str(payload.get("date_local") or "unknown").replace("/", "-")
        stamp = _now().strftime("%Y%m%dT%H%M%SZ")
        path = ARCHIVE_DIR / f"{old_date}-to-{target_date}-{stamp}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _match_key(match: Any) -> str:
    try:
        key = str(getattr(match, "match_key", "") or "")
        if key:
            return key
    except Exception:
        pass
    if isinstance(match, dict):
        return str(match.get("match_key") or match.get("canonical_match_id") or "")
    return ""


def _match_meta(match: Any) -> dict[str, Any]:
    meta = getattr(match, "metadata", None)
    if isinstance(meta, dict):
        return dict(meta)
    if isinstance(match, dict) and isinstance(match.get("metadata"), dict):
        return dict(match.get("metadata") or {})
    return {}


def _match_home(match: Any) -> str:
    if isinstance(match, dict):
        return str(match.get("home_team") or "")
    return str(getattr(match, "home_team", "") or "")


def _match_away(match: Any) -> str:
    if isinstance(match, dict):
        return str(match.get("away_team") or "")
    return str(getattr(match, "away_team", "") or "")


def _match_kickoff(match: Any) -> datetime | None:
    if isinstance(match, dict):
        return _parse_dt(match.get("kickoff_utc") or match.get("commence_time") or match.get("start_time"))
    value = getattr(match, "commence_time", None)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return _parse_dt(value)


def _provider_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    if value in (None, ""):
        return tokens
    if isinstance(value, str):
        raw = value.replace(";", ",").replace("|", ",").split(",")
        tokens.update(x.strip().lower() for x in raw if x.strip())
    elif isinstance(value, dict):
        tokens.update(str(k).strip().lower() for k, v in value.items() if v not in (None, "", False, [], {}))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            tokens |= _provider_tokens(item)
    return {t for t in tokens if t}


def _sources_from_inventory_match(match: Any) -> tuple[set[str], set[str]]:
    meta = _match_meta(match)
    source_ids = meta.get("provider_source_ids") if isinstance(meta.get("provider_source_ids"), dict) else {}
    sources_seen = _provider_tokens(meta.get("sources_seen")) | _provider_tokens(source_ids)
    if isinstance(match, dict):
        sources_seen |= _provider_tokens(match.get("sources_seen")) | _provider_tokens(match.get("source_ids"))
        coverage = match.get("coverage") if isinstance(match.get("coverage"), dict) else {}
    else:
        sources_seen.add(str(getattr(match, "source", "") or "").strip().lower())
        coverage = {}
    # Inventory source id means fixture/context opportunity. It is only a line
    # source for odds_api_io because build-day-inventory does not fetch current
    # prices from Bzzoiro/SStats.
    odds_sources: set[str] = set()
    context_sources: set[str] = set()
    if "odds_api_io" in sources_seen or coverage.get("odds"):
        odds_sources.add("odds_api_io")
    for provider in sources_seen:
        if provider in CONTEXT_PROVIDERS:
            context_sources.add(provider)
    if meta.get("bzzoiro_has_context_hint"):
        context_sources.add("bzzoiro")
    if meta.get("sstats_has_context_hint"):
        context_sources.add("sstats")
    return odds_sources, context_sources


def _load_state() -> dict[str, Any]:
    target_date = _target_date()
    payload = _read_json(STATE_PATH, {})
    if not isinstance(payload, dict) or not payload:
        return _empty_state(target_date)
    state_date = str(payload.get("date_local") or "").strip()
    if state_date and state_date != target_date:
        _archive_stale_state(payload, target_date)
        payload = _empty_state(target_date)
        payload["reset_reason"] = f"target_date_changed:{state_date}->{target_date}"
    else:
        payload.setdefault("version", "progressive_coverage_v2_date_scoped")
        payload["date_local"] = target_date
        payload.setdefault("matches", {})
        payload.setdefault("runs", [])
    if not isinstance(payload.get("matches"), dict):
        payload["matches"] = {}
    if not isinstance(payload.get("runs"), list):
        payload["runs"] = []
    payload["runs"] = payload.get("runs", [])[-120:]
    return _prune_state_to_date(payload, target_date)


def _save_state(state: dict[str, Any]) -> None:
    global _LAST_STATE
    target_date = _target_date()
    state["date_local"] = target_date
    state = _prune_state_to_date(state, target_date)
    state["updated_at_utc"] = _now().isoformat()
    _LAST_STATE = state
    _write_json(STATE_PATH, state)
    _write_json(STATE_EXPORT_PATH, state)


def _entry_for(state: dict[str, Any], match: Any) -> dict[str, Any]:
    key = _match_key(match)
    rows = state.setdefault("matches", {})
    row = rows.setdefault(key, {})
    row.setdefault("match_key", key)
    row.setdefault("home_team", _match_home(match))
    row.setdefault("away_team", _match_away(match))
    kickoff = _match_kickoff(match)
    if kickoff is not None:
        row["kickoff_utc"] = kickoff.isoformat()
        try:
            row["date_local"] = kickoff.astimezone(_app_tz()).date().isoformat()
        except Exception:
            row["date_local"] = _target_date()
    inv_odds, inv_context = _sources_from_inventory_match(match)
    row.setdefault("odds_sources", [])
    row.setdefault("context_sources", [])
    row.setdefault("provider_attempts", {})
    row.setdefault("last_attempt_utc_by_provider", {})
    row.setdefault("last_success_utc_by_provider", {})
    row.setdefault("last_seen_utc", _now().isoformat())
    # Keep inventory hints as baseline, but never remove previously observed data.
    row["odds_sources"] = sorted(_provider_tokens(row.get("odds_sources")) | inv_odds)
    row["context_sources"] = sorted(_provider_tokens(row.get("context_sources")) | inv_context)
    return row


def _coverage_counts(row: dict[str, Any]) -> tuple[int, int]:
    return len(_provider_tokens(row.get("odds_sources"))), len(_provider_tokens(row.get("context_sources")))


def _window_score(match: Any, now: datetime) -> tuple[int, float]:
    kickoff = _match_kickoff(match)
    if kickoff is None:
        return (0, 999999.0)
    hours = (kickoff - now).total_seconds() / 3600.0
    if hours < 0:
        return (-20, abs(hours))
    window_hours = max(1, _to_int(os.getenv("CORE_COVERAGE_WINDOW_HOURS") or 4, 4))
    if hours <= window_hours:
        return (120, hours)
    if hours <= window_hours * 2:
        return (90, hours)
    if hours <= 12:
        return (60, hours)
    if hours <= 24:
        return (25, hours)
    return (5, hours)


def _stale_bonus(row: dict[str, Any], provider: str, now: datetime) -> float:
    retry_min = max(10, _to_int(os.getenv("PROGRESSIVE_COVERAGE_PROVIDER_RETRY_MINUTES") or 90, 90))
    last = _parse_dt((row.get("last_attempt_utc_by_provider") or {}).get(provider))
    if last is None:
        return 16.0
    age = (now - last).total_seconds() / 60.0
    if age >= retry_min:
        return min(16.0, age / retry_min * 8.0)
    return -20.0


def _priority_for(match: Any, row: dict[str, Any], provider: str, method_name: str, now: datetime) -> float:
    min_odds = max(1, _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_ODDS_SOURCES") or 2, 2))
    min_context = max(1, _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_CONTEXT_SOURCES") or 2, 2))
    odds_count, context_count = _coverage_counts(row)
    window, hours = _window_score(match, now)
    score = float(window)
    provider = str(provider or "").lower()
    method_name = str(method_name or "").lower()
    if method_name == "fetch_offers":
        deficit = max(0, min_odds - odds_count)
        score += deficit * 80
        if provider not in _provider_tokens(row.get("odds_sources")):
            score += 35
        else:
            score -= 30
        if provider == "odds_api_io" and odds_count <= 0:
            score += 25
        if provider in {"sportlogic", "bzzoiro", "allsportsapi", "oddspapi"} and odds_count == 1:
            score += 30
    elif method_name == "fetch_context":
        deficit = max(0, min_context - context_count)
        score += deficit * 75
        if provider not in _provider_tokens(row.get("context_sources")):
            score += 35
        else:
            score -= 25
        if provider in {"sstats", "bzzoiro"} and context_count < min_context:
            score += 25
    score += _stale_bonus(row, provider, now)
    if 0 <= hours <= 2.5:
        score += 45
    elif 0 <= hours <= 4:
        score += 28
    row["coverage_priority_last"] = round(score, 3)
    row["coverage_gap"] = {
        "odds_sources": odds_count,
        "context_sources": context_count,
        "odds_needed": max(0, min_odds - odds_count),
        "context_needed": max(0, min_context - context_count),
    }
    return score


def _sort_matches_for_provider(matches: Iterable[Any], provider: str, method_name: str) -> list[Any]:
    if not _truthy(os.getenv("PROGRESSIVE_COVERAGE_ENABLED"), True):
        return list(matches or [])
    state = _load_state()
    now = _now()
    rows: list[tuple[float, float, Any]] = []
    for match in list(matches or []):
        key = _match_key(match)
        if not key:
            continue
        row = _entry_for(state, match)
        score = _priority_for(match, row, provider, method_name, now)
        _, hours = _window_score(match, now)
        rows.append((score, -hours, match))
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    sorted_matches = [item[2] for item in rows]
    limit = _to_int(os.getenv("PROGRESSIVE_COVERAGE_DEBUG_SAMPLE_LIMIT") or 40, 40)
    event = {
        "created_at_utc": now.isoformat(),
        "stage": "target_sort",
        "provider": provider,
        "method": method_name,
        "input_matches": len(list(matches or [])) if not isinstance(matches, list) else len(matches),
        "output_matches": len(sorted_matches),
        "top_gap_sample": [
            {
                "match_key": _match_key(match),
                "home_team": _match_home(match),
                "away_team": _match_away(match),
                "kickoff_utc": _match_kickoff(match).isoformat() if _match_kickoff(match) else None,
                "priority": round(score, 3),
                "gap": (state.get("matches", {}).get(_match_key(match), {}) or {}).get("coverage_gap"),
            }
            for score, _, match in rows[:limit]
        ],
    }
    _RUNTIME_EVENTS.append(event)
    _save_state(state)
    _write_plan_report()
    return sorted_matches


def _mark_attempts(matches: Iterable[Any], provider: str, method_name: str) -> None:
    state = _load_state()
    now = _now().isoformat()
    provider = str(provider or "unknown").lower()
    for match in list(matches or []):
        key = _match_key(match)
        if not key:
            continue
        row = _entry_for(state, match)
        attempts = row.setdefault("provider_attempts", {})
        attempts[provider] = _to_int(attempts.get(provider), 0) + 1
        row.setdefault("last_attempt_utc_by_provider", {})[provider] = now
        row["last_attempt_method"] = method_name
    _save_state(state)


def _iter_map_keys(data: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(data, dict):
        for key, value in data.items():
            yield str(key), value


def _value_has_data(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _record_provider_success(data: Any, provider: str, method_name: str, stats: Any | None = None) -> None:
    provider = str(provider or "unknown").lower()
    method_name = str(method_name or "").lower()
    if method_name not in {"fetch_offers", "fetch_context"}:
        return
    state = _load_state()
    now = _now().isoformat()
    successes = 0
    for key, value in _iter_map_keys(data):
        if not key or not _value_has_data(value):
            continue
        row = state.setdefault("matches", {}).setdefault(key, {"match_key": key})
        if method_name == "fetch_offers":
            sources = _provider_tokens(row.get("odds_sources"))
            sources.add(provider)
            # Also count embedded Bzzoiro offers if a bridge placed them into the
            # offer list with Offer.source='bzzoiro'.
            if isinstance(value, list):
                for item in value[:200]:
                    src = getattr(item, "source", None) if not isinstance(item, dict) else item.get("source")
                    if src:
                        sources.add(str(src).strip().lower())
            row["odds_sources"] = sorted(sources)
        elif method_name == "fetch_context":
            sources = _provider_tokens(row.get("context_sources"))
            sources.add(provider)
            if isinstance(value, list):
                for item in value[:50]:
                    src = getattr(item, "source", None) if not isinstance(item, dict) else item.get("source")
                    if src:
                        sources.add(str(src).strip().lower())
            else:
                src = getattr(value, "source", None) if not isinstance(value, dict) else value.get("source")
                if src:
                    sources.add(str(src).strip().lower())
            row["context_sources"] = sorted(sources)
        row.setdefault("last_success_utc_by_provider", {})[provider] = now
        row["last_success_method"] = method_name
        odds_count, context_count = _coverage_counts(row)
        row["coverage_gap"] = {
            "odds_sources": odds_count,
            "context_sources": context_count,
            "odds_needed": max(0, _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_ODDS_SOURCES") or 2, 2) - odds_count),
            "context_needed": max(0, _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_CONTEXT_SOURCES") or 2, 2) - context_count),
        }
        successes += 1
    state.setdefault("runs", []).append({
        "created_at_utc": now,
        "provider": provider,
        "method": method_name,
        "matches_with_data": successes,
        "stats": {k: v for k, v in dict(stats or {}).items() if k not in {"last_body_preview"}} if isinstance(stats, dict) else {},
    })
    state["runs"] = state.get("runs", [])[-120:]
    _save_state(state)
    _write_plan_report()


def _write_plan_report() -> None:
    state = _load_state()
    matches = state.get("matches") if isinstance(state.get("matches"), dict) else {}
    min_odds = _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_ODDS_SOURCES") or 2, 2)
    min_context = _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_CONTEXT_SOURCES") or 2, 2)
    counts = Counter()
    gap_rows = []
    now = _now()
    for key, row in matches.items():
        if not isinstance(row, dict):
            continue
        odds_count, context_count = _coverage_counts(row)
        counts["matches_tracked"] += 1
        counts["odds_1plus"] += int(odds_count >= 1)
        counts["odds_2plus"] += int(odds_count >= min_odds)
        counts["context_1plus"] += int(context_count >= 1)
        counts["context_2plus"] += int(context_count >= min_context)
        counts["ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
        kickoff = _parse_dt(row.get("kickoff_utc"))
        hours = (kickoff - now).total_seconds() / 3600.0 if kickoff else None
        if hours is not None and 0 <= hours <= 4:
            counts["window_0_4h"] += 1
            counts["window_0_4h_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
        if hours is not None and 0 <= hours <= 12:
            counts["window_0_12h"] += 1
            counts["window_0_12h_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
        if odds_count < min_odds or context_count < min_context:
            gap_rows.append({
                "match_key": key,
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "kickoff_utc": row.get("kickoff_utc"),
                "odds_sources": sorted(_provider_tokens(row.get("odds_sources"))),
                "context_sources": sorted(_provider_tokens(row.get("context_sources"))),
                "odds_needed": max(0, min_odds - odds_count),
                "context_needed": max(0, min_context - context_count),
                "hours_to_kickoff": round(hours, 2) if hours is not None else None,
            })
    gap_rows.sort(key=lambda row: (row.get("hours_to_kickoff") is None, row.get("hours_to_kickoff") or 999999, -row.get("odds_needed", 0), -row.get("context_needed", 0)))
    report = {
        "created_at_utc": now.isoformat(),
        "enabled": _truthy(os.getenv("PROGRESSIVE_COVERAGE_ENABLED"), True),
        "min_odds_sources": min_odds,
        "min_context_sources": min_context,
        "counts": dict(counts),
        "gap_sample": gap_rows[:80],
        "runtime_events": _RUNTIME_EVENTS[-40:],
        "state_path": str(STATE_PATH),
    }
    _write_json(PLAN_PATH, report)


def _sync_inventory_rows_from_state() -> None:
    state = _load_state()
    matches = state.get("matches") if isinstance(state.get("matches"), dict) else {}
    if not matches:
        return
    target_date = _target_date()
    state = _prune_state_to_date(state, target_date)
    for path in [DAY_INV_DIR / "latest.json", DAY_INV_DIR / "current.json", DAY_INV_DIR / "today.json", DAY_INV_DIR / f"{target_date}.json"]:
        payload = _read_json(path, None)
        if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
            continue
        changed = False
        for row in payload["matches"]:
            if not isinstance(row, dict):
                continue
            key = str(row.get("match_key") or "")
            st = matches.get(key)
            if not isinstance(st, dict):
                continue
            odds_sources = sorted(_provider_tokens(st.get("odds_sources")))
            context_sources = sorted(_provider_tokens(st.get("context_sources")))
            coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
            coverage.update({
                "odds": len(odds_sources) >= 1,
                "context": len(context_sources) >= 1,
                "odds_sources": odds_sources,
                "context_sources": context_sources,
                "odds_sources_count": len(odds_sources),
                "context_sources_count": len(context_sources),
                "ready_for_model": len(odds_sources) >= 1 and len(context_sources) >= 1,
                "ready_for_publish_coverage": len(odds_sources) >= 2 and len(context_sources) >= 2,
            })
            row["coverage"] = coverage
            row["progressive_coverage"] = {
                "odds_sources": odds_sources,
                "context_sources": context_sources,
                "coverage_gap": st.get("coverage_gap") or {},
                "last_success_utc_by_provider": st.get("last_success_utc_by_provider") or {},
                "provider_attempts": st.get("provider_attempts") or {},
            }
            changed = True
        if changed:
            payload["progressive_coverage_updated_at_utc"] = _now().isoformat()
            _write_json(path, payload)


def _atexit_flush() -> None:
    try:
        _sync_inventory_rows_from_state()
        _write_plan_report()
    except Exception:
        pass


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    os.environ.setdefault("PROGRESSIVE_COVERAGE_ENABLED", "true")
    os.environ.setdefault("PROGRESSIVE_COVERAGE_MIN_ODDS_SOURCES", "2")
    os.environ.setdefault("PROGRESSIVE_COVERAGE_MIN_CONTEXT_SOURCES", "2")
    os.environ.setdefault("PROGRESSIVE_COVERAGE_PROVIDER_RETRY_MINUTES", "90")
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {"status": "error", "error": f"import_runner:{type(exc).__name__}: {exc}"}

    original_fetch_provider = PredictionRunner._fetch_provider
    if getattr(original_fetch_provider, "_harizon_progressive_coverage", False):
        _INSTALLED = True
        return {"status": "already_wrapped"}

    async def fetch_provider_progressive(self, provider, method_name, matches, *args, **kwargs):  # type: ignore[no-untyped-def]
        provider_name = "unknown"
        try:
            provider_name = self._provider_name(provider) if provider is not None else "none"
        except Exception:
            provider_name = "unknown"
        method = str(method_name or "")
        sorted_matches = _sort_matches_for_provider(list(matches or []), provider_name, method) if method in {"fetch_offers", "fetch_context"} else list(matches or [])
        if method in {"fetch_offers", "fetch_context"}:
            _mark_attempts(sorted_matches, provider_name, method)
        result = await original_fetch_provider(self, provider, method_name, sorted_matches, *args, **kwargs)
        try:
            data = result[0] if isinstance(result, tuple) and len(result) >= 1 else None
            stats = result[1] if isinstance(result, tuple) and len(result) >= 2 else None
            _record_provider_success(data, provider_name, method, stats)
        except Exception:
            pass
        return result

    fetch_provider_progressive._harizon_progressive_coverage = True  # type: ignore[attr-defined]
    PredictionRunner._fetch_provider = fetch_provider_progressive  # type: ignore[assignment]
    atexit.register(_atexit_flush)
    _INSTALLED = True
    _write_plan_report()
    return {"status": "installed", "state_path": str(STATE_PATH), "plan_path": str(PLAN_PATH)}
