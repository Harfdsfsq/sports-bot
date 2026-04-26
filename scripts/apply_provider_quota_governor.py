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


def clamp_int(value: int, low: int, high: int) -> int:
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
    build_env: Callable[[int, int], dict[str, str]]

    @property
    def prefix(self) -> str:
        return self.key.upper()


def build_provider_plans() -> list[ProviderPlan]:
    return [
        ProviderPlan(
            key="odds_api_io",
            title="Odds API IO odds/bootstrap",
            default_daily_budget=96,
            default_bucket_max=160,
            default_per_run_max=8,
            default_reserve_tokens=12,
            build_env=lambda grant, tokens: {
                "ENABLE_ODDS_API_IO": bool_text(grant > 0),
                "ODDS_API_IO_PAGE_LIMIT": "100",
                "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": str(clamp_int(grant, 1, 6)) if grant > 0 else "0",
                "MAX_MATCHES_FOR_ODDS_FETCH": str(clamp_int(grant * 10, 20, 70)) if grant > 0 else "0",
                "ANALYSIS_MATCH_CAP_PER_RUN": str(clamp_int(120 + grant * 20, 120, 340)) if grant > 0 else "80",
                "DIAGNOSTICS_MATCH_LIMIT": str(clamp_int(120 + grant * 20, 120, 340)) if grant > 0 else "80",
            },
        ),
        ProviderPlan(
            key="oddspapi",
            title="OddsPAPI secondary odds",
            default_daily_budget=6,
            default_bucket_max=14,
            default_per_run_max=1,
            default_reserve_tokens=2,
            build_env=lambda grant, tokens: {
                "ENABLE_ODDSPAPI": bool_text(grant > 0),
                "ODDSPAPI_MATCH_LIMIT": str(clamp_int(grant * 4, 2, 8)) if grant > 0 else "0",
                "ODDSPAPI_TOURNAMENT_LIMIT": str(clamp_int(grant, 1, 2)) if grant > 0 else "0",
                "ODDSPAPI_MIN_FETCH_INTERVAL_MINUTES": "360",
            },
        ),
        ProviderPlan(
            key="allsportsapi",
            title="AllSportsAPI odds supplement",
            default_daily_budget=6,
            default_bucket_max=14,
            default_per_run_max=1,
            default_reserve_tokens=2,
            build_env=lambda grant, tokens: {
                "ENABLE_ALLSPORTSAPI": bool_text(grant > 0),
                "ALLSPORTSAPI_MATCH_LIMIT": str(clamp_int(grant * 4, 2, 8)) if grant > 0 else "0",
                "ALLSPORTSAPI_MIN_FETCH_INTERVAL_MINUTES": "240",
            },
        ),
        ProviderPlan(
            key="sstats",
            title="SStats form/xG context",
            default_daily_budget=48,
            default_bucket_max=96,
            default_per_run_max=4,
            default_reserve_tokens=8,
            build_env=lambda grant, tokens: {
                "SSTATS_ENABLED": bool_text(grant > 0),
                "ENABLE_SSTATS_CONTEXT": bool_text(grant > 0),
                "SSTATS_RECENT_MATCHES": str(clamp_int(4 + grant, 4, 10)) if grant > 0 else "0",
                "SSTATS_LOOKBACK_DAYS": "45",
                "CONTEXT_ENRICHMENT_MATCH_LIMIT": str(clamp_int(80 + grant * 28, 80, 260)) if grant > 0 else "60",
                "PREMIUM_CONTEXT_SHORTLIST_LIMIT": str(clamp_int(12 + grant * 3, 12, 36)) if grant > 0 else "8",
            },
        ),
        ProviderPlan(
            key="bzzoiro",
            title="Bzzoiro prediction context",
            default_daily_budget=20,
            default_bucket_max=48,
            default_per_run_max=2,
            default_reserve_tokens=4,
            build_env=lambda grant, tokens: {
                "ENABLE_BZZOIRO_CONTEXT": bool_text(grant > 0),
                "PREMIUM_CONTEXT_SHORTLIST_LIMIT": str(clamp_int(12 + grant * 6, 12, 42)) if grant > 0 else "8",
            },
        ),
        ProviderPlan(
            key="api_football",
            title="API-Football context/predictions",
            default_daily_budget=24,
            default_bucket_max=60,
            default_per_run_max=3,
            default_reserve_tokens=5,
            build_env=lambda grant, tokens: {
                "API_FOOTBALL_ENABLED": bool_text(grant > 0),
                "API_FOOTBALL_FETCH_MATCH_DATES_ONLY": "true",
                "API_FOOTBALL_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 4, 4, 16)) if grant > 0 else "0",
                "API_FOOTBALL_PREDICTIONS_LIMIT": str(clamp_int(grant, 1, 4)) if grant > 0 else "0",
                "API_FOOTBALL_RATE_LIMIT_COOLDOWN_MINUTES": "180",
            },
        ),
        ProviderPlan(
            key="football_data",
            title="football-data.org standings/context",
            default_daily_budget=18,
            default_bucket_max=42,
            default_per_run_max=2,
            default_reserve_tokens=4,
            build_env=lambda grant, tokens: {
                "ENABLE_FOOTBALL_DATA_CONTEXT": bool_text(grant > 0),
                "FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 16, 12, 80)) if grant > 0 else "0",
                "FOOTBALL_DATA_MATCH_LIMIT": str(clamp_int(grant * 12, 12, 60)) if grant > 0 else "0",
                "FOOTBALL_DATA_STANDINGS_LIMIT": str(clamp_int(grant * 2, 2, 6)) if grant > 0 else "0",
            },
        ),
        ProviderPlan(
            key="thesportsdb",
            title="TheSportsDB context",
            default_daily_budget=18,
            default_bucket_max=42,
            default_per_run_max=2,
            default_reserve_tokens=4,
            build_env=lambda grant, tokens: {
                "ENABLE_THESPORTSDB_CONTEXT": bool_text(grant > 0),
                "THESPORTSDB_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 16, 12, 80)) if grant > 0 else "0",
                "THESPORTSDB_MAX_LEAGUES": str(clamp_int(grant * 3, 3, 12)) if grant > 0 else "0",
            },
        ),
        ProviderPlan(
            key="futrixmetrics",
            title="FutrixMetrics premium context",
            default_daily_budget=4,
            default_bucket_max=10,
            default_per_run_max=1,
            default_reserve_tokens=1,
            build_env=lambda grant, tokens: {
                "ENABLE_FUTRIXMETRICS_CONTEXT": bool_text(grant > 0),
                "FUTRIXMETRICS_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 3, 2, 6)) if grant > 0 else "0",
            },
        ),
        ProviderPlan(
            key="newsapi",
            title="NewsAPI injury/news overlay",
            default_daily_budget=8,
            default_bucket_max=20,
            default_per_run_max=1,
            default_reserve_tokens=2,
            build_env=lambda grant, tokens: {
                "ENABLE_NEWSAPI_CONTEXT": bool_text(grant > 0),
                "NEWSAPI_MATCH_LIMIT": str(clamp_int(grant * 2, 1, 4)) if grant > 0 else "0",
                "NEWSAPI_ARTICLES_PER_MATCH": "2",
                "PREMIUM_NEWS_SHORTLIST_LIMIT": str(clamp_int(grant, 1, 2)) if grant > 0 else "0",
            },
        ),
        ProviderPlan(
            key="gnews",
            title="GNews injury/news overlay",
            default_daily_budget=8,
            default_bucket_max=20,
            default_per_run_max=1,
            default_reserve_tokens=2,
            build_env=lambda grant, tokens: {
                "ENABLE_GNEWS_CONTEXT": bool_text(grant > 0),
                "GNEWS_MATCH_LIMIT": str(clamp_int(grant * 2, 1, 4)) if grant > 0 else "0",
                "GNEWS_ARTICLES_PER_MATCH": "2",
                "PREMIUM_NEWS_SHORTLIST_LIMIT": str(clamp_int(grant, 1, 2)) if grant > 0 else "0",
            },
        ),
        ProviderPlan(
            key="weather",
            title="Weather overlays",
            default_daily_budget=18,
            default_bucket_max=42,
            default_per_run_max=2,
            default_reserve_tokens=4,
            build_env=lambda grant, tokens: {
                "WEATHER_CONTEXT_ENABLED": bool_text(grant > 0),
                "WEATHERAPI_ENABLED": bool_text(grant > 0),
                "OPENWEATHERMAP_ENABLED": bool_text(grant > 0),
                "OPENMETEO_ENABLED": bool_text(grant > 0),
                "WEATHER_CONTEXT_MATCH_LIMIT": str(clamp_int(grant * 5, 4, 12)) if grant > 0 else "0",
                "WEATHER_CACHE_TTL_MINUTES": "240",
            },
        ),
        ProviderPlan(
            key="rapidapi_sportsbook",
            title="RapidAPI Sportsbook probe",
            default_daily_budget=1,
            default_bucket_max=3,
            default_per_run_max=1,
            default_reserve_tokens=0,
            build_env=lambda grant, tokens: {
                "RAPIDAPI_SPORTSBOOK_PROBE_ENABLED": bool_text(grant > 0),
                "RAPIDAPI_SPORTSBOOK_DAILY_LIMIT": str(rapidapi_used_today("sportsbook_api") + grant),
            },
        ),
        ProviderPlan(
            key="rapidapi_odds_feed",
            title="RapidAPI Odds Feed probe",
            default_daily_budget=1,
            default_bucket_max=3,
            default_per_run_max=1,
            default_reserve_tokens=0,
            build_env=lambda grant, tokens: {
                "RAPIDAPI_ODDS_FEED_PROBE_ENABLED": bool_text(grant > 0),
                "RAPIDAPI_ODDS_FEED_DAILY_LIMIT": str(rapidapi_used_today("odds_feed") + grant),
            },
        ),
        ProviderPlan(
            key="rapidapi_free_football",
            title="RapidAPI Free Football probe",
            default_daily_budget=1,
            default_bucket_max=3,
            default_per_run_max=1,
            default_reserve_tokens=0,
            build_env=lambda grant, tokens: {
                "RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED": bool_text(grant > 0),
                "RAPIDAPI_FREE_FOOTBALL_DAILY_LIMIT": str(rapidapi_used_today("free_live_football_data") + grant),
            },
        ),
        ProviderPlan(
            key="rapidapi_sportapi7",
            title="RapidAPI SportAPI7 probe",
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
            title="RapidAPI Meteostat weather fallback",
            default_daily_budget=2,
            default_bucket_max=6,
            default_per_run_max=1,
            default_reserve_tokens=1,
            build_env=lambda grant, tokens: {
                "METEOSTAT_RAPIDAPI_ENABLED": bool_text(grant > 0),
                "WEATHER_METEOSTAT_FALLBACK_ENABLED": bool_text(grant > 0),
            },
        ),
    ]


