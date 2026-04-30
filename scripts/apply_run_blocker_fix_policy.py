from __future__ import annotations

"""Runtime blocker fixes for the football value pipeline.

This layer is intentionally applied after provider budget allocation. It does not
force weak picks. It fixes data/coverage blockers that were visible in the latest
run logs:
- odds-api.io was effectively narrowed to Bet365/Unibet, causing single-book and
  no market-derived signals;
- weather had budget but no requests because all shortlisted matches missed a
  venue/city payload;
- api-football was still blanked by old runtime policy;
- publication B-tier was too binary: it required A-grade EV while the target is
  3-5 quality picks/day, not forced picks.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
GITHUB_ENV = os.getenv("GITHUB_ENV")
OUT = ROOT / ".data" / "exports" / "latest-run-blocker-fix-policy.json"
UTC = timezone.utc
POLICY_VERSION = "v1-run-blocker-fixes-odds-weather-api-football-btier"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_env(env: dict[str, str]) -> None:
    if GITHUB_ENV:
        with open(GITHUB_ENV, "a", encoding="utf-8") as fh:
            for key in sorted(env):
                fh.write(f"{key}={env[key]}\n")
    else:
        for key in sorted(env):
            print(f"{key}={env[key]}")


def main() -> int:
    env: dict[str, str] = {
        "RUN_BLOCKER_FIX_POLICY_ACTIVE": "true",
        "RUN_BLOCKER_FIX_POLICY_VERSION": POLICY_VERSION,

        # Use all configured sharp/target books. The latest log showed only
        # Bet365/Unibet in requested_bookmakers, which destroys consensus checks
        # and leaves most candidates as single-book/single-source.
        "TARGET_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "CONSENSUS_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "SHARP_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "ODDS_API_IO_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT1": "Bet365,Unibet",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT2": "Betfair Exchange,Sbobet",
        "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": "70",
        "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": "70",
        "ODDS_API_IO_PER_RUN_MAX": "140",
        "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": "140",
        "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "24",
        "MAX_MATCHES_FOR_ODDS_FETCH": "180",
        "MIN_BOOKS_FOR_CONSENSUS": "2",
        "STRONG_MARKET_MIN_BOOKS": "2",

        # Build a wider candidate pool, then let publication quality gates filter.
        # This fixes raw=0 / market-derived=0 without publishing weak signals.
        "MARKET_DERIVED_CANDIDATES_ENABLED": "true",
        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MARKET_DERIVED_MIN_SOURCES": "1",
        "MARKET_DERIVED_MIN_OBSERVATIONS": "1",
        "MARKET_DERIVED_MIN_EDGE_PCT": "1.0",
        "MARKET_DERIVED_MAX_DISPERSION_PCT": "8.0",
        "MARKET_DERIVED_CONSENSUS_RELIEF_ENABLED": "true",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_BOOKS": "2",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_OBSERVATIONS": "1",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_EDGE_PCT": "1.8",
        "MAX_CANDIDATES_PER_MATCH_PRE_FILTER": "5",
        "MAX_INTERNAL_CANDIDATES_PER_RUN": "30",
        "SHADOW_TRACKING_MAX_PER_RUN": "20",

        # Re-enable api-football safely. The key itself must come from the workflow
        # step env/secrets; this script deliberately does not write secrets.
        "ENABLE_API_FOOTBALL": "true",
        "API_FOOTBALL_ENABLED": "true",
        "API_FOOTBALL_PER_RUN_MAX": "8",
        "API_FOOTBALL_MAX_HTTP_REQUESTS_PER_RUN": "8",
        "API_FOOTBALL_CONTEXT_MATCH_LIMIT": "36",
        "API_FOOTBALL_PREDICTIONS_LIMIT": "12",
        "API_FOOTBALL_REQUEST_BUDGET_GRANTED": "8",
        "API_FOOTBALL_REQUEST_BUDGET_REASON": "reenabled_by_run_blocker_fix_policy",
        "API_FOOTBALL_AUTH_ERROR_COOLDOWN_MINUTES": "1440",
        "API_FOOTBALL_RATE_LIMIT_COOLDOWN_MINUTES": "180",

        # Weather was enabled but zero calls were made because every match missed
        # location. Use shortlist-only fallback by team/venue/city rather than
        # spending weather quota on all fixtures.
        "WEATHER_CONTEXT_ENABLED": "true",
        "WEATHER_SHORTLIST_ONLY": "true",
        "WEATHER_ALLOW_TEAM_NAME_FALLBACK": "true",
        "WEATHER_CONTEXT_MATCH_LIMIT": "16",
        "WEATHERAPI_PER_RUN_MAX": "12",
        "WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN": "12",
        "OPENWEATHERMAP_PER_RUN_MAX": "8",
        "OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN": "8",
        "WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED": "true",
        "WEATHER_CACHE_TTL_MINUTES": "240",

        # Keep A strict, make B usable. This still blocks the latest weak near
        # misses (EV ~1-3%, edge ~0.5-1.4pp), but allows real moderate value.
        "CONTROLLED_FALLBACK_ALLOWED_FAMILIES": "totals,dnb,btts",
        "CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES": "totals",
        "CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES": "totals,dnb,btts",
        "CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES": "",
        "CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED": "false",
        "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_REJECT_PROXY_SINGLE_BOOK": "true",
        "CONTROLLED_FALLBACK_REQUIRE_MARKET_CONFIRMATION_FOR_PROXY": "true",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT": "true",

        "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS": "2",
        "CONTROLLED_FALLBACK_TIER_A_MIN_CONFIDENCE": "65.0",
        "CONTROLLED_FALLBACK_TIER_A_MIN_QUALITY": "65.0",
        "CONTROLLED_FALLBACK_TIER_A_MIN_EDGE_PP": "3.5",
        "CONTROLLED_FALLBACK_TIER_A_MIN_EV_PCT": "7.0",
        "CONTROLLED_FALLBACK_TIER_A_MAX_ODDS": "2.20",

        "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "2",
        "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE": "62.0",
        "CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY": "61.5",
        "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": "2.6",
        "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": "5.5",
        "CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE": "13",
        "CONTROLLED_FALLBACK_TIER_B_MAX_ODDS": "2.25",

        "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": "2.6",
        "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": "5.5",
        "CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EDGE_PP": "2.8",
        "CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EV_PCT": "6.0",
        "CONTROLLED_FALLBACK_EXTRA_PICK_MIN_CONFIDENCE": "62.0",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE": "64.0",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP": "4.0",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT": "7.5",

        # Sanity guards: slightly less brittle than the previous 12pp/0.18 setup,
        # but still rejects obvious xG/model contradictions.
        "CONTROLLED_FALLBACK_XG_SANITY_ENABLED": "true",
        "CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_XG_DIRECTION_MARGIN": "0.25",
        "CONTROLLED_FALLBACK_XG_HARD_REJECT_GAP_PP": "14.0",
        "CONTROLLED_FALLBACK_TIER_A_MAX_XG_GAP_PP": "7.0",
        "CONTROLLED_FALLBACK_TIER_B_MAX_XG_GAP_PP": "12.0",
        "CONTROLLED_FALLBACK_DNB_SANITY_ENABLED": "true",
        "CONTROLLED_FALLBACK_DNB_MAX_MODEL_OPTIMISM_GAP_PP": "10.0",
        "CONTROLLED_FALLBACK_DNB_HARD_REJECT_GAP_PP": "13.0",
        "CONTROLLED_FALLBACK_BTTS_SANITY_ENABLED": "true",
        "CONTROLLED_FALLBACK_BTTS_HARD_REJECT_GAP_PP": "13.0",

        # Slightly wider scheduled scan window reduces missed useful fixtures.
        "PUBLISH_WINDOW_HOURS": "12",
        "MIN_KICKOFF_LEAD_MINUTES": "25",
    }

    append_env(env)
    report = {
        "status": "ok",
        "version": POLICY_VERSION,
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "applied_env": env,
        "expected_effect": {
            "odds": "four target bookmakers requested; should reduce single-book candidates and enable market-derived signals",
            "weather": "shortlist fallback enabled; weather requests should no longer stay at 0 when venue is missing",
            "api_football": "provider is re-enabled with a small per-run cap; key must be present in GitHub secrets",
            "publication": "B-tier now admits moderate value only; weak 1-3% EV near misses remain blocked",
        },
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
