"""Bzzoiro context-gap finalizer.

The v8 reports showed a precise bottleneck: core odds 2+ is healthy, while core
context 2+ stays low because many matches have SStats context but no Bzzoiro
context. The first gap pass used only Bzzoiro v2 /events/ and fetched too small a
universe for matching. This version uses both:
- v1 /events/ and /predictions/ for broad daily coverage;
- v2 /events/ + subresources for stats/metadata/lineups/odds hints.

It does not relax publication guards. It only increases Bzzoiro context coverage
for upcoming progressive gaps.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx

from app.schemas import Match, MatchContext

ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
PLAN_PATH = EXPORT_DIR / "latest-progressive-coverage-plan.json"
RUNTIME_REPORT_PATH = EXPORT_DIR / "latest-bzzoiro-context-gap-finalizer.json"
INSTALL_REPORT_PATH = EXPORT_DIR / "latest-bzzoiro-context-gap-finalizer-install.json"


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _truthy(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(str(value).replace(",", "."))
        if number == number and abs(number) < 1e9:
            return number
    except Exception:
        return None
    return None


def _match_key(match: Match) -> str:
    try:
        return str(match.match_key or "")
    except Exception:
        return ""


def _has_bzzoiro_context(ctx: MatchContext | None) -> bool:
    if ctx is None:
        return False
    if str(getattr(ctx, "source", "") or "").lower() == "bzzoiro":
        return True
    details = getattr(ctx, "details", None)
    if isinstance(details, dict):
        tokens = details.get("source_tokens") or details.get("context_sources") or []
        if isinstance(tokens, str):
            tokens = [tokens]
        if any(str(x).strip().lower() == "bzzoiro" for x in tokens if x is not None):
            return True
    payload = getattr(ctx, "payload", None)
    if isinstance(payload, dict):
        provider = str(payload.get("provider") or payload.get("source") or "").lower()
        if "bzzoiro" in provider:
            return True
    return False


def _gap_keys() -> set[str]:
    plan = _read_json(PLAN_PATH)
    rows = plan.get("core_gap_sample") or plan.get("gap_sample") or []
    keys: set[str] = set()
    if not isinstance(rows, list):
        return keys
    for row in rows:
        if not isinstance(row, dict):
            continue
        hours = _to_float(row.get("hours_to_kickoff"))
        if hours is not None and hours < 0:
            continue
        if _to_int(row.get("core_context_needed") or row.get("context_needed"), 0) <= 0:
            continue
        key = str(row.get("match_key") or "").strip()
        if key:
            keys.add(key)
    return keys


def _source_ids(match: Match) -> dict[str, Any]:
    meta = getattr(match, "metadata", None)
    if isinstance(meta, dict):
        for key in ("provider_source_ids", "source_ids", "provider_ids"):
            value = meta.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _bzzoiro_id_from_match(match: Match) -> str | None:
    ids = _source_ids(match)
    for key in ("bzzoiro_v2", "bsd_v2", "bzzoiro", "bsd", "bzzoiro_v1"):
        value = ids.get(key)
        if value not in (None, ""):
            if isinstance(value, dict):
                for nested in ("id", "event_id", "source_event_id"):
                    if value.get(nested) not in (None, ""):
                        return str(value.get(nested))
            elif isinstance(value, (list, tuple)) and value:
                return str(value[0])
            else:
                return str(value)
    raw = getattr(match, "source_event_id", None)
    if str(getattr(match, "source", "")).lower() in {"bzzoiro", "bsd"} and raw not in (None, ""):
        return str(raw)
    return None


def _extract_starting_count(lineups: Any, side: str) -> int | None:
    if not isinstance(lineups, dict):
        return None
    root = lineups.get("lineups") if isinstance(lineups.get("lineups"), dict) else lineups
    team = root.get(side) if isinstance(root, dict) and isinstance(root.get(side), dict) else None
    if not isinstance(team, dict):
        return None
    players = team.get("players") or team.get("starters") or []
    if isinstance(players, list):
        return len(players) or None
    return None


def _context_from_resources(event: dict[str, Any], resources: dict[str, Any], score: float, quality: str | None) -> MatchContext:
    from app.services import windowed_core_coverage_runtime_patch as wc

    home_xg, away_xg = wc._extract_bzzoiro_xg(resources)
    if home_xg is not None:
        home_xg = round(max(0.05, min(4.5, float(home_xg))), 3)
    if away_xg is not None:
        away_xg = round(max(0.05, min(4.5, float(away_xg))), 3)
    odds_hints = wc._bzzoiro_odds_hints(resources)
    odds_payload = resources.get("odds") if isinstance(resources.get("odds"), dict) else {}
    odds = odds_payload.get("odds") if isinstance(odds_payload.get("odds"), dict) else odds_payload
    home_prob = away_prob = None
    if isinstance(odds, dict):
        home = _to_float(odds.get("home_win"))
        draw = _to_float(odds.get("draw"))
        away = _to_float(odds.get("away_win"))
        inv = [(1 / p) for p in (home, draw, away) if p and p > 1.0]
        total = sum(inv)
        if home and home > 1.0 and total > 0:
            home_prob = (1 / home) / total
        if away and away > 1.0 and total > 0:
            away_prob = (1 / away) / total
    confidence = 58.0
    if home_xg is not None or away_xg is not None:
        confidence += 5.0
    if odds_hints:
        confidence += 3.0
    if quality == "fuzzy":
        confidence -= 4.0
    confidence = max(50.0, min(72.0, confidence))
    return MatchContext(
        source="bzzoiro",
        payload={
            "event": event,
            "resources": resources,
            "odds_hints": odds_hints,
            "provider": "bzzoiro_context_gap_pass",
        },
        expected_home=home_xg,
        expected_away=away_xg,
        home_win_probability=home_prob,
        away_win_probability=away_prob,
        home_starting=_extract_starting_count(resources.get("lineups"), "home"),
        away_starting=_extract_starting_count(resources.get("lineups"), "away"),
        confidence=confidence,
        details={
            "bzzoiro_context_gap_pass": True,
            "bzzoiro_v2_event_id": event.get("id") or event.get("event_id"),
            "bzzoiro_match_score": round(score, 3),
            "bzzoiro_match_quality": quality,
            "bzzoiro_has_stats": isinstance(resources.get("stats"), dict),
            "bzzoiro_has_metadata": isinstance(resources.get("metadata"), dict),
            "bzzoiro_has_lineups": isinstance(resources.get("lineups"), dict),
            "bzzoiro_has_odds": isinstance(resources.get("odds"), dict),
            "bzzoiro_odds_hint_count": len(odds_hints),
            "source_tokens": ["bzzoiro"],
        },
    )





def _norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    stop = {
        "fc", "cf", "sc", "fk", "ac", "cd", "club", "de", "la", "the", "team",
        "women", "u19", "u20", "u21", "u23", "ii", "2", "b",
    }
    tokens = [tok for tok in text.split() if tok and tok not in stop]
    return " ".join(tokens)


def _event_name(row: dict[str, Any], side: str) -> str:
    prefixes = [side, f"{side}_team", f"{side}Team"]
    for key in prefixes + (["домашняя команда"] if side == "home" else []):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nested in ("name", "team_name", "short_name", "display_name", "title"):
                if value.get(nested):
                    return str(value.get(nested)).strip()
    for key in (f"{side}_team_name", f"{side}_name", f"{side}Name"):
        if row.get(key):
            return str(row.get(key)).strip()
    return ""


def _event_start(row: dict[str, Any]) -> datetime | None:
    values = [
        row.get("event_date"), row.get("start_time"), row.get("commence_time"),
        row.get("kickoff"), row.get("utcDate"), row.get("date_time"), row.get("datetime"),
    ]
    event = row.get("event")
    if isinstance(event, dict):
        values.extend([event.get("event_date"), event.get("start_time"), event.get("commence_time")])
    for value in values:
        if value in (None, ""):
            continue
        try:
            if isinstance(value, (int, float)) or str(value).isdigit():
                raw = float(value)
                if raw > 10_000_000_000:
                    raw /= 1000.0
                return datetime.fromtimestamp(raw, tz=UTC)
            text = str(value).strip().replace(" ", "T")
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            if "T" in text and "+" not in text and text.count("-") >= 2:
                text += "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except Exception:
            continue
    return None


def _token_similarity(a: str, b: str) -> float:
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    sa, sb = set(na.split()), set(nb.split())
    overlap = len(sa & sb) / max(1, len(sa | sb))
    seq = SequenceMatcher(None, na, nb).ratio()
    # Token overlap is more reliable for club names with prefixes/suffixes.
    return 100.0 * max(overlap, seq * 0.92)


def _relaxed_event_score(match: Match, row: dict[str, Any]) -> tuple[float, str | None]:
    event = row.get("event") if isinstance(row.get("event"), dict) else row
    if not isinstance(event, dict):
        return 0.0, None
    home = _event_name(event, "home")
    away = _event_name(event, "away")
    if not home or not away:
        return 0.0, None
    direct = (_token_similarity(match.home_team, home) + _token_similarity(match.away_team, away)) / 2.0
    swapped = (_token_similarity(match.home_team, away) + _token_similarity(match.away_team, home)) / 2.0
    name_score = max(direct, swapped - 8.0)
    start = _event_start(event)
    hours_delta = 999.0
    time_score = 45.0
    if start is not None:
        try:
            hours_delta = abs((match.commence_time.astimezone(UTC) - start).total_seconds()) / 3600.0
            if hours_delta <= 0.25:
                time_score = 100.0
            elif hours_delta <= 1.0:
                time_score = 92.0
            elif hours_delta <= 3.0:
                time_score = 82.0
            elif hours_delta <= 8.0:
                time_score = 68.0
            elif hours_delta <= 18.0:
                time_score = 55.0
            else:
                time_score = 10.0
        except Exception:
            pass
    league_score = 0.0
    league = str(event.get("league_name") or event.get("league") or "")
    if isinstance(event.get("league"), dict):
        league = str(event["league"].get("name") or "")
    if league and match.league_name:
        league_score = _token_similarity(match.league_name, league)
    score = name_score * 0.70 + time_score * 0.25 + league_score * 0.05
    quality = "relaxed_exact" if score >= 82 else "relaxed_fuzzy" if score >= 58 else None
    return score, quality


def _best_relaxed_event(match: Match, rows: list[dict[str, Any]], used_ids: set[str] | None = None) -> tuple[dict[str, Any] | None, float, str | None]:
    min_score = _to_float(os.getenv("BZZOIRO_CONTEXT_GAP_RELAXED_MIN_SCORE")) or 54.0
    best: dict[str, Any] | None = None
    best_score = 0.0
    best_quality: str | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        event = row.get("event") if isinstance(row.get("event"), dict) else row
        event_id = str((event or {}).get("id") or (event or {}).get("event_id") or row.get("id") or "")
        if used_ids and event_id and event_id in used_ids:
            continue
        score, quality = _relaxed_event_score(match, row)
        if score > best_score:
            best, best_score, best_quality = row, score, quality
    if best is not None and best_score >= min_score:
        return best, best_score, best_quality
    return None, best_score, None


def _inventory_target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    if explicit:
        return explicit
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
        return datetime.now(UTC).astimezone(tz).date().isoformat()
    except Exception:
        return datetime.now(UTC).date().isoformat()


def _listish(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, dict):
        return list(value.keys())
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in value.replace(";", ",").replace("|", ",").split(",") if x.strip()]
    return []





def _row_contains_bzzoiro_line_evidence(row: dict[str, Any]) -> bool:
    """Return True when the inventory row already has Bzzoiro-derived line/odds evidence.

    This is a controlled context bridge: when Bzzoiro has already matched the
    event well enough to provide odds/line evidence, we can mark it as a light
    event metadata context source. It does not create xG/lineup confidence and it
    does not weaken quality guards; it only fixes the coverage truth gap where
    bzzoiro+odds_api_io overlap was visible in the report but Bzzoiro was not
    counted as a second context source for the same row.
    """
    if not isinstance(row, dict):
        return False
    containers: list[Any] = [row]
    for key in ("metadata", "coverage", "debug", "diagnostics"):
        value = row.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for key in (
            "line_sources", "odds_sources", "price_sources", "source_combinations",
            "provider_sources", "sources", "books", "bookmakers",
        ):
            value = container.get(key) if isinstance(container, dict) else None
            tokens = _listish(value)
            if any("bzzoiro" in str(token).lower() for token in tokens):
                return True
        for key, value in list(container.items()) if isinstance(container, dict) else []:
            if "bzzoiro" in str(key).lower() and value not in (None, "", False, 0):
                return True
            if isinstance(value, str) and "bzzoiro" in value.lower():
                return True
    return False


def _light_context_from_bzzoiro_line_evidence(match: Match, row: dict[str, Any]) -> MatchContext:
    return MatchContext(
        source="bzzoiro",
        payload={
            "provider": "bzzoiro_line_evidence_context_bridge",
            "inventory_match_key": _match_key(match),
            "inventory_row": {
                "match_key": row.get("match_key"),
                "home_team": row.get("home_team") or row.get("home"),
                "away_team": row.get("away_team") or row.get("away"),
                "league_name": row.get("league_name") or row.get("league"),
            },
        },
        expected_home=None,
        expected_away=None,
        confidence=52.0,
        details={
            "bzzoiro_line_evidence_context_bridge": True,
            "bzzoiro_context_quality": "light_event_metadata_from_matched_odds",
            "source_tokens": ["bzzoiro"],
        },
    )




def _date_token(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except Exception:
        pass
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    return match.group(1) if match else ""


def _row_date_token(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    for key in ("date_local", "kickoff_local", "kickoff_utc", "commence_time", "start_time", "event_date"):
        value = row.get(key)
        token = _date_token(value)
        if token:
            return token
    return ""


def _row_team(row: dict[str, Any] | None, side: str) -> str:
    if not isinstance(row, dict):
        return ""
    keys = ("home_team", "home", "home_name", "homeTeam") if side == "home" else ("away_team", "away", "away_name", "awayTeam")
    for key in keys:
        value = row.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("short_name") or value.get("team_name")
        if value not in (None, ""):
            return str(value)
    return ""


def _inventory_alias_keys(*, key: Any = "", home: Any = "", away: Any = "", date: Any = "") -> set[str]:
    aliases: set[str] = set()
    raw_key = str(key or "").strip()
    if raw_key:
        aliases.add(raw_key)
    d = _date_token(date) or _date_token(raw_key)
    h = _norm_text(home)
    a = _norm_text(away)
    if h and a and d:
        aliases.add(f"{d}|{h}|{a}")
        aliases.add(f"{d}|{a}|{h}")
        aliases.add(f"soccer|{h}|{a}|{d}")
        aliases.add(f"soccer|{a}|{h}|{d}")
    if raw_key:
        parts = [part for part in raw_key.split("|") if part]
        # Convert between the two key shapes seen in artifacts:
        #   2026-05-27|home|away
        #   soccer|home|away|2026-05-27 or soccer|away|home|2026-05-27
        if len(parts) >= 3:
            maybe_date = next((part for part in parts if re.match(r"20\d{2}-\d{2}-\d{2}$", part)), "")
            teams = [part for part in parts if not re.match(r"20\d{2}-\d{2}-\d{2}$", part) and part != "soccer"]
            if maybe_date and len(teams) >= 2:
                t1, t2 = _norm_text(teams[0]), _norm_text(teams[1])
                if t1 and t2:
                    aliases.add(f"{maybe_date}|{t1}|{t2}")
                    aliases.add(f"{maybe_date}|{t2}|{t1}")
                    aliases.add(f"soccer|{t1}|{t2}|{maybe_date}")
                    aliases.add(f"soccer|{t2}|{t1}|{maybe_date}")
    return {alias for alias in aliases if alias}


def _match_alias_keys(match: Match) -> set[str]:
    date = ""
    with suppress(Exception):
        date = match.commence_time.astimezone(UTC).date().isoformat()
    return _inventory_alias_keys(key=_match_key(match), home=getattr(match, "home_team", ""), away=getattr(match, "away_team", ""), date=date)


def _row_alias_keys(row: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key_name in ("match_key", "canonical_match_id", "loose_key"):
        keys.update(_inventory_alias_keys(key=row.get(key_name), home=_row_team(row, "home"), away=_row_team(row, "away"), date=_row_date_token(row)))
    keys.update(_inventory_alias_keys(home=_row_team(row, "home"), away=_row_team(row, "away"), date=_row_date_token(row)))
    return keys


def _inventory_row_for_match(match: Match, rows_by_key: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for alias in _match_alias_keys(match):
        row = rows_by_key.get(alias)
        if isinstance(row, dict):
            return row
    return None


def _inventory_row_for_context_key(match_key: Any, rows_by_key: dict[str, dict[str, Any]], ctx: MatchContext | None = None) -> dict[str, Any] | None:
    for alias in _inventory_alias_keys(key=match_key):
        row = rows_by_key.get(alias)
        if isinstance(row, dict):
            return row
    payload = getattr(ctx, "payload", None) if ctx is not None else None
    if isinstance(payload, dict):
        inv = payload.get("inventory_row")
        if isinstance(inv, dict):
            for alias in _row_alias_keys(inv):
                row = rows_by_key.get(alias)
                if isinstance(row, dict):
                    return row
        event = payload.get("event")
        if isinstance(event, dict):
            home = event.get("home_team") or event.get("home") or event.get("home_name")
            away = event.get("away_team") or event.get("away") or event.get("away_name")
            date = event.get("event_date") or event.get("start_time") or event.get("date")
            for alias in _inventory_alias_keys(home=home, away=away, date=date):
                row = rows_by_key.get(alias)
                if isinstance(row, dict):
                    return row
    return None

def _inventory_rows_by_key() -> dict[str, dict[str, Any]]:
    day = _inventory_target_date()
    path = ROOT / ".data" / "day_inventory" / f"{day}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for alias in _row_alias_keys(row):
            out.setdefault(alias, row)
    return out


def _contexts_from_bzzoiro_line_evidence(matches: list[Match], existing_contexts: dict[str, MatchContext]) -> tuple[dict[str, MatchContext], dict[str, Any]]:
    if not _truthy(os.getenv("BZZOIRO_ODDS_MATCH_COUNTS_AS_EVENT_CONTEXT"), True):
        return {}, {"enabled": False}
    rows_by_key = _inventory_rows_by_key()
    added: dict[str, MatchContext] = {}
    inspected = 0
    skipped_has_bzz = 0
    for match in matches or []:
        if getattr(match, "sport_key", "") != "soccer":
            continue
        key = _match_key(match)
        if not key:
            continue
        row = _inventory_row_for_match(match, rows_by_key)
        if not isinstance(row, dict):
            continue
        inspected += 1
        if _has_bzzoiro_context(existing_contexts.get(key)) or _has_bzzoiro_context(existing_contexts.get(str(row.get("match_key") or ""))):
            skipped_has_bzz += 1
            continue
        if not _row_contains_bzzoiro_line_evidence(row):
            continue
        added[key] = _light_context_from_bzzoiro_line_evidence(match, row)
    return added, {
        "enabled": True,
        "inspected": inspected,
        "added": len(added),
        "skipped_already_has_bzzoiro": skipped_has_bzz,
    }



def _row_has_any_line_evidence(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    containers: list[dict[str, Any]] = [row]
    for key in ("metadata", "coverage", "debug", "diagnostics"):
        value = row.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for key in ("line_sources", "odds_sources", "price_sources", "books", "bookmakers", "source_combinations"):
            if _listish(container.get(key)):
                return True
        for key in ("line_sources_count", "odds_sources_count", "price_sources_count", "books_count", "price_confirmation_sources_count"):
            if _to_int(container.get(key), 0) > 0:
                return True
        if bool(container.get("odds") or container.get("has_current_odds_provider")):
            return True
    return False


def _context_source_tokens_from_row(row: dict[str, Any] | None) -> set[str]:
    tokens: set[str] = set()
    if not isinstance(row, dict):
        return tokens
    containers: list[dict[str, Any]] = [row]
    for key in ("metadata", "coverage"):
        value = row.get(key)
        if isinstance(value, dict):
            containers.append(value)
    if _row_has_bzzoiro_context_hint(row):
        tokens.add("bzzoiro")
    for container in containers:
        for key in ("context_sources", "source_tokens", "sources"):
            for item in _listish(container.get(key)):
                text = str(item or "").strip().lower()
                if text:
                    tokens.add(text)
        # Older runtime repairs sometimes only store SStats in evidence samples.
        samples = container.get("source_evidence_samples")
        if isinstance(samples, list):
            for sample in samples:
                if isinstance(sample, dict):
                    source = str(sample.get("source") or sample.get("provider") or "").lower()
                    if "sstats" in source:
                        tokens.add("sstats")
                    if "bzzoiro" in source:
                        tokens.add("bzzoiro")
    return tokens


def _has_non_bzzoiro_context_from_row(row: dict[str, Any] | None) -> bool:
    return any(token and token != "bzzoiro" for token in _context_source_tokens_from_row(row))


def _row_has_bzzoiro_context_hint(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    if any(bool(md.get(key)) for key in (
        "bzzoiro_context_fields", "bzzoiro_has_prediction", "bzzoiro_has_context_hint",
        "bzzoiro_context_gap_annotated_at_utc", "bzzoiro_line_evidence_context_bridge",
    )):
        return True
    source_ids = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
    provider_ids = md.get("provider_source_ids") if isinstance(md.get("provider_source_ids"), dict) else {}
    has_bzz_id = any(str(k).lower().startswith(("bzzoiro", "bsd")) for k in list(source_ids.keys()) + list(provider_ids.keys()))
    return bool(has_bzz_id and (cov.get("context") or cov.get("xg") or md.get("bzzoiro_raw_source")))

def _annotate_day_inventory_from_contexts(contexts: dict[str, MatchContext]) -> dict[str, Any]:
    day = _inventory_target_date()
    path = ROOT / ".data" / "day_inventory" / f"{day}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "missing_inventory", "date_local": day, "path": str(path)}
    matches = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(matches, list):
        return {"status": "bad_inventory_shape", "date_local": day, "path": str(path)}
    by_key: dict[str, dict[str, Any]] = {}
    for row in matches:
        if isinstance(row, dict):
            for alias in _row_alias_keys(row):
                by_key.setdefault(alias, row)
    updated = 0
    with_2_context = 0
    now_s = datetime.now(UTC).isoformat()
    for match_key, ctx in (contexts or {}).items():
        row = _inventory_row_for_context_key(match_key, by_key, ctx)
        if not row:
            continue
        existing_tokens = _context_source_tokens_from_row(row)
        existing = [str(x).strip() for x in _listish(row.get("context_sources")) if str(x).strip()]
        existing.extend(sorted(existing_tokens))
        if "bzzoiro" not in {x.lower() for x in existing}:
            existing.append("bzzoiro")
        merged = sorted({x for x in existing if x}, key=lambda x: x.lower())
        row["context_sources"] = merged
        cov = row.setdefault("coverage", {})
        if isinstance(cov, dict):
            cov["context"] = True
            cov["context_sources_count"] = len(merged)
        md = row.setdefault("metadata", {})
        if isinstance(md, dict):
            md["context_sources"] = merged
            md["context_sources_count"] = max(_to_int(md.get("context_sources_count"), 0), len(merged))
            md["bzzoiro_context_gap_annotated_at_utc"] = now_s
            details = getattr(ctx, "details", None)
            if isinstance(details, dict):
                md["bzzoiro_context_gap_details"] = {k: details.get(k) for k in ("bzzoiro_v2_event_id", "bzzoiro_match_quality", "bzzoiro_has_stats", "bzzoiro_has_metadata", "bzzoiro_has_lineups") if k in details}
        updated += 1
        if len(merged) >= 2:
            with_2_context += 1
    if updated:
        payload["updated_at_utc"] = now_s
        payload.setdefault("sources", {})
        if isinstance(payload.get("sources"), dict):
            payload["sources"]["runtime_bzzoiro_context_annotation"] = {
                "updated_at_utc": now_s,
                "updated_matches": updated,
                "matches_with_2plus_runtime_context_sources": with_2_context,
            }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "ok", "date_local": day, "updated_matches": updated, "matches_with_2plus_runtime_context_sources": with_2_context}


async def _fetch_json(client: httpx.AsyncClient, url: str, headers: dict[str, str], stats: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
    stats["requests"] = _to_int(stats.get("requests"), 0) + 1
    response = await client.get(url, headers=headers, params=params or {})
    stats.setdefault("http_statuses", []).append(response.status_code)
    stats["last_url"] = str(response.url)
    if response.status_code != 200:
        stats["errors"] = _to_int(stats.get("errors"), 0) + 1
        stats["last_error"] = f"http_status={response.status_code}"
        stats["last_body_preview"] = response.text[:800]
        return None
    try:
        return response.json()
    except Exception as exc:
        stats["errors"] = _to_int(stats.get("errors"), 0) + 1
        stats["last_error"] = f"json:{type(exc).__name__}: {exc}"
        stats["last_body_preview"] = response.text[:800]
        return None


def _results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("results", "events", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


async def _fetch_paginated(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    headers: dict[str, str],
    params: dict[str, Any],
    stats: dict[str, Any],
    *,
    mode: str,
    max_pages: int,
    max_requests: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    if mode == "page":
        for page in range(1, max_pages + 1):
            if _to_int(stats.get("requests"), 0) >= max_requests:
                break
            payload = await _fetch_json(client, f"{base_url}{path}", headers, stats, {**params, "page": page})
            batch = _results(payload)
            if not batch:
                break
            for row in batch:
                sig = str(row.get("id") or row.get("uuid") or row.get("api_id") or row.get("event_id") or row)
                if sig in seen:
                    continue
                seen.add(sig)
                rows.append(row)
            if not isinstance(payload, dict) or not payload.get("next"):
                break
    else:
        offset = 0
        while _to_int(stats.get("requests"), 0) < max_requests and offset <= 1200:
            payload = await _fetch_json(client, f"{base_url}{path}", headers, stats, {**params, "limit": 200, "offset": offset})
            batch = _results(payload)
            if not batch:
                break
            for row in batch:
                sig = str(row.get("id") or row.get("uuid") or row.get("api_id") or row.get("event_id") or row)
                if sig in seen:
                    continue
                seen.add(sig)
                rows.append(row)
            if not isinstance(payload, dict) or not payload.get("next"):
                break
            offset += 200
    return rows


async def _fetch_v2_resources(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    event_id: Any,
    stats: dict[str, Any],
    max_requests: int,
) -> dict[str, Any]:
    resources: dict[str, Any] = {}
    if event_id in (None, ""):
        return resources
    for name, url in [
        ("stats", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/stats/"),
        ("metadata", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/metadata/"),
        ("lineups", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/lineups/"),
        ("odds", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/odds/"),
    ]:
        if _to_int(stats.get("requests"), 0) >= max_requests:
            break
        payload = await _fetch_json(client, url, headers, stats)
        if isinstance(payload, dict):
            resources[name] = payload
            stats[f"{name}_resources"] = _to_int(stats.get(f"{name}_resources"), 0) + 1
    return resources


async def _fetch_v2_context_resources(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    event_id: Any,
    stats: dict[str, Any],
    max_requests: int,
) -> dict[str, Any]:
    """Fetch only context resources; current odds use the separate offers lane."""

    resources: dict[str, Any] = {}
    if event_id in (None, ""):
        return resources
    for name, url in [
        ("stats", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/stats/"),
        (
            "metadata",
            f"https://sports.bzzoiro.com/api/v2/events/{event_id}/metadata/",
        ),
        (
            "lineups",
            f"https://sports.bzzoiro.com/api/v2/events/{event_id}/lineups/",
        ),
    ]:
        if _to_int(stats.get("requests"), 0) >= max_requests:
            break
        payload = await _fetch_json(client, url, headers, stats)
        if isinstance(payload, dict):
            resources[name] = payload
            stats[f"{name}_resources"] = (
                _to_int(stats.get(f"{name}_resources"), 0) + 1
            )
    return resources


def _nonempty_resource_value(value: Any) -> bool:
    if value in (None, "", False, [], {}):
        return False
    if isinstance(value, dict):
        return any(
            _nonempty_resource_value(item)
            for key, item in value.items()
            if str(key).lower()
            not in {"id", "event_id", "status", "success", "message", "detail"}
        )
    if isinstance(value, (list, tuple, set)):
        return any(_nonempty_resource_value(item) for item in value)
    return True


def _resources_have_context_information(resources: dict[str, Any]) -> bool:
    """Require provider context data; an odds response alone is not context."""

    stats = resources.get("stats")
    if isinstance(stats, (dict, list)) and _nonempty_resource_value(stats):
        return True
    lineups = resources.get("lineups")
    if isinstance(lineups, (dict, list)) and _nonempty_resource_value(lineups):
        return True
    metadata = resources.get("metadata")
    if isinstance(metadata, dict):
        context_keys = {
            "ai_preview",
            "analysis",
            "facts",
            "form",
            "fun_facts",
            "h2h",
            "head_to_head",
            "injuries",
            "insights",
            "preview",
            "standings",
            "weather",
        }
        if any(
            key in metadata and _nonempty_resource_value(metadata.get(key))
            for key in context_keys
        ):
            return True
    return False


async def _source_id_prefetch(
    self: Any,
    matches: list[Match],
) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
    """Fetch documented v2 resources for exact saved Bzzoiro event ids first."""

    token = str(
        os.getenv("BZZOIRO_API_KEY")
        or getattr(getattr(self, "settings", None), "bzzoiro_api_key", "")
        or ""
    ).strip()
    enabled = _truthy(os.getenv("BZZOIRO_SOURCE_ID_PREFETCH_ENABLED"), True)
    stats: dict[str, Any] = {
        "enabled": enabled and bool(token),
        "source_ids_seen": 0,
        "target_matches": 0,
        "requests": 0,
        "errors": 0,
        "contexts_added": 0,
        "stats_resources": 0,
        "metadata_resources": 0,
        "lineups_resources": 0,
        "odds_resources": 0,
    }
    preview: dict[str, Any] = {"added": [], "without_context_information": []}
    if not enabled or not token or not matches:
        return {}, stats, preview

    now = datetime.now(UTC)
    candidates: list[tuple[Match, str, float]] = []
    seen_ids: set[str] = set()
    for match in matches:
        if getattr(match, "sport_key", "") != "soccer":
            continue
        event_id = _bzzoiro_id_from_match(match)
        if not event_id or event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        try:
            hours = (
                match.commence_time.astimezone(UTC) - now
            ).total_seconds() / 3600.0
        except Exception:
            hours = 999999.0
        if hours < -0.05:
            continue
        candidates.append((match, event_id, hours))
    stats["source_ids_seen"] = len(candidates)
    candidates.sort(
        key=lambda item: (
            0 if item[2] <= 4 else 1 if item[2] <= 12 else 2,
            abs(item[2]),
            _match_key(item[0]),
        )
    )
    match_limit = max(
        1, _to_int(os.getenv("BZZOIRO_SOURCE_ID_PREFETCH_MATCH_LIMIT") or 20, 20)
    )
    max_requests = max(
        3, _to_int(os.getenv("BZZOIRO_SOURCE_ID_PREFETCH_MAX_REQUESTS") or 60, 60)
    )
    candidates = candidates[: min(match_limit, max_requests // 3)]
    stats["target_matches"] = len(candidates)
    if not candidates:
        return {}, stats, preview

    headers = {"Authorization": f"Token {token}"}
    concurrency = max(
        1,
        min(
            12,
            _to_int(os.getenv("BZZOIRO_SOURCE_ID_PREFETCH_CONCURRENCY") or 8, 8),
        ),
    )
    semaphore = asyncio.Semaphore(concurrency)
    timeout = float(
        getattr(getattr(self, "settings", None), "bzzoiro_timeout_seconds", 20.0)
        or 20.0
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        async def fetch_one(
            match: Match, event_id: str, hours: float
        ) -> tuple[Match, str, float, dict[str, Any], dict[str, Any]]:
            local_stats: dict[str, Any] = {"requests": 0, "errors": 0}
            async with semaphore:
                resources = await _fetch_v2_context_resources(
                    client, headers, event_id, local_stats, 3
                )
            return match, event_id, hours, resources, local_stats

        results = await asyncio.gather(
            *(fetch_one(match, event_id, hours) for match, event_id, hours in candidates),
            return_exceptions=True,
        )

    added: dict[str, MatchContext] = {}
    for result in results:
        if isinstance(result, BaseException):
            stats["errors"] = _to_int(stats.get("errors"), 0) + 1
            continue
        match, event_id, hours, resources, local_stats = result
        for field in (
            "requests",
            "errors",
            "stats_resources",
            "metadata_resources",
            "lineups_resources",
            "odds_resources",
        ):
            stats[field] = _to_int(stats.get(field), 0) + _to_int(
                local_stats.get(field), 0
            )
        if not _resources_have_context_information(resources):
            if len(preview["without_context_information"]) < 20:
                preview["without_context_information"].append(
                    {"match_key": _match_key(match), "event_id": event_id}
                )
            continue
        event = {
            "id": event_id,
            "event_id": event_id,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "start_time": match.commence_time.astimezone(UTC).isoformat(),
        }
        context = _context_from_resources(event, resources, 100.0, "source_id")
        context.details["bzzoiro_source_id_prefetch"] = True
        context.details["bzzoiro_hours_to_kickoff"] = round(hours, 3)
        added[_match_key(match)] = context
        if len(preview["added"]) < 20:
            preview["added"].append(
                {
                    "match_key": _match_key(match),
                    "event_id": event_id,
                    "has_xg": context.expected_home is not None
                    or context.expected_away is not None,
                }
            )
    stats["contexts_added"] = len(added)
    return added, stats, preview


def _merge_context(base: MatchContext | None, resources_context: MatchContext | None) -> MatchContext | None:
    if base is None:
        return resources_context
    if resources_context is None:
        return base
    payload = dict(base.payload or {})
    payload["gap_resources_context"] = resources_context.payload
    details = dict(base.details or {})
    details.update(resources_context.details or {})
    return MatchContext(
        source="bzzoiro",
        payload=payload,
        expected_home=base.expected_home if base.expected_home is not None else resources_context.expected_home,
        expected_away=base.expected_away if base.expected_away is not None else resources_context.expected_away,
        home_win_probability=base.home_win_probability if base.home_win_probability is not None else resources_context.home_win_probability,
        away_win_probability=base.away_win_probability if base.away_win_probability is not None else resources_context.away_win_probability,
        home_starting=base.home_starting if base.home_starting is not None else resources_context.home_starting,
        away_starting=base.away_starting if base.away_starting is not None else resources_context.away_starting,
        confidence=max(float(base.confidence or 0), float(resources_context.confidence or 0)),
        profits=dict(base.profits or {}),
        details=details,
    )




def _team_search_queries(name: str) -> list[str]:
    tokens = _norm_text(name).split()
    full = " ".join(tokens)
    queries: list[str] = []
    if full:
        queries.append(full)
    if len(tokens) >= 3:
        queries.append(" ".join(tokens[:3]))
        queries.append(" ".join(tokens[-3:]))
    if len(tokens) >= 2:
        queries.append(" ".join(tokens[:2]))
    raw = str(name or "").strip()
    if raw:
        queries.append(raw)
    seen: set[str] = set()
    out: list[str] = []
    for query in queries:
        q = str(query or "").strip()
        if not q or q.lower() in seen:
            continue
        seen.add(q.lower())
        out.append(q)
    return out[:4]


def _team_results(payload: Any) -> list[dict[str, Any]]:
    return _results(payload)


async def _targeted_team_fixture_event(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    match: Match,
    stats: dict[str, Any],
    max_requests: int,
) -> tuple[dict[str, Any] | None, float, str | None]:
    """Resolve a Bzzoiro v2 event through /teams/?name= and /teams/{id}/fixtures/.

    The broad /api/v2/events/ daily window may only return a small slice of the
    provider universe.  The v2 docs expose team search and team fixtures, so for
    priority gap targets we search both teams and then inspect that team's
    fixtures in the same UTC window.  This is intentionally bounded by request
    budget and target limit; it improves context coverage without weakening any
    publication guard.
    """
    if _to_int(stats.get("requests"), 0) >= max_requests:
        return None, 0.0, None
    date_from = match.commence_time.astimezone(UTC).date().isoformat()
    date_to = (match.commence_time.astimezone(UTC).date() + timedelta(days=1)).isoformat()
    best_event: dict[str, Any] | None = None
    best_score = 0.0
    best_quality: str | None = None
    searched_team_ids: set[str] = set()
    for side_name in (match.home_team, match.away_team):
        for query in _team_search_queries(side_name):
            if not query:
                continue
            if _to_int(stats.get("requests"), 0) >= max_requests:
                break
            stats["targeted_team_search_requests"] = _to_int(stats.get("targeted_team_search_requests"), 0) + 1
            team_payload = await _fetch_json(
                client,
                "https://sports.bzzoiro.com/api/v2/teams/",
                headers,
                stats,
                {"name": query, "limit": 10, "offset": 0},
            )
            teams = _team_results(team_payload)
            stats["targeted_team_rows"] = _to_int(stats.get("targeted_team_rows"), 0) + len(teams)
            for team in teams[:5]:
                team_id = team.get("id") or team.get("team_id")
                if team_id in (None, ""):
                    continue
                sid = str(team_id)
                if sid in searched_team_ids:
                    continue
                searched_team_ids.add(sid)
                if _to_int(stats.get("requests"), 0) >= max_requests:
                    break
                stats["targeted_fixture_requests"] = _to_int(stats.get("targeted_fixture_requests"), 0) + 1
                fixtures_payload = await _fetch_json(
                    client,
                    f"https://sports.bzzoiro.com/api/v2/teams/{sid}/fixtures/",
                    headers,
                    stats,
                    {"date_from": date_from, "date_to": date_to, "limit": 50, "offset": 0},
                )
                fixtures = _team_results(fixtures_payload)
                if not fixtures and _to_int(stats.get("requests"), 0) < max_requests:
                    # BSD docs say team fixtures default to now-3h..now+7d when no
                    # date filters are supplied. Some accounts appear to ignore or
                    # over-restrict date-only filters, so try the documented default
                    # window as a bounded fallback.
                    stats["targeted_fixture_default_window_requests"] = _to_int(stats.get("targeted_fixture_default_window_requests"), 0) + 1
                    default_payload = await _fetch_json(
                        client,
                        f"https://sports.bzzoiro.com/api/v2/teams/{sid}/fixtures/",
                        headers,
                        stats,
                        {"limit": 50, "offset": 0},
                    )
                    fixtures = _team_results(default_payload)
                    stats["targeted_fixture_default_window_rows"] = _to_int(stats.get("targeted_fixture_default_window_rows"), 0) + len(fixtures)
                stats["targeted_fixture_rows"] = _to_int(stats.get("targeted_fixture_rows"), 0) + len(fixtures)
                for event in fixtures:
                    score, quality = _relaxed_event_score(match, event)
                    if score > best_score:
                        best_event, best_score, best_quality = event, score, quality or "targeted_team_fixture"
    min_score = _to_float(os.getenv("BZZOIRO_CONTEXT_GAP_TARGETED_MIN_SCORE")) or 50.0
    if best_event is not None and best_score >= min_score:
        stats["matched_by_targeted_team_fixture"] = _to_int(stats.get("matched_by_targeted_team_fixture"), 0) + 1
        return best_event, best_score, best_quality or "targeted_team_fixture"
    return None, best_score, None


async def _gap_pass(self: Any, matches: list[Match], existing_contexts: dict[str, MatchContext]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
    from app.services import windowed_core_coverage_runtime_patch as wc

    token = str(os.getenv("BZZOIRO_API_KEY") or getattr(getattr(self, "settings", None), "bzzoiro_api_key", "") or "").strip()
    ignore_plan = _truthy(os.getenv("BZZOIRO_CONTEXT_GAP_IGNORE_PLAN"), True)
    stats: dict[str, Any] = {
        "enabled": bool(token),
        "requests": 0,
        "errors": 0,
        "target_matches": 0,
        "matched": 0,
        "contexts_added": 0,
        "contexts_already_present": 0,
        "targets_with_existing_non_bzz_context": 0,
        "targets_with_line_evidence": 0,
        "v1_events_fetched": 0,
        "v1_predictions_fetched": 0,
        "v2_events_fetched": 0,
        "matched_by_source_id": 0,
        "matched_by_v1_prediction": 0,
        "matched_by_v1_event": 0,
        "matched_by_v2_fuzzy": 0,
        "matched_by_relaxed_event": 0,
        "matched_by_relaxed_prediction": 0,
        "matched_by_targeted_team_fixture": 0,
        "targeted_team_search_requests": 0,
        "targeted_fixture_requests": 0,
        "targeted_team_rows": 0,
        "targeted_fixture_rows": 0,
        "relaxed_match_enabled": True,
        "ignore_plan": ignore_plan,
        "stats_resources": 0,
        "metadata_resources": 0,
        "lineups_resources": 0,
        "odds_resources": 0,
        "stop_reason": "",
    }
    preview: dict[str, Any] = {"added": [], "unmatched": [], "target_sample": []}
    if not token or not matches:
        return {}, stats, preview

    gap_keys = set() if ignore_plan else _gap_keys()
    now = datetime.now(UTC)
    rows_by_key = _inventory_rows_by_key()
    candidates: list[Match] = []
    for match in matches:
        if getattr(match, "sport_key", "") != "soccer":
            continue
        key = _match_key(match)
        inventory_row = rows_by_key.get(key)
        if _has_non_bzzoiro_context_from_row(inventory_row) or (key in existing_contexts and not _has_bzzoiro_context(existing_contexts.get(key))):
            stats["targets_with_existing_non_bzz_context"] = _to_int(stats.get("targets_with_existing_non_bzz_context"), 0) + 1
        if _row_has_any_line_evidence(inventory_row):
            stats["targets_with_line_evidence"] = _to_int(stats.get("targets_with_line_evidence"), 0) + 1
        if _row_has_bzzoiro_context_hint(inventory_row):
            # The frozen inventory already contains Bzzoiro event/prediction
            # context evidence.  The repair/truth scripts will count it, so do
            # not spend hundreds of v2 resource calls trying to rediscover it.
            stats["already_has_bzzoiro_context"] = _to_int(stats.get("already_has_bzzoiro_context"), 0) + 1
            continue
        if key in existing_contexts:
            stats["contexts_already_present"] += 1
            # Existing SStats/ClubElo context is not enough for A-tier.  Keep the
            # match in the Bzzoiro gap pass unless it already has a Bzzoiro
            # context; this is how we build 2+ independent context sources.
            if _has_bzzoiro_context(existing_contexts.get(key)):
                stats["already_has_bzzoiro_context"] = _to_int(stats.get("already_has_bzzoiro_context"), 0) + 1
                continue
        try:
            hours = (match.commence_time.astimezone(UTC) - now).total_seconds() / 3600.0
        except Exception:
            hours = 999999.0
        if hours < -0.05:
            continue
        if gap_keys and key not in gap_keys:
            continue
        candidates.append(match)
    
    def _priority(match: Match) -> tuple[int, int, int, int, float, str]:
        key = _match_key(match)
        row = _inventory_row_for_match(match, rows_by_key)
        try:
            hours = (match.commence_time.astimezone(UTC) - now).total_seconds() / 3600.0
        except Exception:
            hours = 999999.0
        # Fill near kickoff first because those are the only matches that can publish soon.
        window = 0 if 0 <= hours <= 4 else 1 if 0 <= hours <= 12 else 2 if hours >= 0 else 3
        # Second priority: matches that already have SStats/ClubElo/other context.
        # Adding Bzzoiro there immediately creates 2+ independent context sources.
        has_other_context = _has_non_bzzoiro_context_from_row(row) or (key in existing_contexts and not _has_bzzoiro_context(existing_contexts.get(key)))
        has_line = _row_has_any_line_evidence(row)
        return (window, 0 if has_other_context else 1, 0 if has_line else 1, 0 if _bzzoiro_id_from_match(match) else 1, abs(hours), match.league_name.lower())

    candidates.sort(key=_priority)
    limit = max(1, _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT") or 240, 240))
    candidates = candidates[:limit]
    stats["target_matches"] = len(candidates)
    preview["target_sample"] = [{
        "match_key": _match_key(m),
        "home": m.home_team,
        "away": m.away_team,
        "bzzoiro_id": _bzzoiro_id_from_match(m),
        "has_other_context": _has_non_bzzoiro_context_from_row(_inventory_row_for_match(m, rows_by_key)) or (_match_key(m) in existing_contexts and not _has_bzzoiro_context(existing_contexts.get(_match_key(m)))),
        "has_line_evidence": _row_has_any_line_evidence(_inventory_row_for_match(m, rows_by_key)),
    } for m in candidates[:25]]
    if not candidates:
        stats["stop_reason"] = "no_gap_targets"
        return {}, stats, preview

    max_requests = max(1, _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MAX_REQUESTS") or 360, 360))
    headers = {"Authorization": f"Token {token}"}
    added: dict[str, MatchContext] = {}
    min_dt = min(m.commence_time for m in candidates).astimezone(UTC)
    max_dt = max(m.commence_time for m in candidates).astimezone(UTC)
    query_to_date = (max_dt.date() + timedelta(days=1)).isoformat()

    async with httpx.AsyncClient(timeout=float(getattr(getattr(self, "settings", None), "bzzoiro_timeout_seconds", 20.0) or 20.0)) as client:
        v1_base = str(getattr(self, "base_url", "https://sports.bzzoiro.com/api") or "https://sports.bzzoiro.com/api").rstrip("/")
        v1_params = {"date_from": min_dt.date().isoformat(), "date_to": query_to_date, "tz": "UTC"}
        v1_events = await _fetch_paginated(client, v1_base, "/events/", headers, v1_params, stats, mode="page", max_pages=12, max_requests=max_requests)
        stats["v1_events_fetched"] = len(v1_events)
        v1_predictions = await _fetch_paginated(client, v1_base, "/predictions/", headers, {**v1_params, "upcoming": "true"}, stats, mode="page", max_pages=12, max_requests=max_requests)
        if not v1_predictions and _to_int(stats.get("requests"), 0) < max_requests:
            v1_predictions = await _fetch_paginated(client, v1_base, "/predictions/", headers, v1_params, stats, mode="page", max_pages=12, max_requests=max_requests)
        stats["v1_predictions_fetched"] = len(v1_predictions)

        v2_events = await _fetch_paginated(client, "https://sports.bzzoiro.com", "/api/v2/events/", headers, {"date_from": min_dt.date().isoformat(), "date_to": query_to_date}, stats, mode="offset", max_pages=12, max_requests=max_requests)
        stats["v2_events_fetched"] = len(v2_events)
        v2_by_id: dict[str, dict[str, Any]] = {}
        for row in v2_events:
            row_id = row.get("id") or row.get("event_id")
            if row_id not in (None, ""):
                v2_by_id[str(row_id)] = row

        used_prediction_ids: set[str] = set()
        for match in candidates:
            if _to_int(stats.get("requests"), 0) >= max_requests:
                stats["stop_reason"] = "request_budget_exhausted"
                break
            context: MatchContext | None = None
            event_for_resources: dict[str, Any] | None = None
            score = 0.0
            quality: str | None = None

            # 1) Broad v1 prediction coverage. This is the richest context path.
            try:
                pred, pred_quality, pred_score, _ = self._match_prediction(match, v1_predictions, used_prediction_ids)
            except Exception:
                pred, pred_quality, pred_score = None, None, 0.0
            if pred is not None:
                pred_id = None
                try:
                    pred_id = self._prediction_identity(pred)
                except Exception:
                    pred_id = None
                if pred_id:
                    used_prediction_ids.add(pred_id)
                try:
                    event_for_resources = self._prediction_event(pred, v1_events)
                except Exception:
                    event_for_resources = pred.get("event") if isinstance(pred.get("event"), dict) else None
                try:
                    context = self._prediction_to_context(pred, event_for_resources, pred_quality)
                    stats["matched_by_v1_prediction"] += 1
                    score = float(pred_score or 0.0)
                    quality = pred_quality
                except Exception:
                    context = None

            # 2) v1 event fallback: still useful because event has odds/basic context.
            if context is None:
                try:
                    event, event_quality, event_score, _ = self._match_event(match, v1_events)
                except Exception:
                    event, event_quality, event_score = None, None, 0.0
                if event is not None:
                    event_for_resources = event
                    try:
                        context = self._event_to_context(event, event_quality)
                    except Exception:
                        context = None
                    if context is not None:
                        stats["matched_by_v1_event"] += 1
                        score = float(event_score or 0.0)
                        quality = event_quality

            # 3) Relaxed Bzzoiro event/prediction fallback.  The upstream API
            # uses slightly different team labels than odds-api.io/SStats, so
            # strict provider matchers often leave gap targets at matched=0.
            if context is None:
                relaxed_pred, relaxed_score, relaxed_quality = _best_relaxed_event(match, v1_predictions, used_prediction_ids)
                if relaxed_pred is not None:
                    try:
                        pred_id = self._prediction_identity(relaxed_pred)
                    except Exception:
                        pred_id = None
                    if pred_id:
                        used_prediction_ids.add(pred_id)
                    try:
                        event_for_resources = self._prediction_event(relaxed_pred, v1_events)
                    except Exception:
                        event_for_resources = relaxed_pred.get("event") if isinstance(relaxed_pred.get("event"), dict) else None
                    try:
                        context = self._prediction_to_context(relaxed_pred, event_for_resources, relaxed_quality or "relaxed")
                        stats["matched_by_relaxed_prediction"] += 1
                        score = float(relaxed_score or 0.0)
                        quality = relaxed_quality
                    except Exception:
                        context = None
            if context is None:
                relaxed_event, relaxed_score, relaxed_quality = _best_relaxed_event(match, v1_events)
                if relaxed_event is not None:
                    event_for_resources = relaxed_event
                    try:
                        context = self._event_to_context(relaxed_event, relaxed_quality or "relaxed")
                    except Exception:
                        context = None
                    if context is not None:
                        stats["matched_by_relaxed_event"] += 1
                        score = float(relaxed_score or 0.0)
                        quality = relaxed_quality

            # 4) v2 direct/fuzzy fallback.
            if context is None:
                event = None
                bzz_id = _bzzoiro_id_from_match(match)
                if bzz_id and bzz_id in v2_by_id:
                    event = v2_by_id[bzz_id]
                    score = 100.0
                    quality = "source_id"
                    stats["matched_by_source_id"] += 1
                elif bzz_id and _to_int(stats.get("requests"), 0) < max_requests:
                    payload = await _fetch_json(client, f"https://sports.bzzoiro.com/api/v2/events/{bzz_id}/", headers, stats)
                    if isinstance(payload, dict) and (payload.get("id") or payload.get("event_id")):
                        event = payload
                        score = 100.0
                        quality = "source_id"
                        stats["matched_by_source_id"] += 1
                if event is None:
                    event, score, quality = wc._match_bzzoiro_event(match, v2_events, getattr(self, "settings", None))
                    if event is not None:
                        stats["matched_by_v2_fuzzy"] += 1
                if event is None:
                    event, relaxed_score, relaxed_quality = _best_relaxed_event(match, v2_events)
                    if event is not None:
                        score = relaxed_score
                        quality = relaxed_quality
                        stats["matched_by_relaxed_event"] += 1
                if event is not None:
                    event_for_resources = event
                    event_id = event.get("id") or event.get("event_id")
                    resources = {"event": event}
                    resources.update(await _fetch_v2_resources(client, headers, event_id, stats, max_requests))
                    context = _context_from_resources(event, resources, float(score or 0.0), quality)

            # 5) Targeted team-search fallback from the documented v2 API.
            # Broad event windows can be small; /teams/?name= + /teams/{id}/fixtures/
            # often finds the same event for obscure leagues that are absent from the
            # first list page.
            targeted_limit = max(0, _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_TARGETED_SEARCH_LIMIT") or 60, 60))
            if context is None and targeted_limit > 0 and _to_int(stats.get("targeted_attempts"), 0) < targeted_limit:
                stats["targeted_attempts"] = _to_int(stats.get("targeted_attempts"), 0) + 1
                event, targeted_score, targeted_quality = await _targeted_team_fixture_event(client, headers, match, stats, max_requests)
                if event is not None:
                    event_for_resources = event
                    event_id = event.get("id") or event.get("event_id")
                    resources = {"event": event}
                    resources.update(await _fetch_v2_resources(client, headers, event_id, stats, max_requests))
                    context = _context_from_resources(event, resources, float(targeted_score or 0.0), targeted_quality or "targeted_team_fixture")
                    score = float(targeted_score or 0.0)
                    quality = targeted_quality or "targeted_team_fixture"

            # 6) If v1 gave a context and we can cheaply enrich it through v2 ids, merge.
            if context is not None and event_for_resources is not None and _to_int(stats.get("requests"), 0) < max_requests:
                event_id = event_for_resources.get("id") or event_for_resources.get("event_id")
                if event_id not in (None, ""):
                    resources = {"event": event_for_resources}
                    resources.update(await _fetch_v2_resources(client, headers, event_id, stats, max_requests))
                    if len(resources) > 1:
                        resources_context = _context_from_resources(event_for_resources, resources, float(score or 0.0), quality)
                        context = _merge_context(context, resources_context)

            if context is None:
                if len(preview["unmatched"]) < 25:
                    preview["unmatched"].append({"match_key": _match_key(match), "home": match.home_team, "away": match.away_team})
                continue
            existing = existing_contexts.get(_match_key(match))
            if existing is not None:
                context = _merge_context(existing, context) or context
            added[_match_key(match)] = context
            stats["matched"] += 1
            stats["contexts_added"] = len(added)
            if len(preview["added"]) < 25:
                preview["added"].append({
                    "match_key": _match_key(match),
                    "home": match.home_team,
                    "away": match.away_team,
                    "score": round(float(score or 0.0), 2),
                    "quality": quality,
                    "has_xg": context.expected_home is not None or context.expected_away is not None,
                    "odds_hints": (context.details or {}).get("bzzoiro_odds_hint_count"),
                    "context_confidence": round(float(context.confidence or 0.0), 2),
                })
    return added, stats, preview


def install() -> dict[str, Any]:
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "status": "starting"}
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider
    except Exception as exc:
        payload.update({"status": "error", "error": f"import:{type(exc).__name__}: {exc}"})
        _write_json(INSTALL_REPORT_PATH, payload)
        return payload
    current = BzzoiroContextProvider.fetch_context
    if getattr(current, "_harizon_bzzoiro_context_gap_finalizer", False):
        payload.update({"status": "already_installed"})
        _write_json(INSTALL_REPORT_PATH, payload)
        return payload

    async def fetch_context_with_gap_pass(self, matches: list[Match]):  # type: ignore[no-untyped-def]
        prefetched, prefetch_stats, prefetch_preview = await _source_id_prefetch(
            self, matches
        )
        prefetched_keys = set(prefetched)
        remaining = [match for match in matches if _match_key(match) not in prefetched_keys]
        contexts, stats, preview = await current(self, remaining)
        contexts = dict(contexts or {})
        contexts.update(prefetched)
        stats = dict(stats or {})
        stats["source_id_prefetch"] = prefetch_stats
        stats["source_id_prefetch_contexts"] = len(prefetched)
        preview = dict(preview or {})
        preview["source_id_prefetch"] = prefetch_preview
        if not _truthy(os.getenv("BZZOIRO_CONTEXT_GAP_PASS_ENABLED"), True):
            return contexts, stats, preview
        try:
            added, gap_stats, gap_preview = await _gap_pass(self, remaining, contexts)
            contexts.update(added)
            evidence_contexts, evidence_stats = _contexts_from_bzzoiro_line_evidence(matches, contexts)
            contexts.update(evidence_contexts)
            all_added = dict(added)
            all_added.update(evidence_contexts)
            gap_stats["line_evidence_context_bridge"] = evidence_stats
            gap_stats["contexts_added_from_line_evidence"] = len(evidence_contexts)
            gap_stats["contexts_added_total"] = len(all_added)
            gap_stats["day_inventory_context_annotation"] = _annotate_day_inventory_from_contexts(all_added)
            stats["context_gap_pass"] = gap_stats
            stats["contexts_built"] = max(_to_int(stats.get("contexts_built"), 0), len(contexts))
            preview["context_gap_pass"] = gap_preview
        except Exception as exc:
            stats["context_gap_pass"] = {"enabled": True, "error": f"{type(exc).__name__}: {exc}"}
            preview["context_gap_pass"] = {"error": f"{type(exc).__name__}: {exc}"}
        _write_json(RUNTIME_REPORT_PATH, {"status": "ran", "created_at_utc": datetime.now(UTC).isoformat(), "stats": stats.get("context_gap_pass", {}), "preview": preview.get("context_gap_pass", {})})
        return contexts, stats, preview

    fetch_context_with_gap_pass._harizon_bzzoiro_context_gap_finalizer = True  # type: ignore[attr-defined]
    BzzoiroContextProvider.fetch_context = fetch_context_with_gap_pass  # type: ignore[assignment]
    payload.update({
        "status": "installed",
        "match_limit": _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT") or 240, 240),
        "max_requests": _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MAX_REQUESTS") or 420, 420),
        "ignore_plan_default": True,
        "runtime_report": str(RUNTIME_REPORT_PATH),
    })
    _write_json(INSTALL_REPORT_PATH, payload)
    return payload
