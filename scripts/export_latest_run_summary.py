from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
OUT_PATH = ROOT / '.data' / 'exports' / 'latest-run-summary.json'


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def first_dict(paths: list[str | Path]) -> dict[str, Any]:
    for path in paths:
        payload = load_json(path, None)
        if isinstance(payload, dict) and payload:
            return payload
    return {}


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def as_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ''):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def compact_dict(raw: Any, limit: int = 10) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    items = list(raw.items())[:limit]
    return {str(k): v for k, v in items}


def provider_snapshot() -> dict[str, Any]:
    quota = load_json(ROOT / '.data' / 'provider_quota_governor_state.json', {})
    providers = quota.get('providers') if isinstance(quota, dict) else {}
    if not isinstance(providers, dict):
        return {}
    important = [
        'odds_api_io',
        'bzzoiro',
        'sstats',
        'football_data',
        'thesportsdb',
        'futrixmetrics',
        'weather',
        'newsapi',
        'gnews',
    ]
    out: dict[str, Any] = {}
    for name in important:
        row = providers.get(name)
        if not isinstance(row, dict):
            continue
        out[name] = {
            'used_today': as_int(row.get('used_today')),
            'tokens': row.get('tokens'),
            'daily_budget': row.get('daily_budget'),
            'per_run_max': row.get('per_run_max'),
            'updated_at': row.get('updated_at'),
        }
    return out


def main() -> int:
    debug = load_json(ROOT / '.logs' / 'debug-last-run.json', {})
    summary = debug.get('summary') if isinstance(debug.get('summary'), dict) else {}
    fallback = first_dict([
        ROOT / 'artifacts' / 'controlled-fallback-report.json',
        ROOT / '.data' / 'exports' / 'latest-controlled-fallback-report.json',
    ])
    detailed = first_dict([
        ROOT / '.data' / 'exports' / 'latest-detailed-run-report.json',
    ])
    learning = load_json(ROOT / '.data' / 'learning-state.json', {})
    volume = load_json(ROOT / '.data' / 'volume-governor-state.json', {})
    watchdog = load_json(ROOT / '.data' / 'autorun-state.json', {})

    learning_near = learning.get('last_near_misses') if isinstance(learning.get('last_near_misses'), dict) else {}
    detailed_counts = detailed.get('candidate_counts') if isinstance(detailed.get('candidate_counts'), dict) else {}
    detailed_reasons = detailed.get('reason_counts') if isinstance(detailed.get('reason_counts'), dict) else {}
    near_misses = detailed.get('near_misses') if isinstance(detailed.get('near_misses'), list) else []

    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'status': (
            fallback.get('status')
            or detailed.get('status')
            or learning_near.get('status')
            or ('published' if as_int(fallback.get('selected_count')) > 0 else 'no_pick')
        ),
        'published': as_bool(fallback.get('published')) or as_bool(detailed.get('published')) or as_int(fallback.get('selected_count')) > 0,
        'selected_count': as_int(fallback.get('selected_count')),
        'summary': {
            'matches_seen': as_int(summary.get('matches_seen')),
            'matches_with_offers': as_int(summary.get('matches_with_offers')),
            'contexts_built': as_int(summary.get('contexts_built')),
            'candidates_raw': as_int(summary.get('candidates_raw')),
            'candidates_before_quality': as_int(summary.get('candidates_before_quality')),
            'candidates_publishable': as_int(summary.get('candidates_publishable')),
            'rescue_candidates_checked': as_int(fallback.get('rescue_candidates_checked') or detailed_counts.get('rescue_checked')),
            'evaluated_candidates': as_int(detailed_counts.get('evaluated')),
        },
        'top_reject_reasons': compact_dict(detailed_reasons or learning_near.get('reason_counts'), 12),
        'near_miss_count': len(near_misses),
        'near_miss_status': learning_near.get('status') or detailed.get('status'),
        'watchdog': {
            'last_policy_version': watchdog.get('last_policy_version'),
            'last_decision_reason': watchdog.get('last_decision_reason'),
            'last_successful_scheduled_run_utc': watchdog.get('last_successful_scheduled_run_utc'),
            'last_preflight_utc': watchdog.get('last_preflight_utc'),
        },
        'volume_policy': {
            'version': volume.get('version'),
            'mode': volume.get('mode'),
            'decision_reasons': volume.get('decision_reasons') if isinstance(volume.get('decision_reasons'), list) else [],
            'existing_picks_today': as_int(volume.get('existing_picks_today')),
            'tier_c_publish_enabled': str((volume.get('applied_env') or {}).get('CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED', 'false')).lower(),
        },
        'providers': provider_snapshot(),
        'source_files': {
            'debug_last_run_present': bool(debug),
            'fallback_report_present': bool(fallback),
            'detailed_report_present': bool(detailed),
            'learning_state_present': bool(learning),
        },
    }

    write_json(OUT_PATH, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
