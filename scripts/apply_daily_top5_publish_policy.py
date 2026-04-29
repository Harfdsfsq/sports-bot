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


def env_float_str(name: str, default: float) -> str:
    return str(as_float(os.getenv(name), default))


def env_nonnegative_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return None
    parsed = as_int(raw, -1)
    return parsed if parsed >= 0 else None


def upstream_pick_cap() -> dict[str, Any]:
    """Return the cap already imposed by earlier policies in this workflow.

    apply_volume_policy.py runs before this script. The previous top5 policy could
    accidentally raise MAX_PICKS_PER_RUN from 0 back to 1 after the soft cap was
    reached. This helper makes top5 monotonic: it may reduce a cap, never raise it.
    """
    names = (
        'CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN',
        'CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN',
        'MAX_PICKS_PER_RUN',
    )
    values: dict[str, int] = {}
    for name in names:
        value = env_nonnegative_int(name)
        if value is not None:
            values[name] = value
    cap = min(values.values()) if values else None
    return {'cap': cap, 'values': values}


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


def collect_rows_for_today(rows: Any, today: str) -> tuple[set[str], int]:
    if not isinstance(rows, list):
        return set(), 0
    out: set[str] = set()
    scanned = 0
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if local_date_of(row_timestamp(row)) != today:
            continue
        scanned += 1
        out.add(row_key(row, f'row:{idx}'))
    return out, scanned