def provider_numbers(plan: ProviderPlan) -> tuple[int, int, int, int, int, int]:
    prefix = plan.prefix
    daily_budget = max(0, env_int(f"{prefix}_DAILY_BUDGET", plan.default_daily_budget))
    bucket_max = max(0, env_int(f"{prefix}_BUCKET_MAX", plan.default_bucket_max))
    per_run_max = max(0, env_int(f"{prefix}_PER_RUN_MAX", plan.default_per_run_max))
    reserve_tokens = max(0, env_int(f"{prefix}_RESERVE_TOKENS", plan.default_reserve_tokens))

    # Critical fix: initial bucket must include reserve + spendable per-run grant.
    default_start = reserve_tokens + per_run_max
    initial_tokens = max(0, env_int(f"{prefix}_INITIAL_TOKENS", default_start))
    min_start_tokens = max(0, env_int(f"{prefix}_MIN_START_TOKENS", default_start))
    return daily_budget, bucket_max, per_run_max, reserve_tokens, initial_tokens, min_start_tokens


def maybe_recover_underfilled_bucket(
    row: dict[str, Any],
    *,
    bucket_max: int,
    min_start_tokens: int,
    today: str,
) -> bool:
    if not env_bool("PROVIDER_QUOTA_RECOVERY_ENABLED", True):
        return False
    if min_start_tokens <= 0 or bucket_max <= 0:
        return False
    if str(row.get("last_recovery_date") or "") == today:
        return False

    current_tokens = max(0, int(row.get("tokens") or 0))
    floor = min(bucket_max, min_start_tokens)
    if current_tokens >= floor:
        return False

    row["tokens"] = floor
    row["last_recovery_date"] = today
    row["recovery_reason"] = "underfilled_initial_bucket"
    return True


