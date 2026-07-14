from __future__ import annotations

"""Build a conservative readiness report for real-money publication.

The performance sample can look strong before it is statistically useful.  This
script turns the all-time unique ledger into a machine-readable decision so the
operator and future guards can see whether the bot is still in pilot mode.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-publication-readiness-report.json')
ART = Path('artifacts/run-bot/latest-publication-readiness-report.json')


def _wilson_lower_bound(wins: int, losses: int, z: float = 1.96) -> float:
    n = wins + losses
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def _load_performance() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    try:
        from scripts import send_all_time_predictions_report as perf
        rows, meta = perf.collect(None, all_time=True)
        return rows, meta, perf.summary(rows)
    except Exception as exc:
        return [], {'error': f'{type(exc).__name__}: {exc}'}, {}


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, ''):
            return default
        return float(str(v).replace(',', '.'))
    except Exception:
        return default


def _quality(row: dict[str, Any]) -> str:
    try:
        from scripts import send_all_time_predictions_report as perf
        return perf.quality(row)
    except Exception:
        return str(row.get('quality_score_source') or row.get('quality_source') or 'unknown')


def main() -> int:
    rows, meta, summary = _load_performance()
    closed = int(summary.get('closed') or 0)
    wins = int(summary.get('wins') or 0)
    losses = int(summary.get('losses') or 0)
    review = int(summary.get('review') or 0)
    pending = int(summary.get('pending') or 0)
    roi = _num(summary.get('roi_pct'))
    avg_odds = _num(summary.get('avg_odds'))
    breakeven = (1.0 / avg_odds) if avg_odds > 1 else 0.0
    lower = _wilson_lower_bound(wins, losses)

    proxy_rows = [r for r in rows if _quality(r) in {'proxy', 'controlled_fallback', 'unknown_quality'}]
    closed_proxy = [r for r in proxy_rows if str(r.get('status') or '').lower() in {'won', 'lost', 'push', 'void', 'cancelled', 'refunded', 'half_won', 'half_lost'}]

    reasons: list[str] = []
    if closed < 100:
        reasons.append(f'closed_unique_sample_below_100:{closed}/100')
    if review > closed:
        reasons.append(f'unsettled_queue_larger_than_closed:{review}/{closed}')
    if lower <= breakeven:
        reasons.append(f'wilson_lower_hit_rate_not_above_breakeven:{lower*100:.1f}%<={breakeven*100:.1f}%')
    if len(closed_proxy) < 30:
        reasons.append(f'proxy_segment_unproven:{len(closed_proxy)}/30_closed')

    mode = 'pilot_micro_stake' if reasons else 'eligible_for_normal_stake_review'
    max_stake_pct = 0.5 if mode == 'pilot_micro_stake' else 1.0
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'mode': mode,
        'recommended_max_stake_pct_bankroll': max_stake_pct,
        'allow_normal_stake': mode != 'pilot_micro_stake',
        'allow_proxy_publication': False,
        'summary': summary,
        'meta': meta,
        'stats': {
            'closed_unique': closed,
            'wins': wins,
            'losses': losses,
            'pending': pending,
            'needs_settlement': review,
            'roi_pct': roi,
            'avg_odds': avg_odds,
            'breakeven_hit_rate_pct': round(breakeven * 100.0, 2),
            'wilson_lower_hit_rate_pct': round(lower * 100.0, 2),
            'proxy_or_unknown_rows': len(proxy_rows),
            'closed_proxy_or_unknown_rows': len(closed_proxy),
        },
        'reasons': reasons,
        'decision': 'keep strict guards; do not lower xG/line/value/price guards; publish only strict A/B-hard in pilot mode',
    }
    for path in (OUT, ART):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'ok', 'mode': mode, 'closed': closed, 'reasons': reasons}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
