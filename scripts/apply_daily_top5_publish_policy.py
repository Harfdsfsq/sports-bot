from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
UTC = timezone.utc
OUT = ROOT / '.data' / 'exports' / 'latest-daily-top5-publish-policy.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')


def tzinfo() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def local_date_of(value: Any) -> str | None:
    dt = parse_dt(value)
    if dt is None:
        return None
    return dt.astimezone(tzinfo()).date().isoformat()


def today_local() -> str:
    return datetime.now(UTC).astimezone(tzinfo()).date().isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(value)
    except Exception:
        return default


def row_timestamp(row: dict[str, Any]) -> Any:
    for key in ('sent_at', 'published_at', 'created_at', 'placed_at', 'timestamp', 'updated_at'):
        if row.get(key):
            return row.get(key)
    return None


def normalize_piece(value: Any) -> str:
    return str(value or '').strip().lower()


def row_key(row: dict[str, Any], fallback: str) -> str:
    parts = [
        normalize_piece(row.get('match_key')),
        normalize_piece(row.get('family')),
        normalize_piece(row.get('selection')),
        normalize_piece(row.get('selection_key')),
        normalize_piece(row.get('point')),
        normalize_piece(row.get('team_side')),
        normalize_piece(row.get('commence_time') or row.get('start_time') or row.get('kickoff')),
    ]
    key = '|'.join(parts).strip('|')
    if key:
        return key
    parts = [
        normalize_piece(row.get('home_team') or row.get('home')),
        normalize_piece(row.get('away_team') or row.get('away')),
        normalize_piece(row.get('selection')),
        normalize_piece(row.get('odds')),
        normalize_piece(row.get('commence_time') or row.get('start_time') or row.get('kickoff')),
    ]
    return '|'.join(parts).strip('|') or fallback


def collect_rows_for_today(rows: Any, today: str, prefix: str) -> set[str]:
    if not isinstance(rows, list):
        return set()
    out: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if local_date_of(row_timestamp(row)) != today:
            continue
        out.add(f'{prefix}:{row_key(row, f"row:{idx}")}')
    return out


def count_today_picks(today: str) -> dict[str, Any]:
    keys: set[str] = set()
    details: dict[str, int] = {}

    sent = load_json(ROOT / '.data' / 'fallback-sent-index.json', {})
    if isinstance(sent, dict):
        rows = [row for row in sent.values() if isinstance(row, dict)]
        k = collect_rows_for_today(rows, today, 'fallback')
        keys.update(k)
        details['fallback_sent_index'] = len(k)
    else:
        details['fallback_sent_index'] = 0

    state = load_json(ROOT / '.data' / 'state.json', {})
    if isinstance(state, dict):
        for collection, prefix in (
            ('bets', 'state_bets'),
            ('published_candidates', 'state_published'),
        ):
            k = collect_rows_for_today(state.get(collection), today, prefix)
            keys.update(k)
            details[prefix] = len(k)
    details['effective_today_picks'] = len(keys)
    return details


def append_env(env: dict[str, str]) -> None:
    if GITHUB_ENV:
        with open(GITHUB_ENV, 'a', encoding='utf-8') as fh:
            for key in sorted(env):
                fh.write(f'{key}={env[key]}\n')
    else:
        for key in sorted(env):
            print(f'{key}={env[key]}')


def main() -> int:
    today = today_local()
    counts = count_today_picks(today)
    existing = as_int(counts.get('effective_today_picks'))
    target = max(1, as_int(os.getenv('DAILY_TOP5_TARGET_PICKS'), 5))
    hard_cap = max(target, as_int(os.getenv('DAILY_TOP5_HARD_CAP_PICKS'), target))
    scheduled_max_per_run = max(1, as_int(os.getenv('DAILY_TOP5_MAX_PICKS_PER_RUN'), 2))
    manual_max_per_run = max(1, as_int(os.getenv('DAILY_TOP5_MANUAL_MAX_PICKS_PER_RUN'), target))
    is_manual_run = str(os.getenv('GITHUB_EVENT_NAME') or '').strip().lower() == 'workflow_dispatch'
    max_per_run = manual_max_per_run if is_manual_run else scheduled_max_per_run
    remaining_to_target = max(0, target - existing)
    remaining_to_hard = max(0, hard_cap - existing)

    allowed_this_run = min(max_per_run, remaining_to_hard)
    reason = f'top5_active:{existing}/{target}'
    if remaining_to_hard <= 0:
        allowed_this_run = 0
        reason = f'daily_top5_hard_cap_reached:{existing}/{hard_cap}'
    elif remaining_to_target <= 0:
        allowed_this_run = min(1, remaining_to_hard)
        reason = f'daily_top5_target_reached_extra_strict:{existing}/{target}'

    env = {
        'DAILY_TOP5_PUBLISH_POLICY_ACTIVE': 'true',
        'VOLUME_POLICY_MODE': 'target_5',
        'VOLUME_DAILY_TARGET_PICKS': str(target),
        'VOLUME_DAILY_SOFT_CAP_PICKS': str(target),
        'VOLUME_DAILY_HARD_CAP_PICKS': str(hard_cap),
        'VOLUME_EXISTING_PICKS_TODAY': str(existing),
        'CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN': str(allowed_this_run),
        'CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN': str(allowed_this_run),
        'MAX_PICKS_PER_RUN': str(allowed_this_run),
        'CONTROLLED_FALLBACK_EXTRA_PICK_STRICT': 'true',
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EV_PCT': str(as_float(os.getenv('DAILY_TOP5_EXTRA_PICK_MIN_EV_PCT'), 6.0)),
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EDGE_PP': str(as_float(os.getenv('DAILY_TOP5_EXTRA_PICK_MIN_EDGE_PP'), 3.0)),
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_CONFIDENCE': str(as_float(os.getenv('DAILY_TOP5_EXTRA_PICK_MIN_CONFIDENCE'), 64.0)),
        'CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED': 'false',
        'CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES': '',
        'CONTROLLED_FALLBACK_DAILY_TOP5_REASON': reason,
        # Keep quality: do not bypass hard guards; only choose fewer, better picks.
        'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REJECT_PROXY_SINGLE_BOOK': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_MARKET_CONFIRMATION_FOR_PROXY': 'true',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT': 'true',
    }
    if allowed_this_run <= 0:
        env['CONTROLLED_FALLBACK_ENABLED'] = 'false'

    append_env(env)
    report = {
        'status': 'ok',
        'today_local': today,
        'existing_today_picks': existing,
        'target_picks': target,
        'hard_cap_picks': hard_cap,
        'max_picks_per_run': max_per_run,
        'scheduled_max_picks_per_run': scheduled_max_per_run,
        'manual_max_picks_per_run': manual_max_per_run,
        'is_manual_run': is_manual_run,
        'allowed_this_run': allowed_this_run,
        'reason': reason,
        'counts': counts,
        'env': env,
        'quality_note': 'Daily top5 policy targets 5 picks/day, lets manual runs fill the remaining target, and keeps Tier C disabled / hard guards enabled.',
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
