from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(".").resolve()
UTC = timezone.utc
POLICY_VERSION = "harizon-runtime-policy-v3-fast-inventory"
OUT_PATH = ROOT / ".data" / "exports" / "latest-harizon-runtime-policy.json"


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_github_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[key] = value
    github_env = os.getenv("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as fh:
            for key in sorted(values):
                fh.write(f"{key}={values[key]}\n")
    else:
        for key in sorted(values):
            print(f"{key}={values[key]}")


def run_script(path: str, required: bool = False) -> dict[str, object]:
    script = ROOT / path
    if not script.exists():
        result = {"script": path, "status": "missing", "required": required}
        if required:
            raise SystemExit(f"required runtime script missing: {path}")
        return result
    proc = subprocess.run([sys.executable, str(script)], check=False)
    status = "ok" if proc.returncode == 0 else "failed"
    result = {"script": path, "status": status, "returncode": proc.returncode, "required": required}
    if required and proc.returncode != 0:
        raise SystemExit(proc.returncode)
    return result


def policy_value(name: str, default: str) -> str:
    return os.getenv(f"HARIZON_{name}") or default


def base_env() -> dict[str, str]:
    now_local = datetime.now(UTC).astimezone(app_tz())
    today = now_local.date().isoformat()
    return {
        "HARIZON_RUNTIME_POLICY_VERSION": POLICY_VERSION,
        "HARIZON_IGNORE_STALE_PROVIDER_BUDGET": "true",
        "PUBLISH_WINDOW_HOURS": policy_value("PUBLISH_WINDOW_HOURS", "12"),
        "MIN_KICKOFF_LEAD_MINUTES": policy_value("MIN_KICKOFF_LEAD_MINUTES", "25"),
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "true",
        "CONTEXT_ENRICHMENT_MATCH_LIMIT": policy_value("CONTEXT_ENRICHMENT_MATCH_LIMIT", "180"),
        "PREMIUM_CONTEXT_SHORTLIST_LIMIT": policy_value("PREMIUM_CONTEXT_SHORTLIST_LIMIT", "72"),
        "PREMIUM_NEWS_SHORTLIST_LIMIT": policy_value("PREMIUM_NEWS_SHORTLIST_LIMIT", "6"),
        "VALUE_HINT_MIN_EDGE_PCT": policy_value("VALUE_HINT_MIN_EDGE_PCT", "0.8"),
        "NEAR_MISS_ENRICHMENT_QUEUE_LIMIT": policy_value("NEAR_MISS_ENRICHMENT_QUEUE_LIMIT", "90"),
        "NEAR_MISS_ENRICHMENT_MIN_EV_PCT": policy_value("NEAR_MISS_ENRICHMENT_MIN_EV_PCT", "3.0"),
        "NEAR_MISS_ENRICHMENT_MIN_EDGE_PP": policy_value("NEAR_MISS_ENRICHMENT_MIN_EDGE_PP", "1.5"),
        "DAY_INVENTORY_NEAR_WINDOW_HOURS": policy_value("DAY_INVENTORY_NEAR_WINDOW_HOURS", "12"),
        "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",

        # Removed providers must stay hard-disabled and must not appear as live sources.
        "ENABLE_BOOKIES_API": "false",
        "BOOKIES_API_ENABLED": "false",
        "BOOKIES_API_ODDS_FETCH_LIMIT": "0",
        "ENABLE_API_FOOTBALL": "false",
        "API_FOOTBALL_ENABLED": "false",
        "API_FOOTBALL_KEY": "",
        "API_FOOTBALL_PER_RUN_MAX": "0",
        "API_FOOTBALL_CONTEXT_MATCH_LIMIT": "0",
        "API_FOOTBALL_PREDICTIONS_LIMIT": "0",
        "ENABLE_ODDSPAPI": "false",
        "ODDSPAPI_ENABLED": "false",
        "ODDSPAPI_MATCH_LIMIT": "0",
        "ODDSPAPI_CONTEXT_MATCH_LIMIT": "0",
        "ODDSPAPI_PER_RUN_MAX": "0",
        "ODDSPAPI_MAX_HTTP_REQUESTS_PER_RUN": "0",

        "MATCH_BOOTSTRAP_PROVIDER": "odds_api_io",
        "ENABLE_ODDS_API_IO": "true",
        "ODDS_API_IO_ENABLED": "true",
        "DAY_INVENTORY_BOOTSTRAP_PROVIDER": "odds_api_io",
        "DAY_INVENTORY_FORCE_PROVIDER_MERGE": "false",
        "DAY_INVENTORY_COVERAGE_MAX_REBUILD": "false",
        "DAY_INVENTORY_USE_FOR_RUN": "true",
        "DAY_INVENTORY_BUILD_TIMEOUT_SECONDS": policy_value("DAY_INVENTORY_BUILD_TIMEOUT_SECONDS", "180"),
        "DAY_INVENTORY_MIN_READY_RATIO_PCT": os.getenv("DAY_INVENTORY_MIN_READY_RATIO_PCT") or "55",
        "COVERAGE_MAXIMIZE_TODAY": "true",
        "COVERAGE_MAXIMIZE_UNTIL_LOCAL_DATE": today,
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT1": "Bet365,Unibet",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT2": "Betfair Exchange,Sbobet",
        "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": policy_value("ODDS_API_IO_ACCOUNT1_PER_RUN_MAX", "100"),
        "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": policy_value("ODDS_API_IO_ACCOUNT2_PER_RUN_MAX", "100"),
        "ODDS_API_IO_PER_RUN_MAX": policy_value("ODDS_API_IO_PER_RUN_MAX", "200"),
        "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": policy_value("ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN", "200"),
        "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": policy_value("ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT", "18"),
        "MAX_MATCHES_FOR_ODDS_FETCH": policy_value("MAX_MATCHES_FOR_ODDS_FETCH", "420"),
        "TARGET_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "CONSENSUS_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "SHARP_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",

        "SSTATS_ENABLED": "true",
        "ENABLE_SSTATS_CONTEXT": "true",
        "SSTATS_TARGET_NEAR_MISS_FIRST": "true",
        "SSTATS_PER_RUN_MAX": policy_value("SSTATS_PER_RUN_MAX", "150"),
        "SSTATS_REQUESTS_MAX_PER_RUN": policy_value("SSTATS_REQUESTS_MAX_PER_RUN", "150"),
        "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": policy_value("SSTATS_MAX_HTTP_REQUESTS_PER_RUN", "150"),
        "SSTATS_CONTEXT_MATCH_LIMIT": policy_value("SSTATS_CONTEXT_MATCH_LIMIT", "180"),
        "BZZOIRO_PER_RUN_MAX": policy_value("BZZOIRO_PER_RUN_MAX", "1000"),
        "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": policy_value("BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN", "1000"),
        "BZZOIRO_CONTEXT_MATCH_LIMIT": policy_value("BZZOIRO_CONTEXT_MATCH_LIMIT", "180"),
        "BZZOIRO_MAX_PAGES": policy_value("BZZOIRO_MAX_PAGES", "120"),
        "ENABLE_FOOTBALL_DATA_CONTEXT": "true",
        "FOOTBALL_DATA_ENABLED": "true",
        "FOOTBALL_DATA_PER_RUN_MAX": os.getenv("FOOTBALL_DATA_PER_RUN_MAX") or "8",
        "FOOTBALL_DATA_REQUESTS_MAX_PER_RUN": os.getenv("FOOTBALL_DATA_REQUESTS_MAX_PER_RUN") or "8",
        "FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": os.getenv("FOOTBALL_DATA_CONTEXT_MATCH_LIMIT") or "48",
        "ENABLE_THESPORTSDB_CONTEXT": "true",
        "THESPORTSDB_CONTEXT_ENABLED": "true",
        "THESPORTSDB_PER_RUN_MAX": os.getenv("THESPORTSDB_PER_RUN_MAX") or "12",
        "THESPORTSDB_REQUESTS_MAX_PER_RUN": os.getenv("THESPORTSDB_REQUESTS_MAX_PER_RUN") or "12",
        "THESPORTSDB_CONTEXT_MATCH_LIMIT": os.getenv("THESPORTSDB_CONTEXT_MATCH_LIMIT") or "60",

        "ENABLE_SPORTLOGIC": "false",
        "SPORTLOGIC_ENABLED": "false",
        "SPORTLOGIC_PER_RUN_MAX": "0",
        "SPORTLOGIC_MATCH_LIMIT": "0",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
        "SPORTLOGIC_ODDS_DISABLED_REASON": "missing_or_invalid_price_payload",
        "WEATHER_CONTEXT_MATCH_LIMIT": policy_value("WEATHER_CONTEXT_MATCH_LIMIT", "24"),
        "WEATHERAPI_PER_RUN_MAX": policy_value("WEATHERAPI_PER_RUN_MAX", "12"),
        "WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN": policy_value("WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN", "12"),
        "OPENWEATHERMAP_PER_RUN_MAX": policy_value("OPENWEATHERMAP_PER_RUN_MAX", "8"),
        "OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN": policy_value("OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN", "8"),

        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_BOOKS": "2",
        "CONTROLLED_FALLBACK_ALLOWED_FAMILIES": "totals,dnb,btts,teamtotals,spreads",
        "CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES": "totals,dnb,btts",
        "CONTROLLED_FALLBACK_TIER_A_MIN_EV_PCT": "7.0",
        "CONTROLLED_FALLBACK_TIER_A_MIN_EDGE_PP": "3.5",
        "CONTROLLED_FALLBACK_TIER_A_MIN_CONFIDENCE": "65.0",
        "CONTROLLED_FALLBACK_TIER_A_MIN_QUALITY": "65.0",
        "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS": "2",
        "CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES": "totals,dnb,btts",
        "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": "5.0",
        "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": "2.2",
        "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE": "62.0",
        "CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY": "62.0",
        "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "2",
        "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": "5.0",
        "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": "2.2",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT": "false",
        "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_FILTER_POOL_BY_TIME": "true",
        "DETAILED_RUN_REPORT_INCLUDE_RUNTIME_POLICY": "true",
        "DETAILED_RUN_REPORT_INCLUDE_BANKROLL_BLOCK": "true",
    }


def main() -> int:
    applied_env = base_env()
    append_github_env(applied_env)
    ordered_scripts = [
        ("scripts/apply_api_capacity_and_keypool_policy.py", False),
        ("scripts/apply_enrichment_cycle_policy.py", False),
        ("scripts/apply_day_inventory_policy.py", True),
        ("scripts/apply_quality_first_runtime_policy.py", False),
        ("scripts/apply_provider_request_budget.py", False),
        ("scripts/check_publication_runtime_syntax.py", True),
    ]
    script_results = [run_script(path, required=required) for path, required in ordered_scripts]
    append_github_env(applied_env)
    payload = {
        "policy_version": POLICY_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "local_now": datetime.now(UTC).astimezone(app_tz()).isoformat(),
        "env_updates": applied_env,
        "script_results": script_results,
        "notes": [
            "Fast inventory mode disables heavy provider merge so Build day inventory cannot consume the whole run.",
            "Removed providers are hard-disabled: bookies_api, api_football, oddspapi.",
            "Context requests are concentrated on priced matches and near-miss/value-first candidates.",
            "Odds-api.io remains routed as two account-specific bookmaker groups and merged by the provider.",
        ],
    }
    write_json(OUT_PATH, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
