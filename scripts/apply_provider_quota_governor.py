from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

UTC = timezone.utc
STATE_PATH = Path(os.getenv("PROVIDER_QUOTA_STATE_PATH") or ".data/provider_quota_governor_state.json")
LEGACY_RAPIDAPI_STATE_PATH = Path(os.getenv("RAPIDAPI_PROVIDER_STATE_PATH") or ".data/provider_quota_state.json")
OUT_JSON = Path(".data/exports/latest-provider-quota-governor.json")
OUT_ENV = Path(".data/exports/latest-provider-quota-governor.env")

RECOVERY_VERSION = os.getenv("PROVIDER_QUOTA_RECOVERY_VERSION") or "real-quota-v1"


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(str(raw).strip()))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except Exception:
        return default


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value)
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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def shell_quote(value: Any) -> str:
    text = str(value)
    return "'" + text.replace("'", "'\"'\"'") + "'"


def today_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def clamp_int(value: int | float, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def rapidapi_used_today(provider_key: str) -> int:
    state = load_json(LEGACY_RAPIDAPI_STATE_PATH, {})
    try:
        return int(
            (((state.get("providers") or {}).get(provider_key) or {}).get("usage") or {})
            .get(today_key(), {})
            .get("requests")
            or 0
        )
    except Exception:
        return 0


@dataclass(frozen=True)
class ProviderPlan:
    key: str
    title: str
    default_daily_budget: int
    default_bucket_max: int
    default_per_run_max: int
    default_reserve_tokens: int
    build_env: Callable[[int, float], dict[str, str]]
    default_minute_spacing: int = 0

    @property
    def prefix(self) -> str:
        return self.key.upper()


def build_provider_plans() -> list[ProviderPlan]:
    return [
        ProviderPlan(
            key="odds_api_io",
            title="odds-api.io odds/bootstrap; quota 100 requests/hour, 2 bookmakers",
            default_daily_budget=384,
            default_bucket_max=96,
            default_per_run_max=8,
            default_reserve_tokens=16,
            default_minute_spacing=0,
            build_env=lambda grant, tokens: {
                "ENABLE_ODDS_API_IO": bool_text(grant > 0),
                "ODDS_API_IO_PAGE_LIMIT": "100",
                "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": str(clamp_int(grant, 1, 8)) if grant > 0 else "0",
                "MAX_MATCHES_FOR_ODDS_FETCH": str(clamp_int(grant * 10, 30, 90)) if grant > 0 else "0",
                "ANALYSIS_MATCH_CAP_PER_RUN": str(clamp_int(160 + grant * 28, 160, 420)) if grant > 0 else "100",
                "DIAGNOSTICS_MATCH_LIMIT": str(clamp_int(160 + grant * 28, 160, 420)) if grant > 0 else "100",
                # Account is limited to 2 bookmakers; do not widen this unless the plan changes.
                "ODDS_API_IO_BOOKMAKERS": "Bet365,Unibet",
            },
        ),
        ProviderPlan(
            key="oddspapi",
            title="OddsPapi secondary odds; quota 250 requests/month",
            default_daily_budget=8,
            default_bucket_max=24,
            default_per_run_max=1,
            default_reserve_tokens=4,
            default_minute_spacing=180,
            build_env=lambda grant, tokens: {
                "ENABLE_ODDSPAPI": bool_text(grant > 0),
                "ODDSPAPI_MATCH_LIMIT": str(clamp_int(grant * 6, 2, 8)) if grant > 0 else "0",
                "ODDSPAPI_TOURNAMENT_LIMIT": "1" if grant > 0 else "0",
                "ODDSPAPI_MIN_FETCH_INTERVAL_MINUTES": "360",
            },
        ),
        ProviderPlan(
            key="allsportsapi",
            title="AllSportsAPI soccer trial/free; unknown limit, conservative",
            default_daily_budget=12,
            default_bucket_max=32,
            default_per_run_max=1,
            default_reserve_tokens=4,
            default_minute_spacing=120,
            build_env=lambda grant, tokens: {
                "ENABLE_ALLSPORTSAPI": bool_text(grant > 0),
                "ALLSPORTSAPI_MATCH_LIMIT": str(clamp_int(grant * 6, 2, 10)) if grant > 0 else "0",
                "ALLSPORTSAPI_MIN_FETCH_INTERVAL_MINUTES": "240",
            },
        ),
        ProviderPlan(
            key="sstats",
            title="SStats form/xG context; account quota unknown, controlled medium usage",
            default_daily_budget=96,
            default_bucket_max=160,
            default_per_run_max=6,
            default_reserve_tokens=12,
            build_env=lambda grant, tokens: {
                "SSTATS_ENABLED": bool_text(grant > 0),
                "ENABLE_SSTATS_CONTEXT": bool_text(grant > 0),
                "SSTATS_RECENT_MATCHES": str(clamp_int(4 + grant, 6, 12)) if grant > 0 else "0",
                "SSTATS_LOOKBACK_DAYS": "45",
                "CONTEXT_ENRICHMENT_MATCH_LIMIT": str(clamp_int(100 + grant * 34, 100, 340)) if grant > 0 else "70",
                "PREMIUM_CONTEXT_SHORTLIST_LIMIT": str(clamp_int(14 + grant * 4, 14, 42)) if grant > 0 else "8",
            },
        ),
        ProviderPlan(
            key="bzzoiro",
            title="bzzoiro.com prediction context; free forever, no rate limits",
            default_daily_budget=10000,
            default_bucket_max=20000,
            default_per_run_max=50,
            default_reserve_tokens=0,
            build_env=lambda grant, tokens: {
                "ENABLE_BZZOIRO_CONTEXT": "true",
                "PREMIUM_CONTEXT_SHORTLIST_LIMIT": str(clamp_int(24 + grant, 24, 80)),
            },
        ),
        ProviderPlan(
            key="api_football",
            title="api-football/API-Sports football; quota 100 requests/day, 10 requests/min",
            default_daily_budget=78,
            default_bucket_max=120,
            default_per_run_max=3,
            default_reserve_tokens=18,
            default_minute_spacing=2,
            build_env=lambda grant, tokens: {
                "API_FOOTBALL_ENABLED": bool_text(grant > 0),
                "API_FOOTBALL_FETCH_MATCH_DATES_ONLY": "true",
                "API_FOOTBALL_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 5, 5, 20)) if grant > 0 else "0",
                "API_FOOTBALL_PREDICTIONS_LIMIT": str(clamp_int(grant, 1, 4)) if grant > 0 else "0",
                "API_FOOTBALL_RATE_LIMIT_COOLDOWN_MINUTES": "180",
            },
        ),
        ProviderPlan(
            key="football_data",
            title="football-data.org registered free; 10 requests/min",
            default_daily_budget=220,
            default_bucket_max=360,
            default_per_run_max=8,
            default_reserve_tokens=24,
            default_minute_spacing=1,
            build_env=lambda grant, tokens: {
                "ENABLE_FOOTBALL_DATA_CONTEXT": bool_text(grant > 0),
                "FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 18, 20, 140)) if grant > 0 else "0",
                "FOOTBALL_DATA_MATCH_LIMIT": str(clamp_int(grant * 12, 20, 100)) if grant > 0 else "0",
                "FOOTBALL_DATA_STANDINGS_LIMIT": str(clamp_int(grant * 2, 2, 10)) if grant > 0 else "0",
            },
        ),
        ProviderPlan(
            key="thesportsdb",
            title="TheSportsDB free; 30 requests/min",
            default_daily_budget=360,
            default_bucket_max=720,
            default_per_run_max=12,
            default_reserve_tokens=36,
            default_minute_spacing=1,
            build_env=lambda grant, tokens: {
                "ENABLE_THESPORTSDB_CONTEXT": bool_text(grant > 0),
                "THESPORTSDB_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 16, 24, 180)) if grant > 0 else "0",
                "THESPORTSDB_MAX_LEAGUES": str(clamp_int(grant * 2, 4, 24)) if grant > 0 else "0",
            },
        ),
        ProviderPlan(
            key="futrixmetrics",
            title="FutrixMetrics; quota 5000 requests/month",
            default_daily_budget=110,
            default_bucket_max=240,
            default_per_run_max=5,
            default_reserve_tokens=20,
            default_minute_spacing=3,
            build_env=lambda grant, tokens: {
                "ENABLE_FUTRIXMETRICS_CONTEXT": bool_text(grant > 0),
                "FUTRIXMETRICS_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 4, 4, 20)) if grant > 0 else "0",
            },
        ),
        ProviderPlan(
            key="newsapi",
            title="NewsAPI; quota 100 requests/day",
            default_daily_budget=34,
            default_bucket_max=70,
            default_per_run_max=1,
            default_reserve_tokens=12,
            default_minute_spacing=30,
            build_env=lambda grant, tokens: {
                "ENABLE_NEWSAPI_CONTEXT": bool_text(grant > 0),
                "NEWSAPI_MATCH_LIMIT": str(clamp_int(grant * 3, 1, 5)) if grant > 0 else "0",
                "NEWSAPI_ARTICLES_PER_MATCH": "2",
                "PREMIUM_NEWS_SHORTLIST_LIMIT": str(clamp_int(grant, 1, 2)) if grant > 0 else "0",
            },
        ),
        ProviderPlan(
            key="gnews",
            title="GNews; quota 100 requests/day",
            default_daily_budget=34,
            default_bucket_max=70,
            default_per_run_max=1,
            default_reserve_tokens=12,
            default_minute_spacing=30,
            build_env=lambda grant, tokens: {
                "ENABLE_GNEWS_CONTEXT": bool_text(grant > 0),
                "GNEWS_MATCH_LIMIT": str(clamp_int(grant * 3, 1, 5)) if grant > 0 else "0",
                "GNEWS_ARTICLES_PER_MATCH": "2",
                "PREMIUM_NEWS_SHORTLIST_LIMIT": str(clamp_int(grant, 1, 2)) if grant > 0 else "0",
            },
        ),
        ProviderPlan(
            key="weather",
            title="WeatherAPI/OpenWeather/OpenMeteo overlay; WeatherAPI 100K/month, OWM 1000/day",
            default_daily_budget=360,
            default_bucket_max=720,
            default_per_run_max=6,
            default_reserve_tokens=36,
            default_minute_spacing=1,
            build_env=lambda grant, tokens: {
                "WEATHER_CONTEXT_ENABLED": bool_text(grant > 0),
                "WEATHERAPI_ENABLED": bool_text(grant > 0),
                "OPENWEATHERMAP_ENABLED": bool_text(grant > 0),
                "OPENMETEO_ENABLED": bool_text(grant > 0),
                "WEATHER_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 4, 8, 24)) if grant > 0 else "0",
                "WEATHER_CACHE_TTL_MINUTES": "240",
            },
        ),
        ProviderPlan(
            key="rapidapi_sportsbook",
            title="Sportsbook API; quota 50 requests/day",
            default_daily_budget=18,
            default_bucket_max=50,
            default_per_run_max=1,
            default_reserve_tokens=6,
            default_minute_spacing=60,
            build_env=lambda grant, tokens: {
                "RAPIDAPI_SPORTSBOOK_PROBE_ENABLED": bool_text(grant > 0),
                "RAPIDAPI_SPORTSBOOK_DAILY_LIMIT": str(rapidapi_used_today("sportsbook_api") + grant),
            },
        ),
        ProviderPlan(
            key="rapidapi_odds_feed",
            title="OddsFeed; quota 500 requests/month",
            default_daily_budget=12,
            default_bucket_max=36,
            default_per_run_max=1,
            default_reserve_tokens=4,
            default_minute_spacing=120,
            build_env=lambda grant, tokens: {
                "RAPIDAPI_ODDS_FEED_PROBE_ENABLED": bool_text(grant > 0),
                "RAPIDAPI_ODDS_FEED_DAILY_LIMIT": str(rapidapi_used_today("odds_feed") + grant),
            },
        ),
        ProviderPlan(
            key="rapidapi_free_football",
            title="FreeAPILiveFootballData; quota 100 requests/month",
            default_daily_budget=3,
            default_bucket_max=10,
            default_per_run_max=1,
            default_reserve_tokens=2,
            default_minute_spacing=360,
            build_env=lambda grant, tokens: {
                "RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED": bool_text(grant > 0),
                "RAPIDAPI_FREE_FOOTBALL_DAILY_LIMIT": str(rapidapi_used_today("free_live_football_data") + grant),
            },
        ),
        ProviderPlan(
            key="rapidapi_sportapi7",
            title="SportAPI/API-Sports sample probe; current endpoint is not football-useful",
            default_daily_budget=0,
            default_bucket_max=1,
            default_per_run_max=0,
            default_reserve_tokens=0,
            build_env=lambda grant, tokens: {
                "RAPIDAPI_SPORTAPI7_PROBE_ENABLED": bool_text(grant > 0),
                "RAPIDAPI_SPORTAPI7_DAILY_LIMIT": str(rapidapi_used_today("sportapi7") + grant),
            },
        ),
        ProviderPlan(
            key="rapidapi_meteostat",
            title="Meteostat; quota 500 requests/month",
            default_daily_budget=10,
            default_bucket_max=30,
            default_per_run_max=1,
            default_reserve_tokens=4,
            default_minute_spacing=120,
            build_env=lambda grant, tokens: {
                "METEOSTAT_RAPIDAPI_ENABLED": bool_text(grant > 0),
                "WEATHER_METEOSTAT_FALLBACK_ENABLED": bool_text(grant > 0),
            },
        ),
    ]


def provider_numbers(plan: ProviderPlan) -> tuple[float, float, int, int, int, int, int]:
    prefix = plan.prefix
    daily_budget = max(0.0, env_float(f"{prefix}_DAILY_BUDGET", float(plan.default_daily_budget)))
    bucket_max = max(0.0, env_float(f"{prefix}_BUCKET_MAX", float(plan.default_bucket_max)))
    per_run_max = max(0, env_int(f"{prefix}_PER_RUN_MAX", plan.default_per_run_max))
    reserve_tokens = max(0, env_int(f"{prefix}_RESERVE_TOKENS", plan.default_reserve_tokens))
    minute_spacing = max(0, env_int(f"{prefix}_MIN_SPACING_MINUTES", plan.default_minute_spacing))
    default_start = reserve_tokens + per_run_max
    initial_tokens = max(0, env_int(f"{prefix}_INITIAL_TOKENS", default_start))
    min_start_tokens = max(0, env_int(f"{prefix}_MIN_START_TOKENS", default_start))
    return daily_budget, bucket_max, per_run_max, reserve_tokens, minute_spacing, initial_tokens, min_start_tokens


def apply_continuous_refill(row: dict[str, Any], *, daily_budget: float, bucket_max: float, now: datetime) -> float:
    current = float(row.get("tokens") or 0.0)
    last = parse_dt(row.get("last_refill_at") or row.get("updated_at"))
    if last is None:
        row["last_refill_at"] = now.isoformat()
        return min(bucket_max, current)

    elapsed_seconds = max(0.0, (now - last).total_seconds())
    refill = (daily_budget / 86400.0) * elapsed_seconds if daily_budget > 0 else 0.0
    tokens = min(bucket_max, current + refill)
    row["last_refill_at"] = now.isoformat()
    return tokens


def maybe_version_recover(row: dict[str, Any], *, bucket_max: float, min_start_tokens: int) -> bool:
    if not env_bool("PROVIDER_QUOTA_RECOVERY_ENABLED", True):
        return False
    if min_start_tokens <= 0 or bucket_max <= 0:
        return False
    if str(row.get("last_recovery_version") or "") == RECOVERY_VERSION:
        return False

    floor = min(float(bucket_max), float(min_start_tokens))
    current = float(row.get("tokens") or 0.0)
    if current < floor:
        row["tokens"] = floor
    row["last_recovery_version"] = RECOVERY_VERSION
    row["recovery_reason"] = "versioned_real_quota_floor"
    return True


def spacing_allows(row: dict[str, Any], spacing_minutes: int, now: datetime) -> tuple[bool, str | None]:
    if spacing_minutes <= 0:
        return True, None
    last_grant_at = parse_dt(row.get("last_grant_at"))
    if last_grant_at is None:
        return True, None
    elapsed = (now - last_grant_at).total_seconds() / 60.0
    if elapsed >= spacing_minutes:
        return True, None
    return False, f"spacing_active:{round(spacing_minutes - elapsed, 1)}m"


def refill_and_grant(state: dict[str, Any], plan: ProviderPlan) -> dict[str, Any]:
    daily_budget, bucket_max, per_run_max, reserve_tokens, spacing_minutes, initial_tokens, min_start_tokens = provider_numbers(plan)
    providers = state.setdefault("providers", {})
    row = providers.setdefault(plan.key, {})
    now = datetime.now(UTC)
    today = today_key()

    if not row:
        row.update(
            {
                "tokens": min(bucket_max, float(initial_tokens)),
                "last_refill_date": today,
                "last_refill_at": now.isoformat(),
                "used_today": 0,
                "created_at": now.isoformat(),
            }
        )

    if str(row.get("last_refill_date") or "") != today:
        row["last_refill_date"] = today
        row["used_today"] = 0

    row["tokens"] = apply_continuous_refill(row, daily_budget=daily_budget, bucket_max=bucket_max, now=now)
    recovered = maybe_version_recover(row, bucket_max=bucket_max, min_start_tokens=min_start_tokens)

    tokens_before = float(row.get("tokens") or 0.0)
    available = max(0.0, tokens_before - float(reserve_tokens))
    spacing_ok, skip_reason = spacing_allows(row, spacing_minutes, now)
    grant = min(per_run_max, int(available)) if spacing_ok else 0

    if grant <= 0 and env_bool(f"{plan.prefix}_ALLOW_RESERVE_SPEND", False):
        reserve_spend_cap = max(0, env_int(f"{plan.prefix}_RESERVE_SPEND_MAX", 1))
        grant = min(per_run_max, reserve_spend_cap, int(tokens_before))

    if not env_bool("PROVIDER_QUOTA_GOVERNOR_DRY_RUN", False):
        row["tokens"] = max(0.0, tokens_before - float(grant))
        if grant > 0:
            row["last_grant_at"] = now.isoformat()
        row["used_today"] = int(row.get("used_today") or 0) + grant

    row["updated_at"] = now.isoformat()
    row["daily_budget"] = daily_budget
    row["bucket_max"] = bucket_max
    row["per_run_max"] = per_run_max
    row["reserve_tokens"] = reserve_tokens
    row["minute_spacing"] = spacing_minutes
    row["initial_tokens"] = initial_tokens
    row["min_start_tokens"] = min_start_tokens

    return {
        "provider": plan.key,
        "title": plan.title,
        "daily_budget": daily_budget,
        "bucket_max": bucket_max,
        "per_run_max": per_run_max,
        "reserve_tokens": reserve_tokens,
        "minute_spacing": spacing_minutes,
        "initial_tokens": initial_tokens,
        "min_start_tokens": min_start_tokens,
        "tokens_before": round(tokens_before, 3),
        "recovered": recovered,
        "granted": grant,
        "tokens_after": round(float(row.get("tokens") or 0.0), 3),
        "enabled_for_run": grant > 0,
        "skip_reason": skip_reason,
    }


def write_env(exports: dict[str, str]) -> None:
    github_env = os.getenv("GITHUB_ENV")
    lines = [f"{key}={value}" for key, value in sorted(exports.items())]

    OUT_ENV.parent.mkdir(parents=True, exist_ok=True)
    OUT_ENV.write_text(
        "\n".join(f"export {key}={shell_quote(value)}" for key, value in sorted(exports.items())) + "\n",
        encoding="utf-8",
    )

    if github_env:
        with open(github_env, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    for line in lines:
        print(line)


def main() -> int:
    if not env_bool("PROVIDER_QUOTA_GOVERNOR_ENABLED", True):
        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "enabled": False,
            "reason": "PROVIDER_QUOTA_GOVERNOR_ENABLED=false",
            "exports": {},
            "providers": [],
        }
        write_json(OUT_JSON, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    state = load_json(STATE_PATH, {"providers": {}})
    provider_rows: list[dict[str, Any]] = []
    exports: dict[str, str] = {
        "PROVIDER_QUOTA_GOVERNOR_ACTIVE": "true",
        "RAPIDAPI_DEFAULT_DAILY_LIMIT": os.getenv("RAPIDAPI_DEFAULT_DAILY_LIMIT") or "1",
        "WEATHER_CACHE_TTL_MINUTES": os.getenv("WEATHER_CACHE_TTL_MINUTES") or "240",
        "SCOREBAT_CACHE_TTL_MINUTES": os.getenv("SCOREBAT_CACHE_TTL_MINUTES") or "240",
        # Currents is budgeted in docs/config, but no Currents provider is present in this repo yet.
        "CURRENTS_NEWS_CONTEXT_ENABLED": "false",
    }

    for plan in build_provider_plans():
        row = refill_and_grant(state, plan)
        provider_rows.append(row)
        exports.update(plan.build_env(int(row["granted"]), float(row["tokens_after"])))

    active_premium = sum(
        1
        for item in provider_rows
        if item["enabled_for_run"] and item["provider"] not in {"odds_api_io", "weather"}
    )
    max_shortlist = clamp_int(12 + active_premium * 4, 12, 80)
    exports["PREMIUM_CONTEXT_SHORTLIST_LIMIT"] = str(
        min(int(exports.get("PREMIUM_CONTEXT_SHORTLIST_LIMIT", max_shortlist) or max_shortlist), max_shortlist)
    )
    exports["CONTEXT_ENRICHMENT_MATCH_LIMIT"] = str(
        min(int(exports.get("CONTEXT_ENRICHMENT_MATCH_LIMIT", "340") or 340), 340)
    )

    state["updated_at"] = datetime.now(UTC).isoformat()
    state["mode"] = "continuous_token_bucket_real_quotas"
    state["recovery_version"] = RECOVERY_VERSION
    write_json(STATE_PATH, state)
    write_env(exports)

    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "enabled": True,
        "mode": "continuous_token_bucket_real_quotas",
        "recovery_version": RECOVERY_VERSION,
        "state_path": str(STATE_PATH),
        "local_env_path": str(OUT_ENV),
        "legacy_rapidapi_state_path": str(LEGACY_RAPIDAPI_STATE_PATH),
        "note": (
            "Budgets are derived from provided free-tier quotas. High-quota/no-limit providers feed broad coverage; "
            "daily/monthly providers are reserved for shortlist confirmation."
        ),
        "providers": provider_rows,
        "exports": exports,
    }
    write_json(OUT_JSON, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