def count_today_picks(today: str) -> dict[str, Any]:
    # Effective count must dedupe the same Telegram publication across
    # fallback-sent-index/state.bets/state.published_candidates. Per-source details
    # remain visible, but the cap uses one normalized key set without source prefixes.
    keys: set[str] = set()
    details: dict[str, Any] = {}

    sent = load_json(ROOT / '.data' / 'fallback-sent-index.json', {})
    if isinstance(sent, dict):
        rows = [row for row in sent.values() if isinstance(row, dict)]
        k, scanned = collect_rows_for_today(rows, today)
        keys.update(k)
        details['fallback_sent_index'] = len(k)
        details['fallback_sent_index_rows'] = scanned
    else:
        details['fallback_sent_index'] = 0
        details['fallback_sent_index_rows'] = 0

    state = load_json(ROOT / '.data' / 'state.json', {})
    if isinstance(state, dict):
        for collection, source_name in (
            ('bets', 'state_bets'),
            ('published_candidates', 'state_published_candidates'),
        ):
            k, scanned = collect_rows_for_today(state.get(collection), today)
            keys.update(k)
            details[source_name] = len(k)
            details[source_name + '_rows'] = scanned
    details['effective_today_picks'] = len(keys)
    details['effective_count_note'] = 'Effective top5 count dedupes the same pick across all real publication sources.'
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
    configured_hard_cap = max(target, as_int(os.getenv('DAILY_TOP5_HARD_CAP_PICKS'), target))
    volume_soft_cap = as_int(os.getenv('VOLUME_DAILY_SOFT_CAP_PICKS'), configured_hard_cap)
    soft_cap = max(target, min(configured_hard_cap, volume_soft_cap if volume_soft_cap > 0 else configured_hard_cap))
    hard_cap = configured_hard_cap
    scheduled_max_per_run = max(1, as_int(os.getenv('DAILY_TOP5_MAX_PICKS_PER_RUN'), 2))
    manual_max_per_run = max(1, as_int(os.getenv('DAILY_TOP5_MANUAL_MAX_PICKS_PER_RUN'), scheduled_max_per_run))
    is_manual_run = str(os.getenv('GITHUB_EVENT_NAME') or '').strip().lower() == 'workflow_dispatch'
    max_per_run = manual_max_per_run if is_manual_run else scheduled_max_per_run
    remaining_to_target = max(0, target - existing)
    remaining_to_soft = max(0, soft_cap - existing)
    remaining_to_hard = max(0, hard_cap - existing)
    upstream = upstream_pick_cap()
    upstream_cap_value = upstream.get('cap')

    allowed_this_run = min(max_per_run, remaining_to_hard, remaining_to_soft)
    reason = f'top5_active:{existing}/{target}'
    if remaining_to_hard <= 0:
        allowed_this_run = 0
        reason = f'daily_top5_hard_cap_reached:{existing}/{hard_cap}'
    elif remaining_to_soft <= 0:
        allowed_this_run = 0
        reason = f'daily_top5_soft_cap_reached:{existing}/{soft_cap}'
    elif remaining_to_target <= 0:
        allowed_this_run = min(1, remaining_to_soft, remaining_to_hard)
        reason = f'daily_top5_target_reached_extra_strict:{existing}/{target}'

    if isinstance(upstream_cap_value, int):
        if upstream_cap_value < allowed_this_run:
            reason = f'{reason}; upstream_volume_cap:{upstream_cap_value}'
        allowed_this_run = min(allowed_this_run, upstream_cap_value)

    env = {
        'DAILY_TOP5_PUBLISH_POLICY_ACTIVE': 'true',
        'VOLUME_POLICY_MODE': 'target_5',
        'VOLUME_DAILY_TARGET_PICKS': str(target),
        'VOLUME_DAILY_SOFT_CAP_PICKS': str(soft_cap),
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
        # Keep quality: do not bypass hard guards; make target_5 override late enough to
        # beat final_runtime_overrides.env and let strong Tier B reserve picks publish.
        'CONTROLLED_FALLBACK_ALLOWED_FAMILIES': os.getenv(
            'DAILY_TOP5_ALLOWED_FAMILIES',
            'totals,dnb,teamtotals,teamTotals,btts,spreads',
        ),
        'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES': os.getenv(
            'DAILY_TOP5_TIER_B_ALLOWED_FAMILIES',
            'totals,dnb,teamtotals,teamTotals,btts,spreads',
        ),
        'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE': env_float_str('DAILY_TOP5_TIER_B_MIN_CONFIDENCE', 63.0),
        'CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY': env_float_str('DAILY_TOP5_TIER_B_MIN_QUALITY', 60.0),
        'CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP': env_float_str('DAILY_TOP5_TIER_B_MIN_EDGE_PP', 3.0),
        'CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT': env_float_str('DAILY_TOP5_TIER_B_MIN_EV_PCT', 6.0),
        'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REJECT_PROXY_SINGLE_BOOK': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_MARKET_CONFIRMATION_FOR_PROXY': 'true',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT': 'true',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE': env_float_str(
            'DAILY_TOP5_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE',
            64.0,
        ),
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP': env_float_str(
            'DAILY_TOP5_PROXY_SINGLE_SOURCE_MIN_EDGE_PP',
            3.0,
        ),
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT': env_float_str(
            'DAILY_TOP5_PROXY_SINGLE_SOURCE_MIN_EV_PCT',
            6.0,
        ),
        'CONTROLLED_FALLBACK_DNB_MIN_XG_EDGE_PP': env_float_str('DAILY_TOP5_DNB_MIN_XG_EDGE_PP', 2.5),
        'CONTROLLED_FALLBACK_DNB_MIN_XG_EV_UNCONDITIONAL_PCT': env_float_str(
            'DAILY_TOP5_DNB_MIN_XG_EV_UNCONDITIONAL_PCT',
            3.5,
        ),
        'CONTROLLED_FALLBACK_DNB_MAX_ABS_MODEL_XG_GAP_PP': env_float_str(
            'DAILY_TOP5_DNB_MAX_ABS_MODEL_XG_GAP_PP',
            36.0,
        ),
        'CONTROLLED_FALLBACK_DNB_MAX_XG_EV_UNCONDITIONAL_PCT': env_float_str(
            'DAILY_TOP5_DNB_MAX_XG_EV_UNCONDITIONAL_PCT',
            90.0,
        ),
        'CONTROLLED_FALLBACK_DNB_MAX_XG_EDGE_PP': env_float_str('DAILY_TOP5_DNB_MAX_XG_EDGE_PP', 45.0),
        'CONTROLLED_FALLBACK_DNB_MAX_NO_PUSH_PROBABILITY_PCT': env_float_str(
            'DAILY_TOP5_DNB_MAX_NO_PUSH_PROBABILITY_PCT',
            88.0,
        ),
    }
    if allowed_this_run <= 0:
        env['CONTROLLED_FALLBACK_ENABLED'] = 'false'

    append_env(env)
    report = {
        'status': 'ok',
        'today_local': today,
        'existing_today_picks': existing,
        'target_picks': target,
        'soft_cap_picks': soft_cap,
        'hard_cap_picks': hard_cap,
        'configured_hard_cap_picks': configured_hard_cap,
        'volume_soft_cap_picks': volume_soft_cap,
        'max_picks_per_run': max_per_run,
        'scheduled_max_picks_per_run': scheduled_max_per_run,
        'manual_max_picks_per_run': manual_max_per_run,
        'is_manual_run': is_manual_run,
        'upstream_pick_cap': upstream,
        'allowed_this_run': allowed_this_run,
        'reason': reason,
        'counts': counts,
        'env': env,
        'quality_note': 'Daily top5 policy targets about 5 picks/day, spreads publishing through the day at 1-2 picks per run, keeps Tier C disabled, relaxes only Tier B/proxy reserve gates, preserves hard market guards, and never raises a stricter upstream volume cap.',
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
