from __future__ import annotations

"""Apply balanced speed-oriented runtime limits for HARIZON fast runs.

Fast mode should reduce GitHub Actions overhead and avoid low-value discovery,
but it must not starve the line market.  The previous fast profile was too thin:
it allowed only a handful of odds-api.io requests in practice and often produced
0 matches with 2+ bookmakers.  This script keeps publication guards unchanged
while preserving a minimum market-depth budget for odds-api.io and Bzzoiro odds.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
EXPORT_DIR = Path('.data/exports')
REPORT_PATH = EXPORT_DIR / 'latest-fast-run-budget.json'


def truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'fast', 'balanced'}


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def load_json(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        pass
    return default


def append_github_env(values: dict[str, str]) -> None:
    target = os.getenv('GITHUB_ENV')
    if not target:
        return
    with open(target, 'a', encoding='utf-8') as fh:
        for key, value in values.items():
            fh.write(f'{key}={value}\n')


def _sportlogic_has_recent_signal() -> bool:
    report = load_json(EXPORT_DIR / 'latest-harizon-telegram-run-report.json', {})
    api = report.get('api') if isinstance(report, dict) else {}
    sport = api.get('sportlogic') if isinstance(api, dict) and isinstance(api.get('sportlogic'), dict) else {}
    if as_int(sport.get('matched')) > 0 or as_int(sport.get('offers')) > 0:
        return True
    debug = load_json(EXPORT_DIR / 'latest-sportlogic-debug.json', {})
    stats = debug.get('stats') if isinstance(debug, dict) and isinstance(debug.get('stats'), dict) else {}
    return as_int(stats.get('events_matched')) > 0 or as_int(stats.get('offers_parsed')) > 0


def should_disable_sportlogic() -> tuple[bool, str]:
    if truthy(os.getenv('FAST_RUN_ENABLE_SPORTLOGIC'), False):
        return False, 'explicitly_enabled'
    if not truthy(os.getenv('FAST_RUN_AUTO_DISABLE_SPORTLOGIC'), True):
        return False, 'auto_disable_disabled'
    if _sportlogic_has_recent_signal():
        return False, 'recent_signal_present'
    return True, 'fast_mode_zero_signal_default'


def _env_int(name: str, default: int) -> int:
    return max(1, as_int(os.getenv(name), default))


def main() -> int:
    run_mode = str(os.getenv('RUN_MODE') or '').strip().lower()
    fast = truthy(os.getenv('HARIZON_FAST_RUN'), False) or run_mode == 'fast'
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    overrides: dict[str, str] = {}
    notes: list[str] = []

    if fast:
        profile = str(os.getenv('FAST_RUN_PROFILE') or 'balanced').strip().lower()
        inventory_limit = max(220, as_int(os.getenv('FAST_RUN_INVENTORY_LIMIT'), 300))
        window_hours = max(8, as_int(os.getenv('FAST_RUN_WINDOW_HOURS') or os.getenv('PUBLISH_WINDOW_HOURS'), 12))
        odds_match_target = max(160, _env_int('FAST_RUN_ODDS_MATCH_TARGET', min(inventory_limit, 220)))
        odds_req = max(60, _env_int('FAST_RUN_ODDS_API_IO_REQUESTS', 120))
        per_account = max(30, odds_req // 2)
        bzz_req = max(60, _env_int('FAST_RUN_BZZOIRO_MAX_REQUESTS', 100))
        sstats_req = max(60, _env_int('FAST_RUN_SSTATS_MAX_REQUESTS', 90))

        overrides.update({
            'HARIZON_FAST_RUN': 'true',
            'FAST_RUN_PROFILE': profile,
            'PUBLISH_WINDOW_HOURS': str(window_hours),
            'DAY_INVENTORY_TARGET_SIZE': str(inventory_limit),
            'DAY_INVENTORY_MAX_MATCHES': str(inventory_limit),
            'MAX_MATCHES_FOR_ODDS_FETCH': str(odds_match_target),
            'DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES': str(odds_match_target),
            'DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES': str(odds_match_target),
            'PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT': str(odds_match_target),
            'PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT': str(max(6, min(14, odds_match_target // 15))),
            'PRICE_BACKFILL_ODDS_API_IO_MAX_EVENT_IDS_PER_REQUEST': '10',
            'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': str(odds_req),
            'ODDS_API_IO_MAX_REQUESTS_PER_RUN': str(odds_req),
            'ODDS_API_IO_REQUEST_BUDGET_GRANTED': str(odds_req),
            'ODDS_API_IO_REQUESTS_MAX_PER_RUN': str(odds_req),
            'ODDS_API_IO_PER_RUN_MAX': str(odds_req),
            'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': str(per_account),
            'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': str(per_account),
            'ODDS_API_IO_ACCOUNT1_MAX_REQUESTS_PER_RUN': str(per_account),
            'ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN': str(per_account),
            'BZZOIRO_MAX_REQUESTS_PER_RUN': str(bzz_req),
            'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': str(bzz_req),
            'BZZOIRO_REQUESTS_MAX_PER_RUN': str(bzz_req),
            'BZZOIRO_REQUEST_BUDGET_GRANTED': str(bzz_req),
            'BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN': str(max(40, min(80, bzz_req // 2))),
            'BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN': str(max(16, min(40, bzz_req // 3))),
            'BZZOIRO_PREDICTIONS_MAX_PAGES': '8',
            'BZZOIRO_MAX_PAGES': '14',
            'BZZOIRO_CONTEXT_MATCH_LIMIT': str(min(inventory_limit, 220)),
            'BZZOIRO_PRICE_BACKFILL_ENABLED': 'true',
            'BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT': str(max(60, min(100, odds_match_target // 2))),
            'BZZOIRO_V2_ODDS_ENABLED': 'true',
            'BZZOIRO_V2_FETCH_EVENT_ODDS': 'true',
            'BZZOIRO_CURRENT_ODDS_AS_SECONDARY_SOURCE': 'true',
            'SSTATS_MAX_REQUESTS_PER_RUN': str(sstats_req),
            'SSTATS_REQUESTS_MAX_PER_RUN': str(sstats_req),
            'SSTATS_REQUEST_BUDGET_GRANTED': str(sstats_req),
            'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': str(sstats_req),
            'SSTATS_CONTEXT_MATCH_LIMIT': str(min(inventory_limit, 220)),
            'SSTATS_DEEP_DETAIL_LIMIT_PER_RUN': str(max(8, min(20, sstats_req // 5))),
            'SSTATS_DEEP_CONTEXT_MATCH_LIMIT': str(max(20, min(50, sstats_req // 2))),
            'NEWS_INJURY_SHORTLIST_ENABLED': os.getenv('FAST_RUN_NEWS_INJURY_SHORTLIST_ENABLED', 'false'),
            'API_HEALTH_DURING_RUN_ENABLED': os.getenv('FAST_RUN_API_HEALTH_DURING_RUN_ENABLED', 'false'),
            'PROVIDER_SMOKE_FAIL_ON_ERROR': 'false',
        })
        disable_sportlogic, reason = should_disable_sportlogic()
        if disable_sportlogic:
            overrides.update({
                'SPORTLOGIC_ENABLED': 'false',
                'ENABLE_SPORTLOGIC': 'false',
                'SPORTLOGIC_CONTROLLED_ODDS_ENABLED': 'false',
                'DAY_INVENTORY_ENABLE_SPORTLOGIC': 'false',
                'SPORTLOGIC_REQUEST_BUDGET_GRANTED': '0',
                'SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN': '0',
                'SPORTLOGIC_REQUESTS_MAX_PER_RUN': '0',
                'SPORTLOGIC_PER_RUN_MAX': '0',
            })
            notes.append(f'sportlogic_disabled:{reason}')
        else:
            notes.append(f'sportlogic_kept:{reason}')
        notes.append('balanced_fast_preserves_minimum_odds_depth')
    else:
        notes.append('fast_run_disabled')

    append_github_env(overrides)
    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'fast_run': fast,
        'run_mode': run_mode,
        'overrides': overrides,
        'notes': notes,
    }
    REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
