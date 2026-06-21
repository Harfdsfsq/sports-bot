from __future__ import annotations

"""Post-process controlled fallback diagnostics.

Diagnostic only: this script never publishes. It answers two questions that the
normal Telegram cap hides:
1. Which candidates would be next if the daily fallback cap had not been reached?
2. Why are coverage-ready A-tier rows not becoming A-tier publications?
"""

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
REPORT = EXPORT / 'latest-controlled-fallback-report.json'
SHADOW_OUT = EXPORT / 'latest-controlled-fallback-shadow-ranking.json'
A_TIER_OUT = EXPORT / 'latest-a-tier-publication-diagnostics.json'

DAILY_PREFIX = 'controlled_fallback_daily_limit_reached'


def load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return default
    return default


def dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def point(value: Any) -> str:
    if value in (None, '', 'null'):
        return ''
    try:
        f = float(str(value).replace(',', '.'))
        return str(int(f)) if f.is_integer() else f'{f:g}'
    except Exception:
        return norm(value)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        f = float(str(value).replace(',', '.'))
        return f if math.isfinite(f) else default
    except Exception:
        return default


def as_int(value: Any) -> int:
    try:
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(value or 0))
    except Exception:
        return 0


def kickoff_date(row: dict[str, Any]) -> str:
    for key in ('commence_time', 'kickoff_utc', 'kickoff', 'start_time', 'match_key', 'canonical_match_id', 'event_key'):
        match = re.search(r'20\d{2}-\d{2}-\d{2}', str(row.get(key) or ''))
        if match:
            return match.group(0)
    return ''


def team_value(row: dict[str, Any], side: str) -> str:
    if side == 'home':
        keys = ('home_team', 'home', 'home_name', 'team_home')
    else:
        keys = ('away_team', 'away', 'away_name', 'team_away')
    for key in keys:
        value = norm(row.get(key))
        if value:
            return value
    return ''


def visible_match_key(row: dict[str, Any]) -> str:
    home = team_value(row, 'home')
    away = team_value(row, 'away')
    date = kickoff_date(row)
    league = norm(row.get('league_name') or row.get('league') or row.get('competition'))
    if home and away:
        return '|'.join([date, league, home, away])
    raw = norm(row.get('match_key') or row.get('canonical_match_id') or row.get('event_key'))
    return raw


def canonical_family(row: dict[str, Any]) -> str:
    raw = norm(row.get('family') or row.get('market_family') or row.get('market') or row.get('market_key'))
    if any(token in raw for token in ('total', 'goals', 'over under')):
        return 'totals'
    if any(token in raw for token in ('spread', 'handicap', 'фора')):
        return 'spreads'
    return raw


def canonical_selection(row: dict[str, Any]) -> str:
    explicit = norm(row.get('selection_key'))
    selection = str(row.get('selection') or '').strip().casefold().replace('ё', 'е')
    if explicit in {'under', 'over', 'home', 'away', 'draw'}:
        return explicit
    if canonical_family(row) in {'totals', 'teamtotals', 'spreads'} or 'тотал' in selection:
        if any(x in selection for x in ('under', 'меньше', 'тотал меньше', 'тм')):
            return 'under'
        if any(x in selection for x in ('over', 'больше', 'тотал больше', 'тб')):
            return 'over'
    return explicit or norm(selection)


def line_point(row: dict[str, Any]) -> str:
    explicit = point(row.get('point') or row.get('line') or row.get('handicap'))
    if explicit:
        return explicit
    # Some rows store the line only inside localized selection text.
    selection = str(row.get('selection') or '')
    match = re.search(r'(?<!\d)(\d+(?:[\.,]\d+)?)(?!\d)', selection)
    return point(match.group(1)) if match else ''


def candidate_key(row: dict[str, Any]) -> str:
    # Do not prefer provider-specific match_key here. The same visible pick can
    # have different source match ids in debug/rescue pools; shadow ranking must
    # dedupe by the user-visible market key.
    visible = visible_match_key(row)
    if not visible:
        visible = norm(row.get('match_key') or row.get('canonical_match_id') or row.get('event_key'))
    return '|'.join([
        visible,
        canonical_family(row),
        canonical_selection(row),
        line_point(row),
    ])