def refill_and_grant(state: dict[str, Any], plan: ProviderPlan) -> dict[str, Any]:
    daily_budget, bucket_max, per_run_max, reserve_tokens, initial_tokens, min_start_tokens = provider_numbers(plan)
    providers = state.setdefault("providers", {})
    row = providers.setdefault(plan.key, {})
    now = datetime.now(UTC)
    today = today_key()

    if not row:
        row.update(
            {
                "tokens": min(bucket_max, initial_tokens),
                "last_refill_date": today,
                "used_today": 0,
                "created_at": now.isoformat(),
            }
        )

    if str(row.get("last_refill_date") or "") != today:
        # Daily refill: unused tokens carry over up to BUCKET_MAX.
        row["tokens"] = min(bucket_max, int(row.get("tokens") or 0) + daily_budget)
        row["last_refill_date"] = today
        row["used_today"] = 0
        row.pop("last_recovery_date", None)
        row.pop("recovery_reason", None)

    recovered = maybe_recover_underfilled_bucket(
        row,
        bucket_max=bucket_max,
        min_start_tokens=min_start_tokens,
        today=today,
    )

    tokens_before = max(0, int(row.get("tokens") or 0))
    available = max(0, tokens_before - reserve_tokens)
    grant = min(per_run_max, available)

    # Emergency valve: disabled by default. Use only after checking provider dashboard.
    if grant <= 0 and env_bool(f"{plan.prefix}_ALLOW_RESERVE_SPEND", False):
        reserve_spend_cap = max(0, env_int(f"{plan.prefix}_RESERVE_SPEND_MAX", 1))
        grant = min(per_run_max, reserve_spend_cap, tokens_before)

    if not env_bool("PROVIDER_QUOTA_GOVERNOR_DRY_RUN", False):
        row["tokens"] = max(0, tokens_before - grant)
        row["used_today"] = int(row.get("used_today") or 0) + grant

    row["updated_at"] = now.isoformat()
    row["daily_budget"] = daily_budget
    row["bucket_max"] = bucket_max
    row["per_run_max"] = per_run_max
    row["reserve_tokens"] = reserve_tokens
    row["initial_tokens"] = initial_tokens
    row["min_start_tokens"] = min_start_tokens

    return {
        "provider": plan.key,
        "title": plan.title,
        "daily_budget": daily_budget,
        "bucket_max": bucket_max,
        "per_run_max": per_run_max,
        "reserve_tokens": reserve_tokens,
        "initial_tokens": initial_tokens,
        "min_start_tokens": min_start_tokens,
        "tokens_before": tokens_before,
        "recovered": recovered,
        "granted": grant,
        "tokens_after": int(row.get("tokens") or 0),
        "enabled_for_run": grant > 0,
    }


