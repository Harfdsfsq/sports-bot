from __future__ import annotations

"""Apply conservative speed-oriented runtime limits for HARIZON fast runs.

This script writes GitHub Actions environment overrides when HARIZON_FAST_RUN is
true. It does not change publication guards; it only reduces expensive discovery
work that is not required for final Telegram publication.
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
    return raw in {'1', 'true', 'yes', 'on', 'fast'}


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


def should_disable_sportlogic() -> tuple[bool, str]:
    if not truthy(os.getenv('FAST_RUN_AUTO_DISABLE_SPORTLOGIC'), True):
        return False, 'auto_disable_disabled'
    active_core = load_json(EXPORT_DIR / 'latest-progressive-active-core-budget-patch.json', {})
    excluded = active_core.get('excluded_core_providers') if isinstance(active_core, dict) else []
    if isinstance(excluded, list):
        for item in excluded:
            if isinstance(item, dict) and str(item.get('provider') or '').lower() == 'sportlogic':
                return True, str(item.get('reason') or 'excluded_by_previous_active_core_patch')
            if str(item).lower().startswith('sportlogic'):
                return True, 'excluded_by_previous_active_core_patch'
    report = load_json(EXPORT_DIR / 'latest-harizon-telegram-run-report.json', {})
    api = report.get('api') if isinstance(report, dict) else {}
    sport = api.get('sportlogic') if isinstance(api, dict) and isinstance(api.get('sportlogic'), dict) else {}
    if sport and as_int(sport.get('matched')) <= 0 and as_int(sport.get('offers')) <= 0 and as_int(sport.get('errors')) > 0:
        return True, 'previous_run_zero_matched_with_errors'
    return False, 'keep_enabled'


def main() -> int:
    fast = truthy(os.getenv('HARIZON_FAST_RUN'), False) or str(os.getenv('RUN_MODE') or '').lower() == 'fast'
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    overrides: dict[str, str] = {}
    notes: list[str] = []
    if fast:
        inventory_limit = str(max(80, as_int(os.getenv('FAST_RUN_INVENTORY_LIMIT'), 300)))
        window_hours = str(max(4, as_int(os.getenv('FAST_RUN_WINDOW_HOURS') or os.getenv('PUBLISH_WINDOW_HOURS'), 12)))
        overrides.update({
            'HARIZON_FAST_RUN': 'true',
            'PUBLISH_WINDOW_HOURS': window_hours,
            'DAY_INVENTORY_TARGET_SIZE': inventory_limit,
            'DAY_INVENTORY_MAX_MATCHES': inventory_limit,
            'MAX_MATCHES_FOR_ODDS_FETCH': inventory_limit,
            'DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES': str(min(as_int(inventory_limit), 180)),
            'DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES': str(min(as_int(inventory_limit), 180)),
            'BZZOIRO_MAX_REQUESTS_PER_RUN': os.getenv('FAST_RUN_BZZOIRO_MAX_REQUESTS', '80'),
            'BZZOIRO_REQUEST_BUDGET_GRANTED': os.getenv('FAST_RUN_BZZOIRO_MAX_REQUESTS', '80'),
            'BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN': os.getenv('FAST_RUN_BZZOIRO_EVENTS_MAX_REQUESTS', '40'),
            'BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN': os.getenv('FAST_RUN_BZZOIRO_PREDICTIONS_MAX_REQUESTS', '12'),
            'BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT': os.getenv('FAST_RUN_BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT', '40'),
            'SSTATS_MAX_REQUESTS_PER_RUN': os.getenv('FAST_RUN_SSTATS_MAX_REQUESTS', '80'),
            'SSTATS_REQUEST_BUDGET_GRANTED': os.getenv('FAST_RUN_SSTATS_MAX_REQUESTS', '80'),
            'SSTATS_CONTEXT_MATCH_LIMIT': os.getenv('FAST_RUN_SSTATS_CONTEXT_MATCH_LIMIT', '180'),
            'SSTATS_DEEP_DETAIL_LIMIT_PER_RUN': os.getenv('FAST_RUN_SSTATS_DEEP_DETAIL_LIMIT_PER_RUN', '12'),
            'SSTATS_DEEP_CONTEXT_MATCH_LIMIT': os.getenv('FAST_RUN_SSTATS_DEEP_CONTEXT_MATCH_LIMIT', '30'),
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
            })
            notes.append(f'sportlogic_disabled:{reason}')
        else:
            notes.append(f'sportlogic_kept:{reason}')
    else:
        notes.append('fast_run_disabled')
    append_github_env(overrides)
    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'fast_run': fast,
        'overrides': overrides,
        'notes': notes,
    }
    REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
