from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-fast-run-depth-check.json')
UTC = timezone.utc


def load_json(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        pass
    return default


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def as_int(value: Any) -> int:
    try:
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value)))
    except Exception:
        return 0


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding='utf-8', errors='replace')
    except Exception:
        return ''


def main() -> int:
    debug = load_json('.logs/debug-last-run.json', {})
    summary = first_dict(debug.get('summary'))
    stats = first_dict(summary.get('source_stats'))
    odds = first_dict(stats.get('odds_api_io'))
    bzz = first_dict(stats.get('bzzoiro'))
    run_log_text = _read_text('.data/exports/latest-run-bot.log').lower()
    observed = {
        'matches_seen': as_int(summary.get('matches_seen')),
        'matches_with_offers': as_int(summary.get('matches_with_offers')),
        'odds_api_io_odds_req': as_int(odds.get('odds_requests')),
        'matches_with_2plus_books': as_int(odds.get('matches_with_2plus_books')),
        'bookmakers_seen': as_int(odds.get('bookmakers_seen')),
        'odds_api_io_429_seen': 'api.odds-api.io' in run_log_text and '429 too many requests' in run_log_text,
        'requested_bookmakers': odds.get('requested_bookmakers') or '',
        'account2_missing': bool(odds.get('account2_missing')),
        'bzzoiro_secondary_offers': as_int(bzz.get('secondary_offers_added')),
        'bzzoiro_v2_odds': as_int(bzz.get('v2_odds_resources')),
    }
    warnings: list[str] = []
    recommendations: list[str] = []
    if observed['matches_seen'] < 80:
        warnings.append('fast_run_match_window_too_thin')
        recommendations.append('increase PUBLISH_WINDOW_HOURS/FAST_RUN_WINDOW_HOURS or avoid internal app fast shortcuts')
    if observed.get('odds_api_io_429_seen'):
        warnings.append('odds_api_io_rate_limited_429_before_odds_backfill')
        recommendations.append('use account2 for odds-api.io event lookup in fast mode or wait for cooldown')
    if observed['odds_api_io_odds_req'] < 8:
        warnings.append('odds_api_io_request_depth_too_low')
        recommendations.append('source latest-fast-run-env.sh before app.cli and keep 24h publish window')
    if observed['bookmakers_seen'] < 4 or 'Betfair' not in str(observed['requested_bookmakers']):
        warnings.append('odds_api_io_second_account_or_bookmaker_group_missing')
        recommendations.append('map ODDS_API_IO_KEY2/ODDS_API_IO_ACC2_KEY from ODDS_API_IO_KEY_2 and request Betfair Exchange,Sbobet')
    if observed['matches_with_2plus_books'] <= 0:
        warnings.append('zero_matches_with_2plus_books')
    if observed['bzzoiro_secondary_offers'] <= 0:
        warnings.append('zero_bzzoiro_secondary_odds')
    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'ok': not warnings,
        'observed': observed,
        'warnings': warnings,
        'recommendations': recommendations,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
