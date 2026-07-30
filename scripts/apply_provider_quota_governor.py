from __future__ import annotations

import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

UTC = timezone.utc
POLICY_PATH = Path(os.getenv("PROVIDER_QUOTA_POLICY_PATH") or "config/provider_free_quota_policy.json")
STATE_PATH = Path(os.getenv("PROVIDER_QUOTA_STATE_PATH") or ".data/provider_quota_governor_state.json")
LEGACY_RAPIDAPI_STATE_PATH = Path(os.getenv("RAPIDAPI_PROVIDER_STATE_PATH") or ".data/provider_quota_state.json")
OUT_JSON = Path(".data/exports/latest-provider-quota-governor.json")
OUT_ENV = Path(".data/exports/latest-provider-quota-governor.env")
RECOVERY_VERSION = os.getenv("PROVIDER_QUOTA_RECOVERY_VERSION") or "pdf-free-limits-2026-05-05-v3"
MAX_STATE_BYTES = int(os.getenv("PROVIDER_QUOTA_MAX_STATE_BYTES") or "1048576")
HARD_TIMEOUT_SECONDS = int(os.getenv("PROVIDER_QUOTA_HARD_TIMEOUT_SECONDS") or "25")


def _timeout(_signum: int, _frame: Any) -> None:
    raise TimeoutError("provider quota governor hard timeout")


if hasattr(signal, "SIGALRM"):
    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(max(1, HARD_TIMEOUT_SECONDS))


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return default if raw is None or str(raw).strip() == "" else int(float(str(raw).strip()))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return default if raw is None or str(raw).strip() == "" else float(str(raw).strip())
    except Exception:
        return default


