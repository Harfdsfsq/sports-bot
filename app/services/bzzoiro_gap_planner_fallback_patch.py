from __future__ import annotations

"""Feed Bzzoiro v2 with real A-tier gap targets when progressive plan is absent.

The source-matrix patch originally looks for latest-progressive-coverage-plan.json.
When that file is absent, this patch falls back to live day inventory and sends
near-future matches that are one Bzzoiro odds source away from A-cover.

It also matches inventory rows to runtime Match objects by conservative aliases
(date|home|away) and applies the same alias logic to Bzzoiro relaxed event
matching.  Without that, run 28185273795 appended alias targets but Bzzoiro still
used exact match_key checks before relaxed matching.
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / ".data" / "exports"
DAY_INV = ROOT / ".data" / "day_inventory"
REPORT = EXPORT / "latest-bzzoiro-gap-planner-fallback-patch.json"
_INSTALLED = False
_ORIGINAL_GAP_ROWS = None
_ORIGINAL_APPEND_GAP_TARGETS = None
_ORIGINAL_MATRIX_MATCH_EVENT_PATCH = None


def _truthy(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force", "y"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
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


def _raise_env_int(name: str, minimum: int) -> None:
    if _to_int(os.getenv(name), 0) < minimum:
        os.environ[name] = str(minimum)


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _date_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    raw = str(value)
    m = re.search(r"20\d{2}-\d{2}-\d{2}", raw)
    if m:
        return m.group(0)
    try:
        iso = value.isoformat()  # type: ignore[attr-defined]
        m = re.search(r"20\d{2}-\d{2}-\d{2}", iso)
        if m:
            return m.group(0)
    except Exception:
        pass
    return ""


def _row_date(row: dict[str, Any]) -> str:
    for key in ("date", "kickoff_utc", "commence_time", "start_time", "kickoff", "event_date", "match_key", "canonical_match_id"):
        date = _date_text(row.get(key))
        if date:
            return date
    return ""


def _match_date(match: Any) -> str:
    for attr in ("commence_time", "kickoff_utc", "start_time", "kickoff", "event_date", "match_key"):
        date = _date_text(getattr(match, attr, None))
        if date:
            return date
    return ""


def _row_aliases(row: dict[str, Any]) -> set[str]:
    out = {_norm(row.get("match_key")), _norm(row.get("canonical_match_id")), str(row.get("match_key") or "").strip(), str(row.get("canonical_match_id") or "").strip()}
    date = _row_date(row)
    home = _norm(row.get("home_team") or row.get("home") or row.get("home_name"))
    away = _norm(row.get("away_team") or row.get("away") or row.get("away_name"))
    if date and home and away:
        out.update({f"{date}|{home}|{away}", f"{date}|{away}|{home}", f"soccer|{home}|{away}|{date}", f"soccer|{away}|{home}|{date}"})
    return {x for x in out if x and x.strip("|")}


def _match_aliases(match: Any) -> set[str]:
    key = str(getattr(match, "match_key", "") or "").strip()
    out = {key, _norm(key)}
    date = _match_date(match)
    home = _norm(getattr(match, "home_team", ""))
    away = _norm(getattr(match, "away_team", ""))
    if date and home and away:
        out.update({f"{date}|{home}|{away}", f"{date}|{away}|{home}", f"soccer|{home}|{away}|{date}", f"soccer|{away}|{home}|{date}"})
    return {x for x in out if x and x.strip("|")}


def _sources(row: dict[str, Any], *keys: str) -> set[str]:
    out: set[str] = set()
    containers = [row]
    for key in ("coverage", "metadata", "source_summary"):
        val = row.get(key)
        if isinstance(val, dict):
            containers.append(val)
    for container in containers:
        for key in keys:
            val = container.get(key)
            if isinstance(val, str):
                out.update(x.strip().lower() for x in re.split(r"[,|;/]+", val) if x.strip())
            elif isinstance(val, (list, tuple, set)):
                out.update(str(x).strip().lower() for x in val if str(x).strip())
    return out


def _count(row: dict[str, Any], *keys: str) -> int:
    best = 0
    containers = [row]
    for key in ("coverage", "metadata", "source_summary"):
        val = row.get(key)
        if isinstance(val, dict):
            containers.append(val)
    for container in containers:
        for key in keys:
            best = max(best, _to_int(container.get(key), 0))
    return best


def _dt(row: dict[str, Any]) -> datetime | None:
    for key in ("kickoff_utc", "commence_time", "start_time", "kickoff", "event_date"):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            text = str(raw).strip().replace(" ", "T")
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            if "T" in text and "+" not in text and text.count("-") >= 2:
                text += "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            continue
    return None


def _future(row: dict[str, Any]) -> bool:
    kickoff = _dt(row)
    return True if kickoff is None else (kickoff - datetime.now(UTC)).total_seconds() >= -240


def _hours_to_kickoff(row: dict[str, Any]) -> float:
    kickoff = _dt(row)
    return 999.0 if kickoff is None else (kickoff - datetime.now(UTC)).total_seconds() / 3600.0


def _inventory_rows() -> list[dict[str, Any]]:
    for path in (DAY_INV / "latest.json", DAY_INV / "current.json", DAY_INV / "today.json"):
        payload = _read_json(path, {})
        rows = payload.get("matches") if isinstance(payload, dict) else payload if isinstance(payload, list) else None
        if isinstance(rows, list) and rows:
            return [dict(x) for x in rows if isinstance(x, dict)]
    return []


def _planner_rows() -> list[dict[str, Any]]:
    payload = _read_json(EXPORT / "latest-coverage-planner.json", {})
    rows = payload.get("matches") if isinstance(payload, dict) else None
    return [dict(x) for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []


def _is_gap_target(row: dict[str, Any]) -> bool:
    if not _future(row):
        return False
    odds_sources = _sources(row, "odds_sources", "line_sources", "core_odds_sources")
    context_sources = _sources(row, "context_sources", "context_confirmations", "core_context_sources")
    has_bzz = "bzzoiro" in odds_sources or "bzzoiro_v2" in odds_sources
    odds_count = max(len(odds_sources), _count(row, "odds_sources_count", "independent_odds_sources_count", "line_sources_count", "core_odds_source_count", "odds_source_count"))
    book_count = max(len(_sources(row, "books", "bookmakers")), _count(row, "books_count", "bookmaker_count", "price_confirmation_sources_count", "bookmakers_count"))
    ctx_count = max(len(context_sources), _count(row, "context_sources_count", "confirmation_sources_count", "core_context_source_count", "context_source_count"))
    if not has_bzz and odds_count < 2 and book_count >= 2 and ctx_count >= 1:
        return True
    if not has_bzz and odds_count <= 1 and book_count >= 1 and _hours_to_kickoff(row) <= _to_float(os.getenv("BZZOIRO_V2_PLANNER_FALLBACK_MAX_HOURS"), 24.0):
        return True
    return False


def _fallback_gap_rows() -> list[dict[str, Any]]:
    limit = max(1, _to_int(os.getenv("BZZOIRO_V2_PLANNER_FALLBACK_TARGET_LIMIT"), 180))
    rows = _inventory_rows()
    source = "day_inventory"
    if not rows:
        rows = _planner_rows()
        source = "coverage_planner"
    candidates = [row for row in rows if _is_gap_target(row)]
    candidates.sort(key=lambda r: (_hours_to_kickoff(r), -_count(r, "books_count", "bookmaker_count", "price_confirmation_sources_count")))
    out = candidates[:limit]
    _write_report({"status": "ok", "created_at_utc": datetime.now(UTC).isoformat(), "source": source if rows else "none", "rows_seen": len(rows), "gap_candidates": len(candidates), "returned": len(out), "limit": limit, "sample": [{"match_key": row.get("match_key") or row.get("canonical_match_id"), "home_team": row.get("home_team") or row.get("home"), "away_team": row.get("away_team") or row.get("away"), "kickoff_utc": row.get("kickoff_utc") or row.get("commence_time"), "odds_sources": sorted(_sources(row, "odds_sources", "line_sources")), "books_count": _count(row, "books_count", "price_confirmation_sources_count"), "context_sources_count": _count(row, "context_sources_count", "confirmation_sources_count")} for row in out[:20]]})
    return out


def _gap_alias_index() -> tuple[list[dict[str, Any]], set[str], dict[str, list[dict[str, Any]]]]:
    try:
        from app.services import bzzoiro_v2_source_matrix_runtime_patch as matrix
        rows = [dict(x) for x in (matrix._gap_rows() or []) if isinstance(x, dict)]  # type: ignore[attr-defined]
    except Exception:
        rows = _fallback_gap_rows()
    exact: set[str] = set()
    alias_to_rows: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
        if key:
            exact.add(key)
        for alias in _row_aliases(row):
            alias_to_rows.setdefault(alias, []).append(row)
    return rows, exact, alias_to_rows


def _is_gap_match_alias(match: Any) -> bool:
    key = str(getattr(match, "match_key", "") or "").strip()
    _, exact, alias_to_rows = _gap_alias_index()
    return bool(key in exact or any(alias in alias_to_rows for alias in _match_aliases(match)))


def _alias_append_gap_targets(base: list[Any], candidates: list[Any], *, limit: int) -> tuple[list[Any], dict[str, Any]]:
    rows, exact_keys, alias_to_rows = _gap_alias_index()
    selected = list(base or [])
    seen = {getattr(m, "match_key", "") for m in selected if getattr(m, "match_key", "")}
    appended: list[Any] = []
    exact_hits = 0
    alias_hits = 0
    for match in list(candidates or []):
        key = str(getattr(match, "match_key", "") or "").strip()
        if not key or key in seen or getattr(match, "sport_key", "") != "soccer":
            continue
        aliases = _match_aliases(match)
        is_exact = key in exact_keys
        if not is_exact and not any(alias in alias_to_rows for alias in aliases):
            continue
        selected.append(match)
        appended.append(match)
        seen.add(key)
        exact_hits += int(is_exact)
        alias_hits += int(not is_exact)
        if limit > 0 and len(selected) >= limit:
            break
    return selected, {"gap_rows": len(rows), "gap_aliases": len(alias_to_rows), "selected_before": len(base or []), "candidate_pool": len(candidates or []), "appended": len(appended), "exact_hits": exact_hits, "alias_hits": alias_hits, "selected_after": len(selected), "limit": limit, "sample": [getattr(m, "match_key", "") for m in appended[:25]], "policy": "alias_match_gap_targets"}


def _alias_relaxed_match_event(self: Any, match: Any, events: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
    try:
        from app.services import bzzoiro_v2_source_matrix_runtime_patch as matrix
        original = getattr(matrix, "_ORIGINAL_V2_MATCH_EVENT", None)
    except Exception:
        original = None
    if callable(original):
        try:
            event, quality, score, diag = original(self, match, events)
            if event is not None or not _truthy(os.getenv("BZZOIRO_V2_GAP_RELAXED_MATCH_ENABLED"), True):
                return event, quality, score, diag
        except Exception:
            diag = None
    else:
        diag = None
    if not _is_gap_match_alias(match):
        if callable(original):
            return original(self, match, events)
        return None, None, 0.0, diag
    try:
        from app.utils import score_event_match_variants, parse_datetime, leagues_related
    except Exception:
        if callable(original):
            return original(self, match, events)
        return None, None, 0.0, diag
    best = None
    best_quality = None
    best_score = 0.0
    best_diag = diag if isinstance(diag, dict) else None
    for candidate in events or []:
        if not isinstance(candidate, dict):
            continue
        try:
            home_candidates, away_candidates = self._team_candidates(candidate)
        except Exception:
            home_candidates, away_candidates = [], []
        if not home_candidates or not away_candidates:
            continue
        try:
            start = parse_datetime(candidate.get("event_date") or candidate.get("start_time") or candidate.get("commence_time"))
        except Exception:
            continue
        try:
            league = self._league_name(candidate)
        except Exception:
            league = ""
        try:
            score, quality, _, _ = score_event_match_variants(
                sport="soccer",
                match_home=getattr(match, "home_team", ""),
                match_away=getattr(match, "away_team", ""),
                match_start=getattr(match, "commence_time", None),
                match_league=getattr(match, "league_name", ""),
                event_home_candidates=home_candidates,
                event_away_candidates=away_candidates,
                event_start=start,
                event_league=league,
                exact_tolerance_hours=_to_float(os.getenv("BZZOIRO_V2_GAP_EXACT_TOLERANCE_HOURS"), 8.0),
                fuzzy_tolerance_hours=_to_float(os.getenv("BZZOIRO_V2_GAP_FUZZY_TOLERANCE_HOURS"), 30.0),
            )
        except Exception:
            continue
        if score > best_score:
            best, best_quality, best_score = candidate, quality, score
            best_diag = {"score": round(float(score or 0.0), 2), "quality": quality, "league": league, "home": home_candidates[:3], "away": away_candidates[:3], "start": getattr(start, "isoformat", lambda: "")(), "relaxed_gap_match": True, "alias_gap_match": True}
    if best is None or best_quality is None:
        return None, None, 0.0, best_diag
    min_exact = _to_float(os.getenv("BZZOIRO_V2_GAP_MIN_EXACT_SCORE"), 58.0)
    min_loose = _to_float(os.getenv("BZZOIRO_V2_GAP_MIN_LOOSE_SCORE"), 52.0)
    min_fuzzy = _to_float(os.getenv("BZZOIRO_V2_GAP_MIN_FUZZY_SCORE"), 52.0)
    min_score = min_fuzzy if best_quality == "fuzzy" else min_loose if best_quality == "loose" else min_exact
    try:
        event_league = self._league_name(best)
        if best_quality == "fuzzy" and event_league and not leagues_related(getattr(match, "league_name", ""), event_league):
            min_score += _to_float(os.getenv("BZZOIRO_V2_GAP_LEAGUE_MISMATCH_PENALTY"), 4.0)
        if not event_league:
            min_score += _to_float(os.getenv("BZZOIRO_V2_GAP_EMPTY_LEAGUE_PENALTY"), 2.0)
    except Exception:
        pass
    if best_score < min_score:
        if isinstance(best_diag, dict):
            best_diag.update({"accepted": False, "rejection_reason": "alias_relaxed_low_score", "required_score": min_score})
        return None, None, 0.0, best_diag
    if isinstance(best_diag, dict):
        best_diag.update({"accepted": True, "required_score": min_score})
    return best, best_quality, best_score, best_diag


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_GAP_ROWS, _ORIGINAL_APPEND_GAP_TARGETS, _ORIGINAL_MATRIX_MATCH_EVENT_PATCH
    if _INSTALLED:
        return {"status": "already_installed"}
    _raise_env_int("BZZOIRO_V2_SOURCE_MATRIX_TARGET_LIMIT", _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT"), 220))
    _raise_env_int("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT", 220)
    _raise_env_int("BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT", 90)
    _raise_env_int("BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS", _to_int(os.getenv("BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT"), 90))
    try:
        from app.services import bzzoiro_v2_source_matrix_runtime_patch as matrix
    except Exception as exc:
        payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        _write_report(payload)
        return payload
    _ORIGINAL_GAP_ROWS = getattr(matrix, "_gap_rows", None)
    _ORIGINAL_APPEND_GAP_TARGETS = getattr(matrix, "_append_gap_targets", None)
    _ORIGINAL_MATRIX_MATCH_EVENT_PATCH = getattr(matrix, "_patched_v2_match_event", None)

    def gap_rows_with_inventory_fallback() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if callable(_ORIGINAL_GAP_ROWS):
            try:
                rows = [dict(x) for x in (_ORIGINAL_GAP_ROWS() or []) if isinstance(x, dict)]
            except Exception:
                rows = []
        if rows:
            _write_report({"status": "original_plan_used", "created_at_utc": datetime.now(UTC).isoformat(), "returned": len(rows)})
            return rows
        return _fallback_gap_rows()

    _alias_relaxed_match_event._harizon_bzzoiro_v2_gap_relaxed_match = True  # type: ignore[attr-defined]
    matrix._gap_rows = gap_rows_with_inventory_fallback  # type: ignore[attr-defined]
    matrix._append_gap_targets = _alias_append_gap_targets  # type: ignore[attr-defined]
    matrix._patched_v2_match_event = _alias_relaxed_match_event  # type: ignore[attr-defined]
    _INSTALLED = True
    payload = {"status": "installed", "created_at_utc": datetime.now(UTC).isoformat(), "policy": "fallback Bzzoiro v2 gap targets from day inventory; alias append and alias relaxed event matching enabled", "target_limit": os.getenv("BZZOIRO_V2_SOURCE_MATRIX_TARGET_LIMIT"), "comparison_limit": os.getenv("BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT")}
    _write_report(payload)
    return payload
