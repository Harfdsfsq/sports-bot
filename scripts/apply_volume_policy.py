from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
POLICY_PATH = ROOT / 'config' / 'volume_policy.json'
STATE_PATH = ROOT / '.data' / 'volume-governor-state.json'
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-volume-governor.json'

MSK = ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
UTC = timezone.utc


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def local_date(value: Any) -> str | None:
    dt = parse_dt(value)
    if dt is None:
        return None
    return dt.astimezone(MSK).date().isoformat()


def today_local() -> str:
    return datetime.now(UTC).astimezone(MSK).date().isoformat()


def row_timestamp(row: dict[str, Any]) -> Any:
    for key in (
        'sent_at',
        'published_at',
        'created_at',
        'placed_at',
        'timestamp',
        'updated_at',
    ):
        if row.get(key):
            return row.get(key)
    return None


def count_today_rows(rows: Any, today: str) -> int:
    if not isinstance(rows, list):
        return 0
    seen: set[str] = set()
    total = 0
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if local_date(row_timestamp(row)) != today:
            continue
        key = '|'.join(str(row.get(k) or '') for k in ('match_key', 'family', 'selection', 'point', 'commence_time'))
        if not key.strip('|'):
            key = f'row:{idx}'
        if key in seen:
            continue
        seen.add(key)
        total += 1
    return total


def count_existing_picks_today(today: str) -> dict[str, int]:
    counts: dict[str, int] = {}

    sent_index = load_json(ROOT / '.data' / 'fallback-sent-index.json', {})
    if isinstance(sent_index, dict):
        rows = [v for v in sent_index.values() if isinstance(v, dict)]
        counts['fallback_sent_index'] = count_today_rows(rows, today)
    else:
        counts['fallback_sent_index'] = 0

    state = load_json(ROOT / '.data' / 'state.json', {})
    if isinstance(state, dict):
        for collection in ('bets', 'published_candidates', 'shadow_bets'):
            counts[f'state_{collection}'] = count_today_rows(state.get(collection) or [], today)
    else:
        counts['state_bets'] = 0
        counts['state_published_candidates'] = 0
        counts['state_shadow_bets'] = 0

    # Use the strongest signal to avoid double-counting the same pick recorded in several stores.
    counts['effective_today_picks'] = max(counts.values() or [0])
    return counts


