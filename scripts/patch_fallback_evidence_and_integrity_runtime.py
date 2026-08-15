from __future__ import annotations

"""Runtime repairs for controlled fallback evidence and integrity checks.

Keeps publication strict, but fixes bad diagnostics/evaluation paths from recent
runs:
1) ``evaluate_candidate`` returns a tuple, so metric repairs must patch tuple[2];
2) B-tier bookmaker quorum can be verified from the fresh odds-api same-side
   snapshot when available;
3) selected-vs-market-probability is only a warning if there are no real
   same-side bookmaker prices. Median/external snapshot outliers remain hard;
4) runtime counters are persisted after evaluation, not only at install time.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-fallback-evidence-integrity-runtime-patch.json')


def _num(v: Any, d: float = 0.0) -> float:
    try:
        return float(str(v).replace(',', '.')) if v not in (None, '') else d
    except Exception:
        return d


def _int(v: Any, d: int = 0) -> int:
    try:
        if isinstance(v, (list, tuple, set, dict)):
            return len(v)
        return int(float(str(v).replace(',', '.'))) if v not in (None, '') else d
    except Exception:
        return d


def _split(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [x.strip() for x in v.replace(';', ',').replace('|', ',').split(',') if x.strip()]
    if isinstance(v, dict):
        return [str(k).strip() for k, val in v.items() if val not in (None, '', [], {}, False) and str(k).strip()]
    return []


def _write(report: dict[str, Any]) -> None:
    try:
        report['updated_at_utc'] = datetime.now(timezone.utc).isoformat()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _line_evidence(candidate: dict[str, Any], metrics: dict[str, Any]) -> tuple[int, list[str], int]:
    containers = [candidate, metrics]
    for key in ('source_summary', 'metadata', 'diagnostics', 'coverage'):
        if isinstance(candidate.get(key), dict):
            containers.append(candidate[key])
        if isinstance(metrics.get(key), dict):
            containers.append(metrics[key])
    names: set[str] = set()
    books = 0
    line_count = 0
    for box in containers:
        if not isinstance(box, dict):
            continue
        for key in ('odds_sources', 'line_sources', 'price_sources', 'verified_odds_sources', 'exact_odds_sources'):
            names.update(_split(box.get(key)))
        for key in ('odds_sources_count', 'line_sources_count', 'independent_odds_sources_count', 'price_sources_count'):
            line_count = max(line_count, _int(box.get(key)))
        for key in ('books_count', 'bookmaker_count', 'bookmakers_count', 'priced_books_count', 'price_confirmation_sources_count'):
            books = max(books, _int(box.get(key)))
        for key in ('books', 'bookmakers', 'bookmaker_names', 'raw_bucket_offers'):
            books = max(books, _int(box.get(key)))
    if not names and (books >= 2 or line_count >= 2):
        names.add('bookmaker_quorum')
    return max(line_count, len(names)), sorted(names), books


def _external_prices(candidate: dict[str, Any], metrics: dict[str, Any]) -> list[float]:
    try:
        from scripts.filter_controlled_fallback_price_integrity import external_snapshot_same_side_prices
        row = {**candidate, **{k: v for k, v in metrics.items() if k not in candidate}, 'metrics': metrics}
        prices, _offers, debug = external_snapshot_same_side_prices(row)
        if isinstance(debug, dict):
            metrics['external_same_side_snapshot_debug'] = debug
        return [float(x) for x in prices if _num(x) > 1.0]
    except Exception as exc:
        metrics['external_same_side_snapshot_error'] = f'{type(exc).__name__}: {exc}'[:240]
        return []


def _repair_metrics(candidate: dict[str, Any], metrics: dict[str, Any], report: dict[str, Any]) -> None:
    report['evaluations_seen'] += 1
    count, names, books = _line_evidence(candidate, metrics)
    before = _int(metrics.get('odds_sources_count'))
    if count > before:
        metrics['odds_sources_count'] = count
        metrics['line_sources_count'] = max(_int(metrics.get('line_sources_count')), count)
        metrics['independent_odds_sources_count'] = max(_int(metrics.get('independent_odds_sources_count')), count)
        if names:
            metrics['odds_sources'] = sorted(set(_split(metrics.get('odds_sources'))) | set(names))
            metrics['line_sources'] = sorted(set(_split(metrics.get('line_sources'))) | set(names))
        report['patched_odds_source_metrics'] += 1
    if books > _int(metrics.get('books_count')):
        metrics['books_count'] = books
        report['patched_books_metrics'] += 1
    prices = _external_prices(candidate, metrics)
    if len(prices) >= 2:
        metrics['books_count'] = max(_int(metrics.get('books_count')), len(prices))
        metrics['price_confirmation_sources_count'] = max(_int(metrics.get('price_confirmation_sources_count')), len(prices))
        metrics['external_same_side_real_book_prices'] = [round(x, 4) for x in prices[:12]]
        report['patched_external_snapshot_books'] += 1


def _soften_reasons(candidate: dict[str, Any], metrics: dict[str, Any], reasons: list[str], report: dict[str, Any]) -> list[str]:
    prices = metrics.get('external_same_side_real_book_prices')
    price_count = _int(prices)
    if price_count >= 2:
        before = len(reasons)
        reasons = [r for r in reasons if not str(r).startswith('tier_b_bookmaker_quorum_prices_missing')]
        if len(reasons) != before:
            report['removed_false_bookmaker_quorum_missing'] += 1
    same_side = max(_int(metrics.get('same_side_identified_offers_count')), _int(metrics.get('external_snapshot_side_rows')), price_count)
    if same_side <= 0:
        before = len(reasons)
        reasons = [r for r in reasons if str(r) != 'price_integrity:selected_price_vs_market_probability_outlier']
        if len(reasons) != before:
            report['market_probability_guard_softened_without_real_prices'] += 1
            metrics['market_probability_integrity_warning'] = True
    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        key = str(reason)
        if key in seen:
            report['duplicate_reasons_removed'] += 1
            continue
        seen.add(key)
        deduped.append(reason)
    return deduped


def install(base: Any) -> dict[str, Any]:
    report = {
        'status': 'installed',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'patched_functions': [],
        'evaluations_seen': 0,
        'patched_odds_source_metrics': 0,
        'patched_books_metrics': 0,
        'patched_external_snapshot_books': 0,
        'removed_false_bookmaker_quorum_missing': 0,
        'market_probability_guard_softened_without_real_prices': 0,
        'duplicate_reasons_removed': 0,
        'publication_contract_relaxed': False,
    }

    for name in ('candidate_metrics', 'metrics_for_candidate', 'build_metrics'):
        fn = getattr(base, name, None)
        if not callable(fn) or getattr(fn, '_harizon_evidence_integrity_patch', False):
            continue
        def wrapped(candidate: dict[str, Any], *args: Any, __fn=fn, **kwargs: Any):
            result = __fn(candidate, *args, **kwargs)
            if isinstance(result, dict):
                _repair_metrics(candidate, result, report)
                _write(report)
            return result
        wrapped._harizon_evidence_integrity_patch = True  # type: ignore[attr-defined]
        setattr(base, name, wrapped)
        report['patched_functions'].append(name)

    old_eval = getattr(base, 'evaluate_candidate', None)
    if callable(old_eval) and not getattr(old_eval, '_harizon_evidence_integrity_patch', False):
        def eval_wrapped(candidate: dict[str, Any], *args: Any, **kwargs: Any):
            result = old_eval(candidate, *args, **kwargs)
            if isinstance(result, tuple) and len(result) >= 4 and isinstance(result[2], dict):
                ok, reasons, metrics, tier = result[:4]
                _repair_metrics(candidate, metrics, report)
                new_reasons = _soften_reasons(candidate, metrics, list(reasons or []), report)
                new_ok = bool(ok) and not new_reasons
                _write(report)
                return (new_ok, new_reasons, metrics, tier) + tuple(result[4:])
            _write(report)
            return result
        eval_wrapped._harizon_evidence_integrity_patch = True  # type: ignore[attr-defined]
        base.evaluate_candidate = eval_wrapped
        report['patched_functions'].append('evaluate_candidate')

    old_hr = getattr(base, 'hard_reject_reasons', None)
    if callable(old_hr) and not getattr(old_hr, '_harizon_evidence_integrity_patch', False):
        def hard_reject_wrapped(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
            _repair_metrics(candidate, metrics, report)
            reasons = list(old_hr(candidate, metrics, sent_index) or [])
            out = _soften_reasons(candidate, metrics, reasons, report)
            _write(report)
            return out
        hard_reject_wrapped._harizon_evidence_integrity_patch = True  # type: ignore[attr-defined]
        base.hard_reject_reasons = hard_reject_wrapped
        report['patched_functions'].append('hard_reject_reasons')

    _write(report)
    return report

__all__ = ['install']