def clamp_int(value: int | float, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def utc_now() -> datetime:
    return datetime.now(UTC)


def today_key() -> str:
    return utc_now().strftime("%Y-%m-%d")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None


def load_json(path: Path, default: Any, *, max_bytes: int | None = None) -> Any:
    try:
        if max_bytes is not None and path.exists() and path.stat().st_size > max_bytes:
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def shell_quote(value: Any) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def rapidapi_used_today(provider_key: str) -> int:
    state = load_json(LEGACY_RAPIDAPI_STATE_PATH, {}, max_bytes=MAX_STATE_BYTES)
    try:
        return int(((((state.get("providers") or {}).get(provider_key) or {}).get("usage") or {}).get(today_key(), {}) or {}).get("requests") or 0)
    except Exception:
        return 0


def account_split(grant: int, cap: int) -> tuple[int, int]:
    if grant <= 0:
        return 0, 0
    first = min(cap, (grant + 1) // 2)
    return first, min(cap, max(0, grant - first))


def generic_env(grant: int, *, enable: tuple[str, ...] = (), caps: tuple[str, ...] = (), fixed: dict[str, str] | None = None) -> dict[str, str]:
    out = {name: bool_text(grant > 0) for name in enable}
    out.update({name: str(grant) for name in caps})
    out.update(fixed or {})
    return out


def strict_market_integrity_env() -> dict[str, str]:
    return {
        "MARKET_INTEGRITY_HARD_GUARD_ENABLED": "true",
        "MARKET_INTEGRITY_CANDIDATE_PATCH_ENABLED": "true",
        "MARKET_INTEGRITY_MIN_BOOKS": "2",
        "MARKET_INTEGRITY_MIN_SOURCES": "1",
        "MARKET_INTEGRITY_SINGLE_SOURCE_MIN_BOOKS": "3",
        "MARKET_INTEGRITY_USE_EXACT_PRICE_SOURCES": "true",
        "MARKET_INTEGRITY_MAX_PRICE_DISPERSION_PCT": "30",
        "MARKET_INTEGRITY_MAX_EXACT_PRICE_DISPERSION_PCT": "22",
        "MARKET_INTEGRITY_MAX_EXACT_LINE_DELTA_PCT": "18",
        "MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS": "1.65",
        "MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS": "3",
        "MATCH_TOTAL_OVER20_MAX_REASONABLE_ODDS": "2.05",
        "PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE": "true",
    }


def odds_api_io_env(grant: int) -> dict[str, str]:
    account1, account2 = account_split(grant, 140)
    out = {
        "ENABLE_ODDS_API_IO": bool_text(grant > 0),
        "ODDS_API_IO_ENABLED": bool_text(grant > 0),
        "ODDS_API_IO_PER_RUN_MAX": str(grant),
        "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": str(grant),
        "ODDS_API_IO_MAX_REQUESTS_PER_RUN": str(grant),
        "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": str(account1),
        "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": str(account2),
        "ODDS_API_IO_ACCOUNT1_MAX_REQUESTS_PER_RUN": str(account1),
        "ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN": str(account2),
        "ODDS_API_IO_PAGE_LIMIT": "100",
        "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": str(clamp_int(max(1, grant // 24), 1, 8)) if grant > 0 else "0",
        "MAX_MATCHES_FOR_ODDS_FETCH": str(clamp_int(60 + grant * 2, 120, 560)) if grant > 0 else "0",
        "ANALYSIS_MATCH_CAP_PER_RUN": str(clamp_int(180 + grant, 240, 620)) if grant > 0 else "120",
        "DIAGNOSTICS_MATCH_LIMIT": str(clamp_int(180 + grant, 240, 620)) if grant > 0 else "120",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT1": "Bet365,Unibet",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT2": "William Hill,Bwin",
        "ODDS_API_IO_BOOKMAKERS": "Bet365,Unibet",
    }
    out.update(strict_market_integrity_env())
    return out


def bzzoiro_env(grant: int) -> dict[str, str]:
    return {
        "ENABLE_BZZOIRO_CONTEXT": bool_text(grant > 0),
        "BZZOIRO_ENABLED": bool_text(grant > 0),
        "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": str(grant),
        "BZZOIRO_PER_RUN_MAX": str(grant),
        "BZZOIRO_MAX_REQUESTS_PER_RUN": str(grant),
        "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN": str(clamp_int(max(2, grant // 4), 2, 20)) if grant > 0 else "0",
        "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN": str(clamp_int(max(2, grant // 4), 2, 20)) if grant > 0 else "0",
        "BZZOIRO_CONTEXT_MATCH_LIMIT": str(clamp_int(40 + grant * 5, 80, 240)) if grant > 0 else "0",
        "BZZOIRO_PAGE_SIZE": "100",
        "BZZOIRO_BEST_ODDS_MARKETS": "1x2,over_under_25,over_under_15,over_under_35,btts",
        "BZZOIRO_ODDS_BEST_MAX_PAGES_PER_MARKET": "2",
        "BZZOIRO_TIMEOUT_SECONDS": "12",
    }


def api_football_env(grant: int) -> dict[str, str]:
    return {
        "API_FOOTBALL_ENABLED": bool_text(grant > 0),
        "ENABLE_API_FOOTBALL": bool_text(grant > 0),
        "API_SPORTS_ENABLED": bool_text(grant > 0),
        "API_FOOTBALL_PER_RUN_MAX": str(grant),
        "API_FOOTBALL_MAX_HTTP_REQUESTS_PER_RUN": str(grant),
        "API_FOOTBALL_FETCH_MATCH_DATES_ONLY": "true",
        "API_FOOTBALL_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 5, 5, 30)) if grant > 0 else "0",
        "API_FOOTBALL_PREDICTIONS_LIMIT": str(clamp_int(max(1, grant // 2), 1, 4)) if grant > 0 else "0",
        "API_FOOTBALL_RATE_LIMIT_COOLDOWN_MINUTES": "240",
    }


def football_data_env(grant: int) -> dict[str, str]:
    return {
        "ENABLE_FOOTBALL_DATA_CONTEXT": bool_text(grant > 0),
        "FOOTBALL_DATA_ENABLED": bool_text(grant > 0),
        "FOOTBALL_DATA_REQUESTS_MAX_PER_RUN": str(grant),
        "FOOTBALL_DATA_MAX_HTTP_REQUESTS_PER_RUN": str(grant),
        "FOOTBALL_DATA_MAX_REQUESTS_PER_RUN": str(grant),
        "FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 14, 30, 180)) if grant > 0 else "0",
        "FOOTBALL_DATA_MATCH_LIMIT": str(clamp_int(grant * 10, 30, 140)) if grant > 0 else "0",
        "FOOTBALL_DATA_STANDINGS_LIMIT": str(clamp_int(grant, 2, 12)) if grant > 0 else "0",
    }


def thesportsdb_env(grant: int) -> dict[str, str]:
    return {
        "ENABLE_THESPORTSDB_CONTEXT": bool_text(grant > 0),
        "THESPORTSDB_CONTEXT_ENABLED": bool_text(grant > 0),
        "THESPORTSDB_REQUESTS_MAX_PER_RUN": str(grant),
        "THESPORTSDB_MAX_HTTP_REQUESTS_PER_RUN": str(grant),
        "THESPORTSDB_MAX_REQUESTS_PER_RUN": str(grant),
        "THESPORTSDB_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 12, 40, 220)) if grant > 0 else "0",
        "THESPORTSDB_MAX_LEAGUES": str(clamp_int(grant * 2, 4, 36)) if grant > 0 else "0",
    }


def weatherapi_env(grant: int) -> dict[str, str]:
    return {
        "WEATHER_CONTEXT_ENABLED": bool_text(grant > 0),
        "WEATHERAPI_ENABLED": bool_text(grant > 0),
        "WEATHERAPI_PER_RUN_MAX": str(grant),
        "WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN": str(grant),
        "WEATHERAPI_MAX_REQUESTS_PER_RUN": str(grant),
        "WEATHER_CONTEXT_MATCH_LIMIT": str(clamp_int(grant, 10, 70)) if grant > 0 else "0",
        "WEATHER_CACHE_TTL_MINUTES": "360",
    }


def news_env(prefix: str, enable_name: str, grant: int) -> dict[str, str]:
    return {
        enable_name: bool_text(grant > 0),
        f"{prefix}_PER_RUN_MAX": str(grant),
        f"{prefix}_MAX_HTTP_REQUESTS_PER_RUN": str(grant),
        f"{prefix}_MAX_REQUESTS_PER_RUN": str(grant),
        f"{prefix}_MATCH_LIMIT": str(clamp_int(grant * 2, 1, 12)) if grant > 0 else "0",
        f"{prefix}_ARTICLES_PER_MATCH": "2",
    }


EXPORTERS: dict[str, Callable[[int], dict[str, str]]] = {
    "odds_api_io": odds_api_io_env,
    "bzzoiro": bzzoiro_env,
    "sstats": lambda grant: generic_env(grant, enable=("SSTATS_ENABLED", "ENABLE_SSTATS_CONTEXT"), caps=("SSTATS_REQUESTS_MAX_PER_RUN", "SSTATS_MAX_HTTP_REQUESTS_PER_RUN", "SSTATS_MAX_REQUESTS_PER_RUN"), fixed={"SSTATS_RECENT_MATCHES": str(clamp_int(4 + grant, 6, 12)) if grant > 0 else "0", "SSTATS_LOOKBACK_DAYS": "45"}),
    "api_football": api_football_env,
    "allsportsapi": lambda grant: generic_env(grant, enable=("ENABLE_ALLSPORTSAPI", "ALLSPORTSAPI_ENABLED"), caps=("ALLSPORTSAPI_PER_RUN_MAX", "ALLSPORTSAPI_MAX_HTTP_REQUESTS_PER_RUN", "ALLSPORTSAPI_MAX_REQUESTS_PER_RUN"), fixed={"ALLSPORTSAPI_MATCH_LIMIT": str(clamp_int(grant * 8, 5, 80)) if grant > 0 else "0"}),
    "oddspapi": lambda grant: generic_env(grant, enable=("ENABLE_ODDSPAPI", "ODDSPAPI_ENABLED"), caps=("ODDSPAPI_PER_RUN_MAX", "ODDSPAPI_MAX_HTTP_REQUESTS_PER_RUN"), fixed={"ODDSPAPI_MATCH_LIMIT": str(clamp_int(grant * 6, 2, 10)) if grant > 0 else "0", "ODDSPAPI_TOURNAMENT_LIMIT": "1" if grant > 0 else "0", "ODDSPAPI_MIN_FETCH_INTERVAL_MINUTES": "360"}),
    "futrixmetrics": lambda grant: generic_env(grant, enable=("ENABLE_FUTRIXMETRICS_CONTEXT", "FUTRIXMETRICS_ENABLED"), caps=("FUTRIXMETRICS_PER_RUN_MAX", "FUTRIXMETRICS_MAX_HTTP_REQUESTS_PER_RUN", "FUTRIXMETRICS_MAX_REQUESTS_PER_RUN"), fixed={"FUTRIXMETRICS_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 3, 6, 72)) if grant > 0 else "0"}),
    "football_data": football_data_env,
    "thesportsdb": thesportsdb_env,
    "sportlogic": lambda grant: generic_env(grant, enable=("SPORTLOGIC_ENABLED", "SPORTLOGIC_BROAD_FALLBACK_ENABLED", "SPORTLOGIC_CONTROLLED_ODDS_ENABLED"), caps=("SPORTLOGIC_PER_RUN_MAX", "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN", "SPORTLOGIC_MAX_REQUESTS_PER_RUN"), fixed={"SPORTLOGIC_QUERY_GUARD_ENABLED": "true", "SPORTLOGIC_BROAD_FALLBACK_ENABLED": "false", "SPORTLOGIC_PER_PAGE": "100" if grant > 0 else "0", "SPORTLOGIC_MIN_REQUEST_INTERVAL_SECONDS": "6.5", "SPORTLOGIC_429_COOLDOWN_SECONDS": "90"}),
    "highlightly": lambda grant: generic_env(grant, enable=("HIGHLIGHTLY_ENABLED",), caps=("HIGHLIGHTLY_PER_RUN_MAX", "HIGHLIGHTLY_MAX_HTTP_REQUESTS_PER_RUN", "HIGHLIGHTLY_MAX_REQUESTS_PER_RUN"), fixed={"HIGHLIGHTLY_BASE_URL": "https://soccer.highlightly.net", "HIGHLIGHTLY_SMOKE_PATH": "/leagues"}),
    "sportsbook_api": lambda grant: generic_env(grant, enable=("RAPIDAPI_SPORTSBOOK_PROBE_ENABLED", "SPORTSBOOK_RAPIDAPI_ENABLED"), caps=("SPORTSBOOK_RAPIDAPI_MAX_REQUESTS_PER_RUN",), fixed={"RAPIDAPI_SPORTSBOOK_DAILY_LIMIT": str(rapidapi_used_today("sportsbook_api") + grant)}),
    "odds_feed": lambda grant: generic_env(grant, enable=("RAPIDAPI_ODDS_FEED_PROBE_ENABLED", "ODDS_FEED_RAPIDAPI_ENABLED"), caps=("ODDS_FEED_RAPIDAPI_MAX_REQUESTS_PER_RUN",), fixed={"RAPIDAPI_ODDS_FEED_DAILY_LIMIT": str(rapidapi_used_today("odds_feed") + grant)}),
    "free_live_football_data": lambda grant: generic_env(grant, enable=("RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED", "FREE_FOOTBALL_RAPIDAPI_ENABLED"), caps=("FREE_FOOTBALL_RAPIDAPI_MAX_REQUESTS_PER_RUN",), fixed={"RAPIDAPI_FREE_FOOTBALL_DAILY_LIMIT": str(rapidapi_used_today("free_live_football_data") + grant)}),
    "weatherapi": weatherapi_env,
    "openweathermap": lambda grant: generic_env(grant, enable=("OPENWEATHERMAP_ENABLED",), caps=("OPENWEATHERMAP_PER_RUN_MAX", "OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN", "OPENWEATHERMAP_MAX_REQUESTS_PER_RUN")),
    "open_meteo": lambda grant: generic_env(grant, enable=("OPEN_METEO_ENABLED", "OPENMETEO_ENABLED"), caps=("OPEN_METEO_PER_RUN_MAX", "OPEN_METEO_MAX_HTTP_REQUESTS_PER_RUN", "OPEN_METEO_MAX_REQUESTS_PER_RUN", "OPENMETEO_MAX_REQUESTS_PER_RUN")),
    "meteostat": lambda grant: generic_env(grant, enable=("METEOSTAT_RAPIDAPI_ENABLED", "WEATHER_METEOSTAT_FALLBACK_ENABLED"), caps=("METEOSTAT_RAPIDAPI_MAX_REQUESTS_PER_RUN", "METEOSTAT_RAPIDAPI_MAX_HTTP_REQUESTS_PER_RUN")),
    "newsapi": lambda grant: news_env("NEWSAPI", "ENABLE_NEWSAPI_CONTEXT", grant),
    "gnews": lambda grant: news_env("GNEWS", "ENABLE_GNEWS_CONTEXT", grant),
    "currents": lambda grant: {"CURRENTS_NEWS_CONTEXT_ENABLED": bool_text(grant > 0), "CURRENTS_NEWS_PER_RUN_MAX": str(grant), "CURRENTS_MAX_HTTP_REQUESTS_PER_RUN": str(grant), "CURRENTS_MAX_REQUESTS_PER_RUN": str(grant), "CURRENTS_MATCH_LIMIT": str(clamp_int(grant * 2, 4, 80)) if grant > 0 else "0", "CURRENTS_ARTICLES_PER_MATCH": "2"},
    "newsdata": lambda grant: generic_env(grant, enable=("NEWSDATA_ENABLED",), caps=("NEWSDATA_PER_RUN_MAX", "NEWSDATA_MAX_HTTP_REQUESTS_PER_RUN", "NEWSDATA_MAX_REQUESTS_PER_RUN")),
    "guardian": lambda grant: generic_env(grant, enable=("GUARDIAN_ENABLED",), caps=("GUARDIAN_PER_RUN_MAX", "GUARDIAN_MAX_HTTP_REQUESTS_PER_RUN", "GUARDIAN_MAX_REQUESTS_PER_RUN")),
    "clubelo": lambda grant: generic_env(grant, enable=("CLUBELO_ENABLED",), caps=("CLUBELO_MAX_REQUESTS_PER_DAY", "CLUBELO_MAX_REQUESTS_PER_RUN"), fixed={"CLUBELO_BASE_URL": "http://api.clubelo.com"}),
    "football_data_co_uk": lambda grant: generic_env(grant, enable=("FOOTBALL_DATA_CO_UK_ENABLED",), caps=("FOOTBALL_DATA_CO_UK_MAX_REQUESTS_PER_DAY", "FOOTBALL_DATA_CO_UK_MAX_REQUESTS_PER_RUN")),
    "wikidata": lambda grant: generic_env(grant, enable=("WIKIDATA_ENABLED",), caps=("WIKIDATA_PER_RUN_MAX", "WIKIDATA_MAX_HTTP_REQUESTS_PER_RUN"), fixed={"WIKIDATA_MAX_REQUESTS_PER_DAY": str(max(grant, 1)), "WIKIDATA_SPARQL_MAX_REQUESTS_PER_DAY": str(clamp_int(grant // 4, 1, 4)) if grant > 0 else "0"}),
}


def provider_numbers(key: str, spec: dict[str, Any]) -> tuple[float, float, int, int, int, int, int]:
    prefix = key.upper()
    daily_budget = max(0.0, env_float(f"{prefix}_DAILY_BUDGET", float(spec.get("daily_budget") or 0)))
    per_run_max = max(0, env_int(f"{prefix}_PER_RUN_MAX", int(spec.get("max_requests_per_run") or 0)))
    bucket_default = int(spec.get("bucket_max") or max(per_run_max * 3, min(daily_budget, per_run_max * 24)))
    bucket_max = max(0.0, env_float(f"{prefix}_BUCKET_MAX", float(bucket_default)))
    reserve_tokens = max(0, env_int(f"{prefix}_RESERVE_TOKENS", int(spec.get("reserve_tokens") or per_run_max)))
    minute_spacing = max(0, env_int(f"{prefix}_MIN_SPACING_MINUTES", int(spec.get("min_spacing_minutes") or 0)))
    start_default = min(int(bucket_max), reserve_tokens + per_run_max)
    initial_tokens = max(0, env_int(f"{prefix}_INITIAL_TOKENS", start_default))
    min_start_tokens = max(0, env_int(f"{prefix}_MIN_START_TOKENS", start_default))
    return daily_budget, bucket_max, per_run_max, reserve_tokens, minute_spacing, initial_tokens, min_start_tokens


def apply_refill(row: dict[str, Any], *, daily_budget: float, bucket_max: float, now: datetime) -> float:
    current = float(row.get("tokens") or 0.0)
    last = parse_dt(row.get("last_refill_at") or row.get("updated_at"))
    if last is None:
        return min(bucket_max, current)
    refill = (daily_budget / 86400.0) * max(0.0, (now - last).total_seconds()) if daily_budget > 0 else 0.0
    return min(bucket_max, current + refill)


def spacing_allows(row: dict[str, Any], minutes: int, now: datetime) -> tuple[bool, str | None]:
    if minutes <= 0:
        return True, None
    last = parse_dt(row.get("last_grant_at"))
    if last is None:
        return True, None
    left = minutes - ((now - last).total_seconds() / 60.0)
    return (left <= 0, None if left <= 0 else f"spacing_active:{round(left, 1)}m")


def maybe_recover(row: dict[str, Any], *, bucket_max: float, min_start_tokens: int) -> bool:
    if not env_bool("PROVIDER_QUOTA_RECOVERY_ENABLED", True) or min_start_tokens <= 0 or bucket_max <= 0:
        return False
    if str(row.get("last_recovery_version") or "") == RECOVERY_VERSION:
        return False
    row["tokens"] = max(float(row.get("tokens") or 0.0), min(float(bucket_max), float(min_start_tokens)))
    row["last_recovery_version"] = RECOVERY_VERSION
    row["recovery_reason"] = "versioned_pdf_free_limits_floor"
    return True


def refill_and_grant(state: dict[str, Any], key: str, spec: dict[str, Any]) -> dict[str, Any]:
    daily_budget, bucket_max, per_run_max, reserve_tokens, spacing_minutes, initial_tokens, min_start_tokens = provider_numbers(key, spec)
    now = utc_now()
    today = today_key()
    row = state.setdefault("providers", {}).setdefault(key, {})
    if not row:
        row.update({"tokens": min(bucket_max, float(initial_tokens)), "created_at": now.isoformat(), "used_today": 0})
    if str(row.get("last_refill_date") or "") != today:
        row["last_refill_date"] = today
        row["used_today"] = 0
    row["tokens"] = apply_refill(row, daily_budget=daily_budget, bucket_max=bucket_max, now=now)
    recovered = maybe_recover(row, bucket_max=bucket_max, min_start_tokens=min_start_tokens)
    tokens_before = float(row.get("tokens") or 0.0)
    spacing_ok, skip_reason = spacing_allows(row, spacing_minutes, now)
    grant = min(per_run_max, int(max(0.0, tokens_before - float(reserve_tokens)))) if spacing_ok else 0
    if grant <= 0 and env_bool(f"{key.upper()}_ALLOW_RESERVE_SPEND", False):
        grant = min(per_run_max, max(0, env_int(f"{key.upper()}_RESERVE_SPEND_MAX", 1)), int(tokens_before))
    if not env_bool("PROVIDER_QUOTA_GOVERNOR_DRY_RUN", False):
        row["tokens"] = max(0.0, tokens_before - float(grant))
        if grant > 0:
            row["last_grant_at"] = now.isoformat()
        row["used_today"] = int(row.get("used_today") or 0) + grant
    row.update({
        "updated_at": now.isoformat(),
        "last_refill_at": now.isoformat(),
        "daily_budget": daily_budget,
        "bucket_max": bucket_max,
        "per_run_max": per_run_max,
        "reserve_tokens": reserve_tokens,
        "minute_spacing": spacing_minutes,
        "quota_basis": spec.get("free_quota_basis"),
        "role": spec.get("role"),
    })
    return {
        "provider": key,
        "role": spec.get("role"),
        "quota_basis": spec.get("free_quota_basis"),
        "daily_budget": daily_budget,
        "bucket_max": bucket_max,
        "per_run_max": per_run_max,
        "reserve_tokens": reserve_tokens,
        "minute_spacing": spacing_minutes,
        "tokens_before": round(tokens_before, 3),
        "recovered": recovered,
        "granted": grant,
        "tokens_after": round(float(row.get("tokens") or 0.0), 3),
        "enabled_for_run": grant > 0,
        "skip_reason": skip_reason,
    }


def write_env(exports: dict[str, str]) -> None:
    OUT_ENV.parent.mkdir(parents=True, exist_ok=True)
    OUT_ENV.write_text("\n".join(f"export {key}={shell_quote(value)}" for key, value in sorted(exports.items())) + "\n", encoding="utf-8")
    github_env = os.getenv("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as handle:
            for key, value in sorted(exports.items()):
                safe = str(value).replace("\n", " ")
                handle.write(f"{key}={safe}\n")
    print(f"provider quota governor exported {len(exports)} env vars", flush=True)


def fallback_exports() -> dict[str, str]:
    out = {
        "PROVIDER_QUOTA_GOVERNOR_ACTIVE": "fallback",
        "PROVIDER_FREE_QUOTA_POLICY_ENABLED": "true",
        "FREE_CONTEXT_RUNTIME_ENABLED": "true",
        "RUNTIME_PROVIDER_DIAGNOSTICS_ENABLED": "true",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "ODDS_API_IO_PER_RUN_MAX": os.getenv("ODDS_API_IO_PER_RUN_MAX") or "160",
        "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": os.getenv("ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN") or "160",
        "ODDS_API_IO_MAX_REQUESTS_PER_RUN": os.getenv("ODDS_API_IO_MAX_REQUESTS_PER_RUN") or "160",
        "MAX_MATCHES_FOR_ODDS_FETCH": "360",
        "ANALYSIS_MATCH_CAP_PER_RUN": "420",
        "DIAGNOSTICS_MATCH_LIMIT": "420",
    }
    out.update(strict_market_integrity_env())
    return out


def build_exports_and_state() -> tuple[dict[str, str], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    policy = load_json(POLICY_PATH, {}, max_bytes=MAX_STATE_BYTES)
    provider_specs: dict[str, dict[str, Any]] = policy.get("providers") or {}
    state = load_json(STATE_PATH, {"providers": {}}, max_bytes=MAX_STATE_BYTES)
    rows: list[dict[str, Any]] = []
    exports: dict[str, str] = {
        "PROVIDER_QUOTA_GOVERNOR_ACTIVE": "true",
        "PROVIDER_FREE_QUOTA_POLICY_ENABLED": "true",
        "FREE_CONTEXT_RUNTIME_ENABLED": "true",
        "RUNTIME_PROVIDER_DIAGNOSTICS_ENABLED": "true",
        "RAPIDAPI_DEFAULT_DAILY_LIMIT": os.getenv("RAPIDAPI_DEFAULT_DAILY_LIMIT") or "1",
        "NEWS_CONTEXT_CACHE_TTL_MINUTES": os.getenv("NEWS_CONTEXT_CACHE_TTL_MINUTES") or "240",
        "WEATHER_CACHE_TTL_MINUTES": os.getenv("WEATHER_CACHE_TTL_MINUTES") or "360",
        "SCOREBAT_CACHE_TTL_MINUTES": os.getenv("SCOREBAT_CACHE_TTL_MINUTES") or "240",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
    }
    exports.update(strict_market_integrity_env())
    for key, spec in provider_specs.items():
        row = refill_and_grant(state, key, spec)
        rows.append(row)
        exporter = EXPORTERS.get(key)
        if exporter is not None:
            exports.update(exporter(int(row["granted"])))
    active_context = sum(1 for item in rows if item["enabled_for_run"] and str(item.get("role") or "").endswith("context"))
    active_news = sum(1 for item in rows if item["enabled_for_run"] and "news" in str(item.get("role") or ""))
    active_weather = sum(1 for item in rows if item["enabled_for_run"] and "weather" in str(item.get("role") or ""))
    exports["PREMIUM_CONTEXT_SHORTLIST_LIMIT"] = str(clamp_int(16 + active_context * 3, 16, 70))
    exports["PREMIUM_NEWS_SHORTLIST_LIMIT"] = str(clamp_int(1 + active_news * 2, 1, 12))
    exports["CONTEXT_ENRICHMENT_MATCH_LIMIT"] = str(clamp_int(180 + active_context * 24 + active_weather * 10, 220, 520))
    state.update({
        "updated_at": utc_now().isoformat(),
        "mode": "bounded_pdf_free_limit_token_bucket",
        "recovery_version": RECOVERY_VERSION,
        "policy_version": policy.get("policy_version"),
        "runs_per_day_assumption": policy.get("runs_per_day_assumption"),
        "safety_factor_default": policy.get("safety_factor_default"),
    })
    return exports, state, rows, policy


def main() -> int:
    print("provider quota governor starting", flush=True)
    if not env_bool("PROVIDER_QUOTA_GOVERNOR_ENABLED", True):
        payload = {"created_at": utc_now().isoformat(), "enabled": False, "reason": "PROVIDER_QUOTA_GOVERNOR_ENABLED=false", "exports": {}, "providers": []}
        write_json(OUT_JSON, payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    try:
        exports, state, rows, policy = build_exports_and_state()
        write_json(STATE_PATH, state)
        enabled_count = sum(1 for row in rows if row.get("enabled_for_run"))
        payload = {
            "created_at": utc_now().isoformat(),
            "enabled": True,
            "mode": "bounded_pdf_free_limit_token_bucket",
            "recovery_version": RECOVERY_VERSION,
            "policy_path": str(POLICY_PATH),
            "state_path": str(STATE_PATH),
            "local_env_path": str(OUT_ENV),
            "source_document": policy.get("source_document") or "api_free_limits_ru.pdf / api_free_limits_ru.docx",
            "note": "Bounded governor: local-only token bucket, capped state reads, context/news/weather never confirm price.",
            "providers_enabled_for_run": enabled_count,
            "providers": rows,
            "exports": exports,
        }
    except Exception as exc:
        exports = fallback_exports()
        payload = {
            "created_at": utc_now().isoformat(),
            "enabled": True,
            "mode": "fallback_safe_static_limits",
            "error": f"{type(exc).__name__}: {exc}",
            "providers": [],
            "exports": exports,
        }
    write_env(exports)
    write_json(OUT_JSON, payload)
    print(json.dumps({k: payload[k] for k in ("created_at", "enabled", "mode") if k in payload}, ensure_ascii=False, sort_keys=True), flush=True)
    if hasattr(signal, "SIGALRM"):
        signal.alarm(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
