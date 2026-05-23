from __future__ import annotations

"""Apply a balanced-fast HARIZON runtime profile.

This version intentionally avoids the previous ultra-thin fast behavior:
- app RUN_MODE remains normal;
- publish window is 24h, not 12h;
- odds-api.io account1+account2 are both enabled when the second key exists;
- the script writes both GITHUB_ENV and a sourceable shell file so the same
  workflow step can apply the effective values before running app.cli.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
EXPORT_DIR = Path('.data/exports')
REPORT_PATH = EXPORT_DIR / 'latest-fast-run-budget.json'
ENV_SH_PATH = EXPORT_DIR / 'latest-fast-run-env.sh'


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


def shell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


def append_github_env(values: dict[str, str]) -> None:
    target = os.getenv('GITHUB_ENV')
    if target:
        with open(target, 'a', encoding='utf-8') as fh:
            for key, value in values.items():
                fh.write(f'{key}={value}\n')
    ENV_SH_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_SH_PATH.write_text(
        '\n'.join(f'export {key}={shell_quote(value)}' for key, value in values.items()) + '\n',
        encoding='utf-8',
    )


def should_disable_sportlogic() -> tuple[bool, str]:
    if truthy(os.getenv('FAST_RUN_ENABLE_SPORTLOGIC'), False):
        return False, 'explicit_enabled'
    if not truthy(os.getenv('FAST_RUN_AUTO_DISABLE_SPORTLOGIC'), True):
        return False, 'auto_disable_off'
    report = load_json('.data/exports/latest-progressive-active-core-budget-patch.json', {})
    excluded = report.get('excluded_core_providers') if isinstance(report, dict) else None
    if isinstance(excluded, list):
        for item in excluded:
            if isinstance(item, dict) and str(item.get('provider')).lower() == 'sportlogic':
                return True, str(item.get('reason') or 'previously_excluded')
    return True, 'fast_mode_zero_signal_default'


def main() -> int:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    workflow_mode = str(os.getenv('FAST_WORKFLOW_MODE') or os.getenv('RUN_MODE') or 'fast').strip().lower()
    fast = truthy(os.getenv('HARIZON_FAST_RUN'), workflow_mode != 'full') and workflow_mode != 'full'
    notes: list[str] = []
    overrides: dict[str, str] = {
        'RUN_MODE': 'normal',
        'HARIZON_FAST_RUN': 'true' if fast else 'false',
        'FAST_WORKFLOW_MODE': workflow_mode or 'fast',
    }

    if fast:
        inventory_limit = max(300, as_int(os.getenv('FAST_RUN_INVENTORY_LIMIT'), 300))
        window_hours = max(18, as_int(os.getenv('FAST_RUN_WINDOW_HOURS'), 24))
        keep_window_hours = max(window_hours + 4, as_int(os.getenv('FAST_RUN_KEEP_WINDOW_HOURS'), 30))
        odds_target = max(220, as_int(os.getenv('FAST_RUN_ODDS_MATCH_TARGET'), 260))
        odds_req = max(140, as_int(os.getenv('FAST_RUN_ODDS_API_IO_REQUESTS'), 160))
        bzz_req = max(120, as_int(os.getenv('FAST_RUN_BZZOIRO_REQUESTS'), 140))
        sstats_req = max(80, as_int(os.getenv('FAST_RUN_SSTATS_REQUESTS'), 100))
        acc2_present = bool((os.getenv('ODDS_API_IO_KEY_2') or os.getenv('ODDS_API_IO_KEY2') or os.getenv('ODDS_API_IO_ACC2_KEY') or os.getenv('ODDS_API_IO_SECONDARY_KEY') or '').strip())
        overrides.update({
            'FAST_RUN_PROFILE': 'balanced-depth-v3',
            'PUBLISH_WINDOW_HOURS': str(window_hours),
            'RUN_DAYS_AHEAD': os.getenv('FAST_RUN_DAYS_AHEAD', '2'),
            'MIN_KICKOFF_LEAD_MINUTES': os.getenv('FAST_RUN_MIN_KICKOFF_LEAD_MINUTES', '20'),
            'FAST_RUN_KEEP_WINDOW_HOURS': str(keep_window_hours),
            'DAY_INVENTORY_TARGET_SIZE': str(inventory_limit),
            'DAY_INVENTORY_MAX_MATCHES': str(inventory_limit),
            'DAY_INVENTORY_FORCE_TOP_300': 'true',
            'DAY_INVENTORY_FORCE_FULL_300': 'true',
            'DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES': str(odds_target),
            'DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES': str(odds_target),
            'MAX_MATCHES_FOR_ODDS_FETCH': str(odds_target),
            'ODDS_API_IO_MATCH_LIMIT': str(odds_target),
            'ODDS_API_IO_ODDS_MATCH_LIMIT': str(odds_target),
            'ODDS_API_IO_PER_RUN_MAX': str(odds_req),
            'ODDS_API_IO_MAX_REQUESTS_PER_RUN': str(odds_req),
            'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': str(odds_req),
            'ODDS_API_IO_REQUESTS_MAX_PER_RUN': str(odds_req),
            'ODDS_API_IO_REQUEST_BUDGET_GRANTED': str(odds_req),
            'ODDS_API_IO_MAX_ODDS_REQUESTS_PER_RUN': str(max(80, odds_req - 20)),
            'ODDS_API_IO_FETCH_ODDS_MAX_REQUESTS': str(max(80, odds_req - 20)),
            'ODDS_API_IO_ODDS_REQUEST_BUDGET_GRANTED': str(max(80, odds_req - 20)),
            'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': str(max(60, odds_req // 2)),
            'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': str(max(60, odds_req // 2)),
            'ODDS_API_IO_ACCOUNT1_MAX_REQUESTS_PER_RUN': str(max(60, odds_req // 2)),
            'ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN': str(max(60, odds_req // 2)),
            'ODDS_API_IO_ACCOUNT2_ACTIVE': 'true' if acc2_present else 'false',
            'ODDS_API_IO_BOOKMAKERS': 'Bet365,Unibet,Betfair Exchange,Sbobet',
            'ODDS_API_IO_BOOKMAKERS_ACCOUNT1': 'Bet365,Unibet',
            'ODDS_API_IO_BOOKMAKERS_ACCOUNT2': 'Betfair Exchange,Sbobet',
            'TARGET_BOOKMAKERS': 'Bet365,Unibet,Betfair Exchange,Sbobet',
            'CONSENSUS_BOOKMAKERS': 'Bet365,Unibet,Betfair Exchange,Sbobet',
            'PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT': '24',
            'PRICE_BACKFILL_ODDS_API_IO_MAX_EVENT_IDS_PER_REQUEST': '10',
            'PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT': str(odds_target),
            'BZZOIRO_MAX_REQUESTS_PER_RUN': str(bzz_req),
            'BZZOIRO_REQUESTS_MAX_PER_RUN': str(bzz_req),
            'BZZOIRO_REQUEST_BUDGET_GRANTED': str(bzz_req),
            'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': str(bzz_req),
            'BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN': str(max(70, bzz_req // 2)),
            'BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN': str(max(35, bzz_req // 3)),
            'BZZOIRO_PREDICTIONS_MAX_PAGES': '10',
            'BZZOIRO_MAX_PAGES': '16',
            'BZZOIRO_CONTEXT_MATCH_LIMIT': str(min(inventory_limit, 240)),
            'BZZOIRO_PRICE_BACKFILL_ENABLED': 'true',
            'BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT': '140',
            'BZZOIRO_CURRENT_ODDS_AS_SECONDARY_SOURCE': 'true',
            'BZZOIRO_V2_ODDS_ENABLED': 'true',
            'BZZOIRO_V2_FETCH_EVENT_ODDS': 'true',
            'BZZOIRO_V2_MATCH_LIMIT': '220',
            'BZZOIRO_V2_MAX_HTTP_REQUESTS_PER_RUN': str(bzz_req),
            'SSTATS_MAX_REQUESTS_PER_RUN': str(sstats_req),
            'SSTATS_REQUESTS_MAX_PER_RUN': str(sstats_req),
            'SSTATS_REQUEST_BUDGET_GRANTED': str(sstats_req),
            'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': str(sstats_req),
            'SSTATS_CONTEXT_MATCH_LIMIT': str(min(inventory_limit, 220)),
            'SSTATS_DEEP_DETAIL_LIMIT_PER_RUN': '12',
            'SSTATS_DEEP_CONTEXT_MATCH_LIMIT': '30',
            'NEWS_INJURY_SHORTLIST_ENABLED': 'false',
            'API_HEALTH_DURING_RUN_ENABLED': 'false',
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
        notes.append('balanced_depth_v3_uses_24h_window_and_dual_account_bookmakers')
        if not acc2_present:
            notes.append('warning:odds_api_io_second_key_missing_or_not_mapped')
    else:
        notes.append('fast_run_disabled')

    append_github_env(overrides)
    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'fast_run': fast,
        'run_mode': overrides.get('RUN_MODE'),
        'workflow_mode': workflow_mode,
        'overrides': overrides,
        'notes': notes,
        'env_shell': str(ENV_SH_PATH),
    }
    REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
