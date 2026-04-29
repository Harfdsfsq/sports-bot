from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
OUT_JSON = ROOT / '.data' / 'exports' / 'latest-match-analysis-audit.json'
OUT_TXT = ROOT / '.data' / 'exports' / 'latest-match-analysis-audit.txt'


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


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


def first_dict(paths: list[str | Path]) -> dict[str, Any]:
    for path in paths:
        payload = load_json(path, None)
        if isinstance(payload, dict) and payload:
            return payload
    return {}


def debug_summary() -> dict[str, Any]:
    debug = load_json(ROOT / '.logs' / 'debug-last-run.json', {})
    if isinstance(debug, dict) and isinstance(debug.get('summary'), dict):
        return dict(debug.get('summary') or {})
    return debug if isinstance(debug, dict) else {}


def fallback_report() -> dict[str, Any]:
    return first_dict([
        ROOT / 'artifacts' / 'controlled-fallback-report.json',
        ROOT / '.data' / 'exports' / 'latest-controlled-fallback-report.json',
    ])


def governor_report() -> dict[str, Any]:
    return first_dict([
        ROOT / '.data' / 'exports' / 'latest-daily-best5-governor.json',
        ROOT / '.data' / 'exports' / 'latest-volume-governor.json',
        ROOT / '.data' / 'exports' / 'latest-daily-top5-publish-policy.json',
    ])


def detailed_report() -> dict[str, Any]:
    return first_dict([
        ROOT / '.data' / 'exports' / 'latest-detailed-run-report.json',
    ])


