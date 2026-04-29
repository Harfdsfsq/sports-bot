from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
UTC = timezone.utc
POLICY_VERSION = 'v1-unified-daily-best5-governor'
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-daily-best5-governor.json'
VOLUME_EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-volume-governor.json'
TOP5_EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-daily-top5-publish-policy.json'
STATE_PATH = ROOT / '.data' / 'daily-best5-governor-state.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')


def app_tz() -> ZoneInfo:
    name = os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow'
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo('Europe/Moscow')


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


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


def local_date(value: Any, tz: ZoneInfo) -> str | None:
    dt = parse_dt(value)
    if dt is None:
        return None
    return dt.astimezone(tz).date().isoformat()


def row_timestamp(row: dict[str, Any]) -> Any:
    for key in ('sent_at', 'published_at', 'created_at', 'placed_at', 'timestamp', 'updated_at'):
        if row.get(key):
            return row.get(key)
    return None


def norm(value: Any) -> str:
    return str(value or '').strip().lower()


def pick_key(row: dict[str, Any], fallback: str) -> str:
    parts = [
        norm(row.get('match_key')),
        norm(row.get('family')),
        norm(row.get('selection')),
        norm(row.get('selection_key')),
        norm(row.get('point')),
        norm(row.get('team_side')),
        norm(row.get('commence_time') or row.get('start_time') or row.get('kickoff')),
    ]
    key = '|'.join(parts).strip('|')
    if key:
        return key
    parts = [
        norm(row.get('home_team') or row.get('home')),
        norm(row.get('away_team') or row.get('away')),
        norm(row.get('selection')),
        norm(row.get('odds')),
        norm(row.get('commence_time') or row.get('start_time') or row.get('kickoff')),
    ]
    return '|'.join(parts).strip('|') or fallback


def collect_today_keys(rows: Any, today: str, tz: ZoneInfo) -> tuple[set[str], int]:
    if not isinstance(rows, list):
        return set(), 0
    keys: set[str] = set()
    scanned = 0
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if local_date(row_timestamp(row), tz) != today:
            continue
        scanned += 1
        keys.add(pick_key(row, f'row:{idx}'))
    return keys, scanned


def count_today_picks(today: str, tz: ZoneInfo) -> dict[str, Any]:
    keys: set[str] = set()
    details: dict[str, Any] = {}

    sent_index = load_json(ROOT / '.data' / 'fallback-sent-index.json', {})
    if isinstance(sent_index, dict):
        rows = [row for row in sent_index.values() if isinstance(row, dict)]
        k, scanned = collect_today_keys(rows, today, tz)
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
            k, scanned = collect_today_keys(state.get(collection), today, tz)
            keys.update(k)
            details[source_name] = len(k)
            details[source_name + '_rows'] = scanned
        shadow_keys, shadow_scanned = collect_today_keys(state.get('shadow_bets'), today, tz)
        details['state_shadow_bets'] = len(shadow_keys)
        details['state_shadow_bets_rows'] = shadow_scanned
    else:
        details['state_bets'] = 0
        details['state_published_candidates'] = 0
        details['state_shadow_bets'] = 0

    details['effective_today_picks'] = len(keys)
    details['effective_count_note'] = 'Deduped real Telegram publications only; shadow/watchlist rows are excluded.'
    return details


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


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw in (None, ''):
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def minutes_from_midnight(local_now: datetime) -> int:
    return local_now.hour * 60 + local_now.minute


def expected_by_now(target: int, local_now: datetime) -> float:
    # Smooth day-progress target. A 5-pick day means roughly one strong pick every 4-5 hours,
    # but the governor only changes thresholds; it never publishes without positive value.
    return max(0.0, min(float(target), float(target) * minutes_from_midnight(local_now) / 1440.0))


def stage_for(existing: int, target: int, expected: float, local_now: datetime) -> str:
    if existing >= target + 2:
        return 'ahead_elite_only'
    if existing >= target:
        return 'after_target_elite_only'
    gap = expected - float(existing)
    if local_now.hour >= 20 and existing < target:
        return 'late_catchup'
    if gap >= 1.25:
        return 'behind_schedule_catchup'
    if existing >= max(0, target - 1):
        return 'last_target_pick'
    return 'on_track_build'