def choose_mode(policy: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw_mode = os.getenv('VOLUME_POLICY_MODE') or str(policy.get('mode') or 'target_3')
    modes = policy.get('modes') if isinstance(policy.get('modes'), dict) else {}
    mode_cfg = modes.get(raw_mode)
    if not isinstance(mode_cfg, dict):
        raw_mode = 'target_3'
        mode_cfg = modes.get(raw_mode) if isinstance(modes.get(raw_mode), dict) else {}
    return raw_mode, dict(mode_cfg)


def flatten_env(mode: str, cfg: dict[str, Any], existing_today: int) -> tuple[dict[str, str], list[str]]:
    reasons: list[str] = []
    target = int(cfg.get('daily_target_picks', 3))
    soft_cap = int(cfg.get('daily_soft_cap_picks', max(target, 4)))
    hard_cap = int(cfg.get('daily_hard_cap_picks', max(soft_cap, target + 1)))
    max_per_run = int(cfg.get('max_picks_per_run', 2))

    env: dict[str, str] = {
        'VOLUME_POLICY_VERSION': 'v10-target-volume',
        'VOLUME_POLICY_MODE': mode,
        'VOLUME_DAILY_TARGET_PICKS': str(target),
        'VOLUME_DAILY_SOFT_CAP_PICKS': str(soft_cap),
        'VOLUME_DAILY_HARD_CAP_PICKS': str(hard_cap),
        'VOLUME_EXISTING_PICKS_TODAY': str(existing_today),
        'CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN': str(max_per_run),
        'CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN': str(max_per_run),
        'MAX_PICKS_PER_RUN': str(max_per_run),
        'CONTROLLED_FALLBACK_MAX_PICKS_PER_MATCH': str(int(cfg.get('max_picks_per_match', 1))),
        'CONTROLLED_FALLBACK_TOTAL_STAKE_CAP_PCT': str(float(cfg.get('daily_stake_cap_pct', 2.25))),
        'CONTROLLED_FALLBACK_SKIP_IF_STAKE_BELOW_MIN': 'true',
        'CONTROLLED_FALLBACK_EXTRA_PICK_STRICT': 'true',
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EV_PCT': str(float(cfg.get('extra_pick_min_ev_pct', 8.5))),
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EDGE_PP': str(float(cfg.get('extra_pick_min_edge_pp', 3.8))),
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_CONFIDENCE': str(float(cfg.get('extra_pick_min_confidence', 69.0))),
        'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REJECT_PROXY_SINGLE_BOOK': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_MARKET_CONFIRMATION_FOR_PROXY': 'true',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT': 'true',
    }

    families = cfg.get('allowed_families') or ['totals', 'dnb']
    env['CONTROLLED_FALLBACK_ALLOWED_FAMILIES'] = ','.join(str(x) for x in families)

    tiers = cfg.get('tiers') if isinstance(cfg.get('tiers'), dict) else {}
    for tier_name in ('A', 'B', 'C'):
        tier = tiers.get(tier_name) if isinstance(tiers.get(tier_name), dict) else {}
        prefix = f'CONTROLLED_FALLBACK_TIER_{tier_name}_'
        allowed = tier.get('allowed_families') or families
        env[prefix + 'ALLOWED_FAMILIES'] = ','.join(str(x) for x in allowed)
        mapping = {
            'min_books': 'MIN_BOOKS',
            'min_confidence': 'MIN_CONFIDENCE',
            'min_quality': 'MIN_QUALITY',
            'min_edge_pp': 'MIN_EDGE_PP',
            'min_ev_pct': 'MIN_EV_PCT',
            'min_publication_score': 'MIN_PUBLICATION_SCORE',
            'max_odds': 'MAX_ODDS',
        }
        for src, dst in mapping.items():
            if src in tier:
                env[prefix + dst] = str(tier[src])

    # Conservative final guards; do not disable xG/negative-value/proxy hard rejects.
    final_guards = cfg.get('final_guards') if isinstance(cfg.get('final_guards'), dict) else {}
    for key, value in final_guards.items():
        env[str(key)] = str(value).lower() if isinstance(value, bool) else str(value)

    # Stake sizing by tier.
    stakes = cfg.get('tier_stake') if isinstance(cfg.get('tier_stake'), dict) else {}
    if 'default_pct' in stakes:
        env['CONTROLLED_FALLBACK_STAKE_PCT'] = str(stakes['default_pct'])
    if 'min_stake' in stakes:
        env['CONTROLLED_FALLBACK_MIN_STAKE'] = str(stakes['min_stake'])
    for tier_name in ('A', 'B', 'C'):
        key = f'max_stake_tier_{tier_name.lower()}'
        if key in stakes:
            env[f'CONTROLLED_FALLBACK_MAX_STAKE_TIER_{tier_name}'] = str(stakes[key])

    if existing_today >= hard_cap:
        env['CONTROLLED_FALLBACK_ENABLED'] = 'false'
        env['CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN'] = '0'
        env['CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN'] = '0'
        env['MAX_PICKS_PER_RUN'] = '0'
        reasons.append(f'daily_hard_cap_reached:{existing_today}/{hard_cap}')
    elif existing_today >= soft_cap:
        env['CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN'] = '0'
        env['CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN'] = '0'
        env['MAX_PICKS_PER_RUN'] = '0'
        reasons.append(f'daily_soft_cap_reached:{existing_today}/{soft_cap}')
    elif existing_today >= target:
        # After reaching target, allow only one extra high-quality A/B pick; no micro Tier C expansion.
        env['CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN'] = '1'
        env['CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN'] = '1'
        env['MAX_PICKS_PER_RUN'] = '1'
        env['CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED'] = 'false'
        reasons.append(f'daily_target_reached_extra_pick_strict:{existing_today}/{target}')
    else:
        env['CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED'] = str(bool(cfg.get('tier_c_publish_enabled', True))).lower()
        reasons.append(f'target_mode_active:{existing_today}/{target}')

    return env, reasons


def append_github_env(env: dict[str, str]) -> None:
    target = os.getenv('GITHUB_ENV')
    lines = [f'{key}={value}' for key, value in sorted(env.items())]
    if target:
        with open(target, 'a', encoding='utf-8') as fh:
            for line in lines:
                fh.write(line + '\n')
    else:
        print('\n'.join(lines))


def main() -> int:
    policy = load_json(POLICY_PATH, {})
    if not isinstance(policy, dict):
        policy = {}
    mode, cfg = choose_mode(policy)
    today = today_local()
    counts = count_existing_picks_today(today)
    existing_today = int(counts.get('effective_today_picks') or 0)
    env, reasons = flatten_env(mode, cfg, existing_today)

    now_utc = datetime.now(UTC).isoformat()
    payload = {
        'version': 'v10-target-volume',
        'mode': mode,
        'today_local': today,
        'now_utc': now_utc,
        'existing_picks_today': existing_today,
        'counts': counts,
        'decision_reasons': reasons,
        'applied_env': env,
        'quality_policy': {
            'hard_guards_preserved': [
                'canonical_negative_value',
                'xg_direction_conflict',
                'xg_probability_gap_hard_reject',
                'dnb_outlier_guard',
                'proxy_single_book_reject',
            ],
            'target': 'increase daily volume through Tier B/C micro only, not through hard-guard bypass',
        },
    }
    write_json(STATE_PATH, payload)
    write_json(EXPORT_PATH, payload)
    append_github_env(env)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
