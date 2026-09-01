from __future__ import annotations

"""Runtime helpers for fuller inventory and evidence coverage.

The patch is intentionally conservative: it does not create predictions and it
does not relax publication rules. It only makes provider discovery/enrichment use
configured evidence sources more effectively.
"""

import os
import sys
from datetime import datetime, timezone
from typing import Any

_INSTALLED = False
UTC = timezone.utc

ENV_DEFAULTS = {
    "DAY_INVENTORY_EXTRA_FIXTURES_ENABLED": "true",
    "DAY_INVENTORY_ENABLE_BZZOIRO": "true",
    "DAY_INVENTORY_ENABLE_SSTATS": "true",
    "DAY_INVENTORY_ENABLE_SPORTLOGIC": "true",
    "DAY_INVENTORY_ENABLE_ALLSPORTSAPI": "true",
    "DAY_INVENTORY_TARGET_SIZE": "300",
    "DAY_INVENTORY_MAX_MATCHES": "300",
    "DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES": "300",
    "DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES": "300",
    "DAY_INVENTORY_BZZOIRO_MAX_PAGES": "30",
    "DAY_INVENTORY_BZZOIRO_MAX_REQUESTS": "220",
    "DAY_INVENTORY_SSTATS_LIMIT": "1000",
    "DAY_INVENTORY_SSTATS_MAX_REQUESTS": "3",
    "DAY_INVENTORY_SSTATS_WINDOW_DAYS": "0",
    "DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT": "300",
    "DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS": "36",
    "SPORTLOGIC_ENABLED": "true",
    "ENABLE_SPORTLOGIC": "true",
    "SPORTLOGIC_PER_RUN_MAX": "80",
    "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "80",
    "SPORTLOGIC_REQUESTS_MAX_PER_RUN": "80",
    "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "80",
    "SPORTLOGIC_MATCH_LIMIT": "300",
    "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "150",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "150",
    "SPORTLOGIC_SKIP_ACTIVE_ODDS_WHEN_NO_CURRENT_GAMES": "false",
    "SPORTLOGIC_ACTIVE_ODDS_ALLOW_WITHOUT_CURRENT_GAMES": "true",
    "SPORTLOGIC_ACTIVE_ODDS_TARGETED_CONFIRMATION_ENABLED": "true",
    "SPORTLOGIC_TARGETED_GAME_DETAIL_LIMIT": "20",
    "SPORTLOGIC_ACTIVE_ODDS_GAME_DETAIL_LIMIT": "20",
    "PROVIDER_DAY_DISCOVERY_INCLUDE_SPORTLOGIC_ACTIVE_ODDS": "true",
    "PROVIDER_DAY_DISCOVERY_SPORTLOGIC_ACTIVE_ODDS_PAGES": "1",
    "PROVIDER_DAY_DISCOVERY_BZZOIRO_ODDS_PAGES": "3",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "300",
    "BZZOIRO_ODDS_MATCH_LIMIT": "300",
    "BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT": "220",
    "SSTATS_CONTEXT_MATCH_LIMIT": "300",
    "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": "160",
    "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "80",
    "SSTATS_LOOKBACK_DAYS": "45",
    "SSTATS_RECENT_MATCHES": "8",
    "SSTATS_FORM_MIN_SAMPLE_PER_TEAM": "2",
    "SSTATS_TEAM_KEY_MIN_SCORE": "0.78",
}

SSTATS_HOME_RESULT_KEYS = (
    "homeResult", "homeFTResult", "HomeScore", "homeScore", "home_score",
    "homeGoals", "home_goals", "HomeGoals", "fullTimeHome", "ftHome",
    "score.home", "score.fullTime.home", "scores.home", "result.home",
)
SSTATS_AWAY_RESULT_KEYS = (
    "awayResult", "awayFTResult", "AwayScore", "awayScore", "away_score",
    "awayGoals", "away_goals", "AwayGoals", "fullTimeAway", "ftAway",
    "score.away", "score.fullTime.away", "scores.away", "result.away",
)


def _int_env(name: str, default: int) -> int:
    try:
        return int(float(str(os.getenv(name) or default)))
    except Exception:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name) or default).replace(",", "."))
    except Exception:
        return default