def stage_policy(stage: str, existing: int, target: int, is_manual: bool) -> dict[str, Any]:
    # The system targets about 5/day through dynamic quality gates, not a blind cap.
    base = {
        'allowed_this_run': 1,
        'tier_b_min_confidence': 64.0,
        'tier_b_min_quality': 61.0,
        'tier_b_min_edge_pp': 3.2,
        'tier_b_min_ev_pct': 6.5,
        'tier_b_max_odds': 2.70,
        'final_min_edge_pp': 3.0,
        'final_min_ev_pct': 6.0,
        'require_2_books': True,
        'reject_proxy_single_book': True,
        'proxy_single_source_strict': True,
        'stage_note': 'normal build mode',
    }
    if stage == 'behind_schedule_catchup':
        base.update({
            'allowed_this_run': 2 if existing <= max(1, target - 2) else 1,
            'tier_b_min_confidence': 63.0,
            'tier_b_min_quality': 60.0,
            'tier_b_min_edge_pp': 3.0,
            'tier_b_min_ev_pct': 6.0,
            'tier_b_max_odds': 2.75,
            'final_min_edge_pp': 3.0,
            'final_min_ev_pct': 6.0,
            'require_2_books': True,
            'reject_proxy_single_book': True,
            'stage_note': 'behind schedule: allow up to 2, still require market confirmation',
        })
    elif stage == 'late_catchup':
        base.update({
            'allowed_this_run': 2 if existing <= max(1, target - 2) else 1,
            'tier_b_min_confidence': 64.0,
            'tier_b_min_quality': 61.5,
            'tier_b_min_edge_pp': 3.3,
            'tier_b_min_ev_pct': 7.0,
            'tier_b_max_odds': 2.65,
            'final_min_edge_pp': 3.2,
            'final_min_ev_pct': 6.8,
            'require_2_books': True,
            'reject_proxy_single_book': True,
            'stage_note': 'late catchup: modestly stricter, no single-book shortcut',
        })
    elif stage == 'last_target_pick':
        base.update({
            'allowed_this_run': 1,
            'tier_b_min_confidence': 64.5,
            'tier_b_min_quality': 61.5,
            'tier_b_min_edge_pp': 3.4,
            'tier_b_min_ev_pct': 7.0,
            'tier_b_max_odds': 2.65,
            'final_min_edge_pp': 3.2,
            'final_min_ev_pct': 6.8,
            'stage_note': 'last pick to target: slightly stricter',
        })
    elif stage == 'after_target_elite_only':
        base.update({
            'allowed_this_run': 1,
            'tier_b_min_confidence': 67.0,
            'tier_b_min_quality': 63.0,
            'tier_b_min_edge_pp': 4.0,
            'tier_b_min_ev_pct': 8.5,
            'tier_b_max_odds': 2.55,
            'final_min_edge_pp': 3.8,
            'final_min_ev_pct': 8.0,
            'require_2_books': True,
            'reject_proxy_single_book': True,
            'stage_note': 'target reached: elite-only extra picks',
        })
    elif stage == 'ahead_elite_only':
        base.update({
            'allowed_this_run': 1,
            'tier_b_min_confidence': 69.0,
            'tier_b_min_quality': 64.5,
            'tier_b_min_edge_pp': 4.5,
            'tier_b_min_ev_pct': 10.0,
            'tier_b_max_odds': 2.45,
            'final_min_edge_pp': 4.2,
            'final_min_ev_pct': 9.5,
            'require_2_books': True,
            'reject_proxy_single_book': True,
            'stage_note': 'ahead of target: only exceptional extra picks',
        })
    if is_manual:
        base['allowed_this_run'] = min(2, max(1, int(base['allowed_this_run'])))
    else:
        base['allowed_this_run'] = min(2, max(1, int(base['allowed_this_run'])))
    return base


def append_env(env: dict[str, str]) -> None:
    if GITHUB_ENV:
        with open(GITHUB_ENV, 'a', encoding='utf-8') as fh:
            for key in sorted(env):
                fh.write(f'{key}={env[key]}\n')
    else:
        for key in sorted(env):
            print(f'{key}={env[key]}')