def reasons(row: dict[str, Any]) -> list[str]:
    val = row.get('reject_reasons') or row.get('reasons') or []
    if isinstance(val, list):
        return [str(x) for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [val]
    return []


def is_daily_reason(reason: str) -> bool:
    return str(reason or '').startswith(DAILY_PREFIX)


def clean_non_daily_reasons(row: dict[str, Any]) -> list[str]:
    return [r for r in reasons(row) if not is_daily_reason(r)]


def metrics(row: dict[str, Any]) -> dict[str, Any]:
    return row.get('metrics') if isinstance(row.get('metrics'), dict) else {}


def score(row: dict[str, Any]) -> float:
    m = metrics(row)
    ev = as_float(m.get('canonical_ev_pct'))
    edge = as_float(m.get('canonical_edge_pp'))
    quality = as_float(m.get('quality_score'))
    confidence = as_float(m.get('confidence'))
    publication = as_float(m.get('publication_score'))
    odds = as_float(m.get('odds') or row.get('odds'))
    non_daily = clean_non_daily_reasons(row)
    reason_penalty = 7.0 * sum(1 for r in non_daily if 'xg' in r or 'conflict' in r)
    reason_penalty += 5.0 * sum(1 for r in non_daily if 'negative' in r or 'отриц' in r or 'below_min' in r)
    odds_penalty = max(0.0, odds - 2.8) * 2.5 if odds else 0.0
    return ev + edge * 1.15 + quality * 0.05 + confidence * 0.07 + publication * 0.12 - reason_penalty - odds_penalty


def row_summary(row: dict[str, Any], rank: int) -> dict[str, Any]:
    m = metrics(row)
    rs = reasons(row)
    non_daily = clean_non_daily_reasons(row)
    return {
        'rank': rank,
        'shadow_score': round(score(row), 3),
        'status_without_daily_cap': 'would_publish_without_daily_cap' if any(is_daily_reason(r) for r in rs) and not non_daily else 'blocked_by_other_guards',
        'dedupe_key': candidate_key(row),
        'match_key': row.get('match_key'),
        'home_team': row.get('home_team'),
        'away_team': row.get('away_team'),
        'league_name': row.get('league_name'),
        'commence_time': row.get('commence_time'),
        'family': row.get('family'),
        'selection': row.get('selection'),
        'point': row.get('point'),
        'tier': row.get('tier'),
        'candidate_source': row.get('candidate_source'),
        'odds': m.get('odds') or row.get('odds'),
        'edge_pp': m.get('canonical_edge_pp'),
        'ev_pct': m.get('canonical_ev_pct'),
        'confidence': m.get('confidence'),
        'quality_score': m.get('quality_score'),
        'quality_score_source': m.get('quality_score_source'),
        'publication_score': m.get('publication_score'),
        'books_count': m.get('books_count'),
        'odds_sources_count': m.get('odds_sources_count'),
        'confirmation_sources_count': m.get('confirmation_sources_count'),
        'confirmation_sources': m.get('confirmation_sources'),
        'daily_cap_reasons': [r for r in rs if is_daily_reason(r)],
        'other_reasons': non_daily[:12],
    }


def evaluated_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = report.get('evaluated') if isinstance(report.get('evaluated'), list) else []
    return [r for r in rows if isinstance(r, dict)]


def dedupe_best(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    best: dict[str, dict[str, Any]] = {}
    removed = 0
    for row in rows:
        key = candidate_key(row)
        if not key.strip('|'):
            key = json.dumps(row, ensure_ascii=False, sort_keys=True)[:300]
        current = best.get(key)
        if current is None or score(row) > score(current):
            if current is not None:
                removed += 1
            best[key] = row
        else:
            removed += 1
    out = list(best.values())
    out.sort(key=score, reverse=True)
    return out, removed


def build_shadow(report: dict[str, Any]) -> dict[str, Any]:
    rows = evaluated_rows(report)
    unique, duplicates_removed = dedupe_best(rows)
    daily_rows = [r for r in unique if any(is_daily_reason(x) for x in reasons(r))]
    clean_after_cap = [r for r in daily_rows if not clean_non_daily_reasons(r)]
    blocked_after_cap = [r for r in daily_rows if clean_non_daily_reasons(r)]
    reason_counter = Counter()
    reason_counter_without_daily = Counter()
    for row in unique:
        for reason in reasons(row):
            reason_counter[reason] += 1
            if not is_daily_reason(reason):
                reason_counter_without_daily[reason] += 1
    top_clean = [row_summary(row, idx + 1) for idx, row in enumerate(sorted(clean_after_cap, key=score, reverse=True)[:12])]
    top_blocked = [row_summary(row, idx + 1) for idx, row in enumerate(sorted(blocked_after_cap, key=score, reverse=True)[:12])]
    return {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'ok',
        'source_report': str(REPORT),
        'published': bool(report.get('published')),
        'report_status': report.get('status'),
        'evaluated_rows': len(rows),
        'unique_evaluated_rows': len(unique),
        'duplicates_removed': duplicates_removed,
        'daily_cap_blocked_unique_rows': len(daily_rows),
        'blocked_only_by_daily_cap': len(clean_after_cap),
        'blocked_by_daily_cap_and_other_guards': len(blocked_after_cap),
        'reason_counts': dict(reason_counter),
        'reason_counts_without_daily_cap': dict(reason_counter_without_daily),
        'top_would_publish_without_daily_cap': top_clean,
        'top_blocked_after_daily_cap_removed': top_blocked,
    }


def tier_a_reason_alias(reason: str) -> str | None:
    r = str(reason or '')
    if r.startswith('tier_a_'):
        return r
    if 'proxy quality not allowed' in r or 'proxy_quality_not_allowed' in r:
        return 'tier_a_proxy_quality_not_allowed'
    if 'confirmation_sources_below_min' in r:
        return 'tier_a_confirmation_sources_below_min'
    return None


def build_a_tier_diag(report: dict[str, Any]) -> dict[str, Any]:
    rows = evaluated_rows(report)
    unique, duplicates_removed = dedupe_best(rows)
    tier_a_blockers = Counter()
    quality_sources = Counter()
    samples: list[dict[str, Any]] = []
    for row in unique:
        m = metrics(row)
        quality_sources[str(m.get('quality_score_source') or 'unknown')] += 1
        a_reasons = []
        for reason in reasons(row):
            alias = tier_a_reason_alias(reason)
            if alias:
                tier_a_blockers[alias] += 1
                a_reasons.append(alias)
        if a_reasons and len(samples) < 12:
            samples.append(row_summary(row, len(samples) + 1) | {'a_tier_reasons': a_reasons})
    explanation = []
    if tier_a_blockers.get('tier_a_proxy_quality_not_allowed'):
        explanation.append('A-tier rejects proxy quality; it needs raw/model quality, not only reserve proxy score.')
    if any('publication_score_below_min' in k for k in tier_a_blockers):
        explanation.append('A-tier publication score is below min on many candidates.')
    if any('confirmation_sources_below_min' in k for k in tier_a_blockers):
        explanation.append('Some candidates still have fewer than the A-tier confirmation/context minimum.')
    if not tier_a_blockers:
        explanation.append('No explicit tier_a_* blockers found in fallback report; main A-tier likely stops before fallback candidate export: raw candidate generation/quality produced no publishable A pick.')
    return {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'ok',
        'source_report': str(REPORT),
        'evaluated_rows': len(rows),
        'unique_evaluated_rows': len(unique),
        'duplicates_removed': duplicates_removed,
        'quality_score_sources': dict(quality_sources),
        'tier_a_blocker_counts': dict(tier_a_blockers),
        'top_a_tier_blocked_samples': samples,
        'plain_explanation': explanation,
    }


def main() -> int:
    report = load(REPORT, {})
    if not isinstance(report, dict):
        report = {}
    shadow = build_shadow(report)
    a_tier = build_a_tier_diag(report)
    dump(SHADOW_OUT, shadow)
    dump(A_TIER_OUT, a_tier)
    if report:
        report['shadow_ranking'] = {
            'blocked_only_by_daily_cap': shadow.get('blocked_only_by_daily_cap'),
            'blocked_by_daily_cap_and_other_guards': shadow.get('blocked_by_daily_cap_and_other_guards'),
            'top_would_publish_without_daily_cap': shadow.get('top_would_publish_without_daily_cap', [])[:5],
            'reason_counts_without_daily_cap': shadow.get('reason_counts_without_daily_cap', {}),
            'duplicates_removed': shadow.get('duplicates_removed'),
        }
        report['a_tier_diagnostics'] = {
            'tier_a_blocker_counts': a_tier.get('tier_a_blocker_counts', {}),
            'quality_score_sources': a_tier.get('quality_score_sources', {}),
            'plain_explanation': a_tier.get('plain_explanation', []),
        }
        dump(REPORT, report)
        dump(ROOT / 'artifacts' / 'controlled-fallback-report.json', report)
    print(json.dumps({'status': 'ok', 'blocked_only_by_daily_cap': shadow.get('blocked_only_by_daily_cap'), 'duplicates_removed': shadow.get('duplicates_removed'), 'a_tier_blockers': a_tier.get('tier_a_blocker_counts')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
