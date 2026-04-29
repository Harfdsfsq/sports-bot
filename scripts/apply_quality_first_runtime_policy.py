from __future__ import annotations

"""Quality-first Telegram publication policy.

This runtime layer keeps the full data/candidate pipeline available for diagnostics
and learning, but publishes only markets that have explicit sanity guards.

Policy shape:
- Tier A remains match totals only.
- Tier B can publish totals, DNB and BTTS.
- Tier B is stricter on value than the old totals-only policy and requires
  two-book confirmation plus xG/sanity checks.
- Strong totals near-misses get a narrow B grace window via the global B gate:
  lower confidence floor, but higher EV/edge requirements.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
UTC = timezone.utc
GITHUB_ENV = os.getenv('GITHUB_ENV')
OUT = ROOT / '.data' / 'exports' / 'latest-quality-first-runtime-policy.json'
POLICY_VERSION = 'v4-quality-first-b-tier-dnb-btts-totals-grace'


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def append_env(env: dict[str, str]) -> None:
    if GITHUB_ENV:
        with open(GITHUB_ENV, 'a', encoding='utf-8') as fh:
            for key in sorted(env):
                fh.write(f'{key}={env[key]}\n')
    else:
        for key in sorted(env):
            print(f'{key}={env[key]}')


def main() -> int:
    env = {
        'QUALITY_FIRST_RUNTIME_POLICY_ACTIVE': 'true',
        'QUALITY_FIRST_RUNTIME_POLICY_VERSION': POLICY_VERSION,

        # Publication market policy.
        # A-tier stays conservative; B-tier is allowed to use guarded DNB/BTTS.
        'TELEGRAM_MARKETS': 'totals,dnb,btts',
        'CONTROLLED_FALLBACK_ALLOWED_FAMILIES': 'totals,dnb,btts',
        'CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES': 'totals',
        'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES': 'totals,dnb,btts',
        'CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES': '',
        'CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED': 'false',
        'DAILY_BEST5_ALLOWED_FAMILIES': 'totals,dnb,btts',
        'DAILY_BEST5_TIER_A_ALLOWED_FAMILIES': 'totals',
        'DAILY_BEST5_TIER_B_ALLOWED_FAMILIES': 'totals,dnb,btts',

        # Avoid historical failure modes: high odds and single-book/proxy signals.
        'ODDS_MIN': '1.45',
        'ODDS_MAX': '2.25',
        'TARGET_ODDS_HARD_MIN': '1.45',
        'TARGET_ODDS_HARD_MAX': '2.25',
        'CONTROLLED_FALLBACK_GLOBAL_MIN_ODDS': '1.45',
        'CONTROLLED_FALLBACK_GLOBAL_MAX_ODDS': '2.25',
        'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REJECT_PROXY_SINGLE_BOOK': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_MARKET_CONFIRMATION_FOR_PROXY': 'true',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT': 'true',

        # Tier A: strong totals only.
        'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_A_MIN_CONFIDENCE': '66.0',
        'CONTROLLED_FALLBACK_TIER_A_MIN_QUALITY': '66.0',
        'CONTROLLED_FALLBACK_TIER_A_MIN_EDGE_PP': '3.8',
        'CONTROLLED_FALLBACK_TIER_A_MIN_EV_PCT': '7.5',
        'CONTROLLED_FALLBACK_TIER_A_MIN_PUBLICATION_SCORE': '18',
        'CONTROLLED_FALLBACK_TIER_A_MAX_ODDS': '2.20',

        # Tier B: stricter value gate, but confidence grace for strong totals near-misses.
        # This catches candidates like totals EV >= 8%, edge >= 4pp, books >= 2, xG sane
        # without opening weak low-edge signals.
        'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE': '63.0',
        'CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY': '62.0',
        'CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP': '4.0',
        'CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT': '8.0',
        'CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE': '14',
        'CONTROLLED_FALLBACK_TIER_B_MAX_ODDS': '2.25',

        # Explicit totals B-grace markers for reports/future code paths.
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_ENABLED': 'true',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MIN_CONFIDENCE': '63.0',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MIN_QUALITY': '62.0',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MIN_EDGE_PP': '4.0',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MIN_EV_PCT': '8.0',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MAX_ODDS': '2.25',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MAX_XG_GAP_PP': '10.5',

        # Final value gates follow the new B-tier standard.
        'CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP': '4.0',
        'CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT': '8.0',
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EDGE_PP': '4.0',
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EV_PCT': '8.0',
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_CONFIDENCE': '63.0',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE': '63.0',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP': '4.0',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT': '8.0',

        # Totals sanity remains mandatory.
        'CONTROLLED_FALLBACK_XG_SANITY_ENABLED': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_XG_DIRECTION_MARGIN': '0.18',
        'CONTROLLED_FALLBACK_XG_HARD_REJECT_GAP_PP': '12.0',
        'CONTROLLED_FALLBACK_TIER_A_REQUIRE_XG_SANITY': 'true',
        'CONTROLLED_FALLBACK_TIER_A_MAX_XG_GAP_PP': '6.0',
        'CONTROLLED_FALLBACK_TIER_B_MAX_XG_GAP_PP': '10.5',

        # DNB sanity: no-push xG probability must not be materially worse than model.
        'CONTROLLED_FALLBACK_DNB_SANITY_ENABLED': 'true',
        'CONTROLLED_FALLBACK_DNB_MAX_MODEL_OPTIMISM_GAP_PP': '10.0',
        'CONTROLLED_FALLBACK_DNB_HARD_REJECT_GAP_PP': '13.0',

        # BTTS sanity: requires xG agreement and blocks obvious low/high-team-xG conflicts.
        'CONTROLLED_FALLBACK_BTTS_SANITY_ENABLED': 'true',
        'CONTROLLED_FALLBACK_BTTS_LOW_TEAM_XG_THRESHOLD': '0.72',
        'CONTROLLED_FALLBACK_BTTS_HIGH_TEAM_XG_THRESHOLD': '1.15',
        'CONTROLLED_FALLBACK_BTTS_HARD_REJECT_GAP_PP': '13.0',

        # Weather should be spent on useful shortlist candidates and queried by venue/city,
        # not by raw club names.
        'WEATHER_SHORTLIST_ONLY': 'true',
        'WEATHER_ALLOW_TEAM_NAME_FALLBACK': 'false',
        'WEATHER_CONTEXT_MATCH_LIMIT': '12',
        'WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN': '8',
        'OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN': '4',
        'WEATHER_CACHE_TTL_MINUTES': '360',

        # RapidAPI discovery/probe is diagnostic only. It caused repeated 400/404/429 noise
        # in production runs, so default production runs keep it off.
        'RAPIDAPI_PROBE_ENABLED': 'false',
        'RAPIDAPI_ENDPOINT_DISCOVERY_ENABLED': 'false',
        'RAPIDAPI_SPORTSBOOK_PROBE_ENABLED': 'false',
        'RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED': 'false',
        'RAPIDAPI_SPORTAPI7_PROBE_ENABLED': 'false',

        # OddsPapi is now routed through RapidAPI.
        'ODDSPAPI_RAPIDAPI_ENABLED': 'true',
        'ODDSPAPI_RAPIDAPI_HOST': 'odds-api1.p.rapidapi.com',
        'ODDSPAPI_BASE_URL': 'https://odds-api1.p.rapidapi.com/v4',
        'ODDSPAPI_RAPIDAPI_USE_QUERY_API_KEY': 'false',

        # Keep target-5 cadence, but do not force volume over quality.
        'DAILY_TOP5_TARGET_PICKS': os.getenv('DAILY_TOP5_TARGET_PICKS', '5'),
        'DAILY_BEST5_TARGET_PICKS': os.getenv('DAILY_BEST5_TARGET_PICKS', '5'),
        'DAILY_TOP5_NO_HARD_CAP': 'true',
        'VOLUME_NO_DAILY_HARD_CAP': 'true',
        'CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN': os.getenv('CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN', '2'),
        'CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN': os.getenv('CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN', '2'),
        'MAX_PICKS_PER_RUN': os.getenv('MAX_PICKS_PER_RUN', '2'),

        # Bankroll: lower flat exposure while collecting a clean post-policy sample.
        'BANKROLL_KELLY_ENABLED': 'false',
        'BANKROLL_FLAT_STAKE_PCT': '0.50',
        'BANKROLL_MAX_STAKE_PCT': '0.75',
        'BANKROLL_MAX_OPEN_EXPOSURE_PCT': '10',
        'CONTROLLED_FALLBACK_TOTAL_STAKE_CAP_PCT': '2.50',
    }
    append_env(env)

    report = {
        'status': 'ok',
        'version': POLICY_VERSION,
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'applied_env': env,
        'summary': {
            'telegram_publication': 'tier_a_totals_only__tier_b_totals_dnb_btts',
            'diagnostics': 'all generated candidates can still be tracked in reports/shadow',
            'target': 'about 3-5 best picks/day when value exists, no forced volume',
            'b_tier': '2 books, EV >= 8%, edge >= 4pp, odds <= 2.25, sanity guards enabled',
            'totals_grace': 'confidence floor lowered to 63 only because EV/edge and xG requirements are stricter',
            'rapidapi': 'production probes disabled; OddsPapi routed through RapidAPI',
            'weather': 'shortlist-only and venue/city lookup preferred',
        },
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