def bool_str(value: bool) -> str:
    return 'true' if value else 'false'


def build_env(policy: dict[str, Any], target: int, existing: int, stage: str) -> dict[str, str]:
    allowed = int(policy['allowed_this_run'])
    require_2_books = bool(policy['require_2_books'])
    reject_single = bool(policy['reject_proxy_single_book'])
    proxy_strict = bool(policy['proxy_single_source_strict'])

    env = {
        'DAILY_BEST5_GOVERNOR_ACTIVE': 'true',
        'DAILY_BEST5_GOVERNOR_VERSION': POLICY_VERSION,
        'DAILY_TOP5_PUBLISH_POLICY_ACTIVE': 'true',
        'DAILY_TOP5_POLICY_VERSION': POLICY_VERSION,
        'VOLUME_POLICY_VERSION': POLICY_VERSION,
        'VOLUME_POLICY_MODE': 'daily_best5',
        'VOLUME_POLICY_STAGE': stage,
        'VOLUME_DAILY_TARGET_PICKS': str(target),
        'VOLUME_DAILY_SOFT_CAP_PICKS': str(target),
        'VOLUME_DAILY_HARD_CAP_PICKS': str(target),
        'VOLUME_EXISTING_PICKS_TODAY': str(existing),
        'CONTROLLED_FALLBACK_ENABLED': 'true',
        'CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN': str(allowed),
        'CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN': str(allowed),
        'MAX_PICKS_PER_RUN': str(allowed),
        'CONTROLLED_FALLBACK_MAX_PICKS_PER_MATCH': '1',
        'CONTROLLED_FALLBACK_TOTAL_STAKE_CAP_PCT': os.getenv('DAILY_BEST5_TOTAL_STAKE_CAP_PCT', '3.0'),
        'CONTROLLED_FALLBACK_SKIP_IF_STAKE_BELOW_MIN': 'true',
        'CONTROLLED_FALLBACK_EXTRA_PICK_STRICT': 'true',
        'CONTROLLED_FALLBACK_ALLOWED_FAMILIES': os.getenv('DAILY_BEST5_ALLOWED_FAMILIES', 'totals,dnb,teamtotals,teamTotals,btts,spreads'),
        'CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES': os.getenv('DAILY_BEST5_TIER_A_ALLOWED_FAMILIES', 'totals,dnb,teamtotals,teamTotals'),
        'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES': os.getenv('DAILY_BEST5_TIER_B_ALLOWED_FAMILIES', 'totals,dnb,teamtotals,teamTotals,btts,spreads'),
        'CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED': 'false',
        'CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES': '',
        'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_A_MIN_CONFIDENCE': os.getenv('DAILY_BEST5_TIER_A_MIN_CONFIDENCE', '66.0'),
        'CONTROLLED_FALLBACK_TIER_A_MIN_QUALITY': os.getenv('DAILY_BEST5_TIER_A_MIN_QUALITY', '66.0'),
        'CONTROLLED_FALLBACK_TIER_A_MIN_EDGE_PP': os.getenv('DAILY_BEST5_TIER_A_MIN_EDGE_PP', '3.5'),
        'CONTROLLED_FALLBACK_TIER_A_MIN_EV_PCT': os.getenv('DAILY_BEST5_TIER_A_MIN_EV_PCT', '6.0'),
        'CONTROLLED_FALLBACK_TIER_A_MAX_ODDS': os.getenv('DAILY_BEST5_TIER_A_MAX_ODDS', '2.70'),
        'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE': str(policy['tier_b_min_confidence']),
        'CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY': str(policy['tier_b_min_quality']),
        'CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP': str(policy['tier_b_min_edge_pp']),
        'CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT': str(policy['tier_b_min_ev_pct']),
        'CONTROLLED_FALLBACK_TIER_B_MAX_ODDS': str(policy['tier_b_max_odds']),
        'CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP': str(policy['final_min_edge_pp']),
        'CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT': str(policy['final_min_ev_pct']),
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EV_PCT': str(policy['tier_b_min_ev_pct']),
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EDGE_PP': str(policy['tier_b_min_edge_pp']),
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_CONFIDENCE': str(policy['tier_b_min_confidence']),
        'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': bool_str(require_2_books),
        'CONTROLLED_FALLBACK_REJECT_PROXY_SINGLE_BOOK': bool_str(reject_single),
        'CONTROLLED_FALLBACK_REQUIRE_MARKET_CONFIRMATION_FOR_PROXY': 'true',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT': bool_str(proxy_strict),
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE': str(policy['tier_b_min_confidence']),
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP': str(policy['tier_b_min_edge_pp']),
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT': str(policy['tier_b_min_ev_pct']),
        'CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_BTTS_SANITY_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_DNB_SANITY_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_DNB_OUTLIER_GUARD_ENABLED': 'true',
        'CONTROLLED_FALLBACK_DNB_MIN_XG_EDGE_PP': os.getenv('DAILY_BEST5_DNB_MIN_XG_EDGE_PP', '2.5'),
        'CONTROLLED_FALLBACK_DNB_MIN_XG_EV_UNCONDITIONAL_PCT': os.getenv('DAILY_BEST5_DNB_MIN_XG_EV_UNCONDITIONAL_PCT', '3.5'),
        'CONTROLLED_FALLBACK_DNB_MAX_ABS_MODEL_XG_GAP_PP': os.getenv('DAILY_BEST5_DNB_MAX_ABS_MODEL_XG_GAP_PP', '36.0'),
        'CONTROLLED_FALLBACK_DNB_MAX_XG_EV_UNCONDITIONAL_PCT': os.getenv('DAILY_BEST5_DNB_MAX_XG_EV_UNCONDITIONAL_PCT', '90.0'),
        'CONTROLLED_FALLBACK_DNB_MAX_XG_EDGE_PP': os.getenv('DAILY_BEST5_DNB_MAX_XG_EDGE_PP', '45.0'),
        'CONTROLLED_FALLBACK_DNB_MAX_NO_PUSH_PROBABILITY_PCT': os.getenv('DAILY_BEST5_DNB_MAX_NO_PUSH_PROBABILITY_PCT', '88.0'),
        'CONTROLLED_FALLBACK_DAILY_TOP5_REASON': f'daily_best5:{stage}:{existing}/{target}',
    }
    return env