def _truthy_env(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _setattr_safe(obj: Any, name: str, value: Any) -> None:
    try:
        object.__setattr__(obj, name, value)
    except Exception:
        try:
            setattr(obj, name, value)
        except Exception:
            pass


def _dig(row: dict[str, Any], dotted: str) -> Any:
    cur: Any = row
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _first_present(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = _dig(row, key) if "." in key else row.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_float(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _dig(row, key) if "." in key else row.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _date_key(value: Any) -> str:
    try:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).date().isoformat()
    except Exception:
        return ""


def _patch_sstats() -> dict[str, Any]:
    try:
        from app.providers.sstats import SStatsContextProvider
        from app.utils import canonicalize_team_name, team_similarity
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    if getattr(SStatsContextProvider, "_harizon_inventory_coverage_patch", False):
        return {"status": "already_patched"}
    original_init = SStatsContextProvider.__init__
    original_extract_result = SStatsContextProvider._extract_result
    original_resolve_team_key = SStatsContextProvider._resolve_team_key

    def __init__(self: Any, settings: Any) -> None:
        _setattr_safe(settings, "sstats_lookback_days", _int_env("SSTATS_LOOKBACK_DAYS", 45))
        _setattr_safe(settings, "sstats_recent_matches", _int_env("SSTATS_RECENT_MATCHES", 8))
        _setattr_safe(settings, "sstats_form_min_sample_per_team", _int_env("SSTATS_FORM_MIN_SAMPLE_PER_TEAM", 2))
        _setattr_safe(settings, "sstats_request_chunk_days", _int_env("SSTATS_REQUEST_CHUNK_DAYS", 5))
        original_init(self, settings)

    def _extract_result(row: dict[str, Any], side: str) -> float | None:
        value = original_extract_result(row, side)
        if value is not None:
            return value
        return _first_float(row, SSTATS_HOME_RESULT_KEYS if side == "home" else SSTATS_AWAY_RESULT_KEYS)

    def _resolve_team_key(self: Any, team_name: str, canonical_keys: set[str], cache: dict[str, str | None]) -> str | None:
        resolved = original_resolve_team_key(self, team_name, canonical_keys, cache)
        if resolved:
            return resolved
        raw = str(team_name or "")
        canonical = canonicalize_team_name(raw)
        if not canonical:
            cache[raw] = None
            return None
        threshold = max(0.70, min(0.90, _float_env("SSTATS_TEAM_KEY_MIN_SCORE", 0.78)))
        raw_tokens = {token for token in canonical.split() if token}
        best_key: str | None = None
        best_score = 0.0
        for key in canonical_keys:
            score = team_similarity(canonical, key)
            if score > best_score:
                best_key = key
                best_score = score
        if best_key:
            key_tokens = {token for token in str(best_key).split() if token}
            shared = raw_tokens & key_tokens
            if best_score >= 0.88 or (best_score >= threshold and bool(shared)):
                cache[raw] = best_key
                return best_key
        cache[raw] = None
        return None

    SStatsContextProvider.__init__ = __init__
    SStatsContextProvider._extract_result = staticmethod(_extract_result)
    SStatsContextProvider._resolve_team_key = _resolve_team_key
    SStatsContextProvider._harizon_inventory_coverage_patch = True
    return {"status": "patched", "target": "SStatsContextProvider.__init__/_extract_result/_resolve_team_key"}


def _patch_provider_day_discovery() -> dict[str, Any]:
    try:
        from scripts import provider_day_discovery_canonical_pool as pool
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    if getattr(pool, "_harizon_inventory_coverage_patch", False):
        return {"status": "already_patched"}
    original_extract_event = pool.extract_event
    original_build_calls = pool.build_calls

    def extract_event(row: Any, provider: str) -> dict[str, Any] | None:
        if provider == "sportlogic" and isinstance(row, dict):
            nested = None
            for key in ("game", "fixture", "match", "event"):
                value = row.get(key)
                if isinstance(value, dict):
                    nested = value
                    break
            if nested is not None:
                event = original_extract_event(nested, provider)
                if event is None:
                    return None
                if _date_key(event.get("kickoff_utc")) != pool.target_date():
                    return None
                game_id = str(row.get("game_id") or row.get("gameId") or row.get("fixture_id") or row.get("match_id") or "").strip()
                if game_id:
                    event["source_id"] = game_id
                return event
        return original_extract_event(row, provider)

    def build_calls() -> list[Any]:
        calls = list(original_build_calls())
        t = pool.target_date()
        tm = pool.date_plus(t, 1)
        existing = {str(getattr(call, "command", "")) for call in calls}
        _, bzz = pool.first_env("BZZOIRO_API_KEY")
        if bzz:
            headers = {"Authorization": f"Token {bzz}"}
            pages = max(1, min(pool.as_int(pool.env("PROVIDER_DAY_DISCOVERY_BZZOIRO_ODDS_PAGES"), 3), 8))
            for market in ("1x2", "over_under_25", "over_under_15", "over_under_35", "btts"):
                for page in range(1, pages):
                    command = f"odds_best_{market}_offset_{page * 200}"
                    if command in existing:
                        continue
                    calls.append(pool.CallSpec(
                        "bzzoiro",
                        command,
                        "https://sports.bzzoiro.com/api/v2/odds/best/",
                        "odds_secondary_discovery",
                        {"market": market, "date_from": f"{t}T00:00:00Z", "date_to": f"{tm}T00:00:00Z", "limit": 200, "offset": page * 200},
                        headers,
                    ))
        if _truthy_env("PROVIDER_DAY_DISCOVERY_INCLUDE_SPORTLOGIC_ACTIVE_ODDS", True):
            _, sl = pool.first_env("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN")
            if sl:
                root = pool.env("SPORTLOGIC_BASE_URL", "https://api.sportlogic.io/api/v1").rstrip("/")
                header_name = pool.env("SPORTLOGIC_HEADER_NAME", "X-API-Key") or "X-API-Key"
                command = "active_odds_current_page"
                if command not in existing:
                    calls.append(pool.CallSpec("sportlogic", command, f"{root}/odds", "odds_secondary_discovery", {"is_active": "true", "per_page": 100}, {header_name: sl}))
        return calls

    pool.extract_event = extract_event
    pool.build_calls = build_calls
    pool._harizon_inventory_coverage_patch = True
    return {"status": "patched", "target": "provider_day_discovery_canonical_pool"}


def _patch_sstats_fixture_module(module: Any) -> None:
    if getattr(module, "_harizon_inventory_coverage_runtime_patch", False):
        return

    def _is_day_inventory_process() -> bool:
        argv = " ".join(str(part or "") for part in sys.argv).replace("\\", "/")
        if _truthy_env("HARIZON_FORCE_SSTATS_FIXTURE_SOURCE", False):
            return True
        return any(token in argv for token in ("build_day_inventory.py", "app/cli.py", "app.cli", "run-once"))

    def _extract_team(row: dict[str, Any], side: str) -> str:
        if not isinstance(row, dict):
            return ""
        low = str(side or "").lower()
        keys = (
            ("HomeTeamName", "HomeTeam", "HomeName", "Home", "TeamHomeName", "homeTeamName", "homeTeam", "home_team", "home_team_name", "home")
            if low == "home"
            else ("AwayTeamName", "AwayTeam", "AwayName", "Away", "TeamAwayName", "awayTeamName", "awayTeam", "away_team", "away_team_name", "away")
        )
        value = _first_present(row, keys)
        if isinstance(value, dict):
            nested = _first_present(value, ("Name", "name", "Title", "title", "ShortName", "shortName", "short_name", "DisplayName", "displayName"))
            return str(nested or "").strip()
        return str(value or "").strip() if value is not None and not str(value).strip().startswith("{") else ""

    def _extract_league(row: dict[str, Any]) -> str:
        if not isinstance(row, dict):
            return ""
        value = _first_present(row, (
            "LeagueName", "leagueName", "league_name", "League", "league",
            "CompetitionName", "competitionName", "TournamentName", "tournamentName",
            "season.league.name", "season.league.Name", "Season.League.Name", "competition.name",
        ))
        if isinstance(value, dict):
            nested = _first_present(value, ("Name", "name", "Title", "title"))
            return str(nested or "").strip()
        return str(value or "").strip() if value is not None and not str(value).strip().startswith("{") else ""

    def _extract_country(row: dict[str, Any]) -> str:
        if not isinstance(row, dict):
            return ""
        value = _first_present(row, ("Country", "country", "CountryName", "countryName", "LeagueCountry", "league.country", "season.league.country"))
        if isinstance(value, dict):
            value = _first_present(value, ("Name", "name"))
        return str(value or "").strip()

    def _extract_start(row: dict[str, Any]) -> Any:
        if not isinstance(row, dict):
            return None
        value = _first_present(row, ("Date", "date", "DateUtc", "dateUtc", "DateUTC", "dateUTC", "UtcDate", "utcDate", "StartTime", "startTime", "start_time", "Kickoff", "kickoff", "KickoffUtc", "kickoffUtc"))
        if value in (None, ""):
            return None
        try:
            return module.parse_datetime(str(value))
        except Exception:
            return None

    module._is_day_inventory_process = _is_day_inventory_process
    module._extract_team = _extract_team
    module._extract_league = _extract_league
    module._extract_country = _extract_country
    module._extract_start = _extract_start
    module._harizon_inventory_coverage_runtime_patch = True


def _install_sstats_fixture_source() -> dict[str, Any]:
    try:
        from app.services import day_inventory_sstats_fixture_source as source_module
        _patch_sstats_fixture_module(source_module)
        return source_module.install()
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def install() -> dict[str, Any]:
    global _INSTALLED
    for key, value in ENV_DEFAULTS.items():
        os.environ[key] = value
    if _INSTALLED:
        return {"status": "already_installed", "env_applied": len(ENV_DEFAULTS)}
    _INSTALLED = True
    return {
        "status": "installed",
        "env_applied": len(ENV_DEFAULTS),
        "sstats": _patch_sstats(),
        "sstats_fixture_source": _install_sstats_fixture_source(),
        "provider_day_discovery": _patch_provider_day_discovery(),
    }