def write_env(exports: dict[str, str]) -> None:
    github_env = os.getenv("GITHUB_ENV")
    lines = [f"{key}={value}" for key, value in sorted(exports.items())]

    OUT_ENV.parent.mkdir(parents=True, exist_ok=True)
    OUT_ENV.write_text("\n".join(f"export {key}={shell_quote(value)}" for key, value in sorted(exports.items())) + "\n", encoding="utf-8")

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
    }

    for plan in build_provider_plans():
        row = refill_and_grant(state, plan)
        provider_rows.append(row)
        exports.update(plan.build_env(int(row["granted"]), int(row["tokens_after"])))

    active_premium = sum(
        1
        for item in provider_rows
        if item["enabled_for_run"] and item["provider"] not in {"odds_api_io", "weather"}
    )
    max_shortlist = clamp_int(10 + active_premium * 3, 10, 42)
    exports["PREMIUM_CONTEXT_SHORTLIST_LIMIT"] = str(
        min(int(exports.get("PREMIUM_CONTEXT_SHORTLIST_LIMIT", max_shortlist) or max_shortlist), max_shortlist)
    )
    exports["CONTEXT_ENRICHMENT_MATCH_LIMIT"] = str(
        min(int(exports.get("CONTEXT_ENRICHMENT_MATCH_LIMIT", "260") or 260), 260)
    )

    state["updated_at"] = datetime.now(UTC).isoformat()
    state["mode"] = "token_bucket"
    write_json(STATE_PATH, state)
    write_env(exports)

    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "enabled": True,
        "state_path": str(STATE_PATH),
        "local_env_path": str(OUT_ENV),
        "legacy_rapidapi_state_path": str(LEGACY_RAPIDAPI_STATE_PATH),
        "note": (
            "Conservative token-bucket budgets with recovery floors. "
            "The local wrapper sources latest-provider-quota-governor.env before run-once."
        ),
        "providers": provider_rows,
        "exports": exports,
    }
    write_json(OUT_JSON, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