def main() -> int:
    tz = app_tz()
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(tz)
    today = now_local.date().isoformat()
    target = max(1, as_int(os.getenv('DAILY_BEST5_TARGET_PICKS') or os.getenv('DAILY_TOP5_TARGET_PICKS'), 5))
    counts = count_today_picks(today, tz)
    existing = as_int(counts.get('effective_today_picks'), 0)
    expected = expected_by_now(target, now_local)
    stage = stage_for(existing, target, expected, now_local)
    is_manual = str(os.getenv('GITHUB_EVENT_NAME') or '').strip().lower() == 'workflow_dispatch'
    policy = stage_policy(stage, existing, target, is_manual)
    env = build_env(policy, target, existing, stage)
    append_env(env)

    payload = {
        'status': 'ok',
        'policy_version': POLICY_VERSION,
        'utc_now': now_utc.isoformat(),
        'local_now': now_local.isoformat(),
        'timezone': str(tz.key),
        'today_local': today,
        'target_picks': target,
        'existing_today_picks': existing,
        'expected_by_now': round(expected, 3),
        'pace_gap': round(expected - existing, 3),
        'stage': stage,
        'allowed_this_run': int(policy['allowed_this_run']),
        'is_manual_run': is_manual,
        'counts': counts,
        'policy': policy,
        'applied_env': env,
        'quality_strategy': {
            'summary': 'One unified daily governor targets the best ~5 picks/day by pacing and dynamic quality thresholds. It does not hard-disable fallback after a fixed cap.',
            'before_target': 'Allow 1-2 picks/run only when value, confidence, market confirmation and sanity guards pass.',
            'after_target': 'Keep fallback enabled, but publish only elite extra picks with stricter EV/edge/confidence/quality.',
            'single_book_policy': 'No broad single-book shortcut. Telegram publication keeps two-book confirmation unless this file is explicitly changed.',
            'tier_c_publication': 'disabled',
        },
    }
    for path in (EXPORT_PATH, VOLUME_EXPORT_PATH, TOP5_EXPORT_PATH, STATE_PATH):
        write_json(path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