def evaluated_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ('evaluated', 'candidates', 'checked_candidates', 'rejected_candidates'):
        rows = report.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def unwrap(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    candidate = row.get('candidate') if isinstance(row.get('candidate'), dict) else row
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    if not metrics and isinstance(candidate.get('metrics'), dict):
        metrics = candidate.get('metrics')
    raw_reasons = row.get('reject_reasons') or row.get('reasons') or candidate.get('reject_reasons') or candidate.get('reasons') or []
    if isinstance(raw_reasons, str):
        raw_reasons = [raw_reasons]
    return candidate if isinstance(candidate, dict) else {}, metrics if isinstance(metrics, dict) else {}, [str(x) for x in raw_reasons if str(x).strip()]


def metric(candidate: dict[str, Any], metrics: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in metrics:
            return as_float(metrics.get(key), default)
        if key in candidate:
            return as_float(candidate.get(key), default)
    return default


def reason_group(reason: str) -> str:
    text = reason.lower()
    if 'negative' in text or 'canonical' in text or 'value' in text or 'ev_below' in text or 'edge_below' in text:
        return 'value_ev'
    if 'confidence' in text or 'quality' in text:
        return 'confidence_quality'
    if 'book' in text or 'source' in text or 'publish_books' in text:
        return 'book_source_support'
    if 'xg' in text or 'sanity' in text or 'direction' in text or 'dnb_' in text:
        return 'xg_sanity'
    if 'context' in text:
        return 'context'
    if 'market_derived' in text or 'market_signal' in text or 'simple_market' in text:
        return 'market_signal'
    if 'time' in text or 'kickoff' in text or 'started' in text:
        return 'time_window'
    return 'other'


def best_near_misses(rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        candidate, metrics, reasons = unwrap(row)
        ev = metric(candidate, metrics, 'canonical_ev_pct', 'ev_pct')
        edge = metric(candidate, metrics, 'canonical_edge_pp', 'edge_pp')
        confidence = metric(candidate, metrics, 'confidence')
        quality = metric(candidate, metrics, 'quality_score', 'quality')
        if ev <= 0 and edge <= 0:
            continue
        books = as_int(candidate.get('books_count') or metrics.get('books_count') or candidate.get('bookmakers_count'))
        sources = as_int(candidate.get('sources_count') or metrics.get('sources_count'))
        score = ev * 2.2 + edge * 4.0 + max(0.0, confidence - 55.0) * 0.55 + max(0.0, quality - 58.0) * 0.45
        if books >= 2:
            score += 5.0
        elif books == 1:
            score -= 3.0
        if sources >= 2:
            score += 2.5
        if any('xg' in r.lower() and ('conflict' in r.lower() or 'outlier' in r.lower()) for r in reasons):
            score -= 18.0
        out.append({
            'score': round(score, 3),
            'match_key': candidate.get('match_key'),
            'home_team': candidate.get('home_team') or candidate.get('home'),
            'away_team': candidate.get('away_team') or candidate.get('away'),
            'selection': candidate.get('selection'),
            'family': candidate.get('family'),
            'point': candidate.get('point'),
            'odds': candidate.get('odds') or metrics.get('odds'),
            'commence_time': candidate.get('commence_time') or candidate.get('start_time') or candidate.get('kickoff'),
            'ev_pct': round(ev, 3),
            'edge_pp': round(edge, 3),
            'confidence': round(confidence, 3),
            'quality': round(quality, 3),
            'books_count': books,
            'sources_count': sources,
            'reject_reasons': reasons[:8],
            'reason_groups': sorted({reason_group(r) for r in reasons}),
        })
    out.sort(key=lambda x: float(x.get('score') or 0.0), reverse=True)
    return out[:limit]


def main_bottleneck(summary: dict[str, Any], fallback: dict[str, Any], reasons: Counter[str]) -> dict[str, Any]:
    before_quality = as_int(summary.get('candidates_before_quality'))
    raw = as_int(summary.get('candidates_raw'))
    publishable = as_int(summary.get('candidates_publishable'))
    evaluated = as_int(fallback.get('rescue_candidates_checked') or len(evaluated_rows(fallback)))
    groups = Counter({reason_group(k): v for k, v in reasons.items()})
    if before_quality <= 0:
        label = 'candidate_generation'
        msg = 'Матчи и линии есть, но модель почти не создаёт кандидатов до quality-фильтра.'
    elif raw <= 0:
        label = 'quality_filter'
        msg = 'Кандидаты появляются, но отсекаются quality/model guard’ами.'
    elif publishable <= 0 and evaluated > 0:
        label = 'fallback_publish_guards'
        msg = 'Резерв оценивает кандидатов, но публикацию блокируют финальные guard’ы.'
    elif groups.get('book_source_support', 0) >= max(groups.values() or [0]):
        label = 'book_source_support'
        msg = 'Основной bottleneck — недостаточно подтверждения линиями/источниками.'
    elif groups.get('value_ev', 0) >= max(groups.values() or [0]):
        label = 'value_ev'
        msg = 'Основной bottleneck — контрольная value/EV ниже порогов.'
    else:
        label = 'mixed'
        msg = 'Bottleneck смешанный; смотри reason_groups и top_near_misses.'
    return {'label': label, 'message': msg, 'reason_groups': dict(groups)}


def render(payload: dict[str, Any]) -> str:
    counts = payload['funnel']
    bottleneck = payload['bottleneck']
    lines = [
        '🧪 Match Analysis Audit',
        '',
        f"📌 Bottleneck: {bottleneck['label']} — {bottleneck['message']}",
        '',
        '🔢 Воронка',
        f"• Матчи: {counts['matches_seen']} | с линиями: {counts['matches_with_offers']} | контекстов: {counts['contexts_built']}",
        f"• Кандидаты: before quality {counts['candidates_before_quality']} → raw {counts['candidates_raw']} → publishable {counts['candidates_publishable']}",
        f"• Резерв: checked {counts['fallback_checked']} | selected {counts['fallback_selected']}",
        '',
        '🎯 Best near-miss',
    ]
    near = payload.get('top_near_misses') or []
    if not near:
        lines.append('• Нет near-miss с положительным EV/edge.')
    else:
        for idx, item in enumerate(near[:5], start=1):
            lines.append(
                f"{idx}. {item.get('home_team') or ''} — {item.get('away_team') or ''}: "
                f"{item.get('selection')} @{item.get('odds')} | score {item.get('score')} | "
                f"EV {item.get('ev_pct')}% | edge {item.get('edge_pp')} п.п. | "
                f"conf {item.get('confidence')} | q {item.get('quality')} | books {item.get('books_count')}"
            )
    return '\n'.join(lines).strip() + '\n'


def main() -> int:
    summary = debug_summary()
    fallback = fallback_report()
    detailed = detailed_report()
    governor = governor_report()
    evaluated = evaluated_rows(fallback)
    raw_reasons: dict[str, Any] = {}
    if isinstance(summary.get('rejections'), dict):
        raw_reasons.update(summary.get('rejections') or {})
    if isinstance(detailed.get('reason_counts'), dict):
        raw_reasons.update(detailed.get('reason_counts') or {})
    reason_counts = Counter({str(k): as_int(v) for k, v in raw_reasons.items()})
    near = best_near_misses(evaluated)
    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'policy_version': 'match-analysis-audit-v1',
        'funnel': {
            'matches_seen': as_int(summary.get('matches_seen')),
            'matches_before_publish_window': as_int(summary.get('matches_before_publish_window')),
            'matches_with_offers': as_int(summary.get('matches_with_offers')),
            'contexts_built': as_int(summary.get('contexts_built')),
            'context_matches_requested': as_int(summary.get('context_matches_requested')),
            'candidates_before_quality': as_int(summary.get('candidates_before_quality')),
            'candidates_raw': as_int(summary.get('candidates_raw')),
            'candidates_publishable': as_int(summary.get('candidates_publishable')),
            'fallback_checked': as_int(fallback.get('rescue_candidates_checked') or len(evaluated)),
            'fallback_selected': as_int(fallback.get('selected_count')),
            'market_derived_before_quality': as_int(summary.get('candidates_before_quality_with_derived_market_signal')),
            'market_derived_publishable': as_int(summary.get('publishable_with_derived_market_signal')),
        },
        'daily_best5': {
            'stage': governor.get('stage'),
            'existing_today_picks': governor.get('existing_today_picks'),
            'target_picks': governor.get('target_picks'),
            'allowed_this_run': governor.get('allowed_this_run'),
        },
        'top_reasons': dict(reason_counts.most_common(20)),
        'bottleneck': main_bottleneck(summary, fallback, reason_counts),
        'top_near_misses': near,
        'recommendations': [
            'Если book_source_support доминирует — улучшать source reliability и market confirmation вместо простого снятия single-book guard.',
            'Если value_ev доминирует — калибровать вероятности/xG и market-derived probability boost, а не снижать EV-порог глобально.',
            'Если candidate_generation низкий — расширять market-derived/simple-market слой и сохранять кандидатов в дневной pool.',
        ],
    }
    text = render(payload)
    payload['text'] = text
    write_json(OUT_JSON, payload)
    write_text(OUT_TXT, text)
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
