from __future__ import annotations

"""Export the candidates rejected by quality with enough metrics to debug no-pick runs."""

import json
import os
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-quality-shadow-diagnostics.json')
_INSTALLED = False


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw in (None, ''):
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(value)
    except Exception:
        return default


def _row(candidate: Any) -> dict[str, Any]:
    try:
        data = asdict(candidate) if is_dataclass(candidate) else dict(candidate)
    except Exception:
        data = {}
    ct = data.get('commence_time')
    if hasattr(ct, 'isoformat'):
        data['commence_time'] = ct.isoformat()
    return data


def _rank(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        _as_float(row.get('publication_score')),
        _as_float(row.get('ev_pct')),
        _as_float(row.get('edge_pct')),
        _as_float(row.get('confidence')),
    )


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    except Exception:
        pass


def install(quality_module: Any | None = None) -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    if not _enabled('HARIZON_QUALITY_SHADOW_DIAGNOSTICS_ENABLED', True):
        return {'status': 'disabled'}
    if quality_module is None:
        import app.services.quality as quality_module  # type: ignore[no-redef]
    cls = getattr(quality_module, 'PredictionQualityService', None)
    if cls is None or getattr(cls, '_harizon_quality_shadow_diagnostics', False):
        return {'status': 'missing_or_already_patched'}
    original = cls.apply_to_candidates

    def apply_to_candidates(self: Any, candidates: list[Any], quality_report: dict[str, Any], now_utc: Any):
        before_rows = [_row(item) for item in (candidates or [])]
        passed, rejections, debug = original(self, candidates, quality_report, now_utc)
        decisions = list((debug or {}).get('decisions') or []) if isinstance(debug, dict) else []
        decision_by_sig: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
        for item in decisions:
            if not isinstance(item, dict):
                continue
            decision_by_sig[(item.get('match_key'), item.get('family'), item.get('selection_key'), item.get('point'))] = item
        passed_keys = {getattr(item, 'match_key', None) for item in (passed or [])}
        rows: list[dict[str, Any]] = []
        reason_counts: Counter[str] = Counter()
        for row in before_rows:
            sig = (row.get('match_key'), row.get('family'), row.get('selection_key'), row.get('point'))
            dec = decision_by_sig.get(sig, {})
            source_summary = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
            quality_status = dec.get('status') or source_summary.get('quality_status') or ('passed_quality' if row.get('match_key') in passed_keys else 'unknown')
            reasons = list(dec.get('reasons') or source_summary.get('quality_reasons') or [])
            for reason in reasons or [quality_status]:
                reason_counts[str(reason)] += 1
            rows.append({
                'match_key': row.get('match_key'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'league_name': row.get('league_name'),
                'commence_time': row.get('commence_time'),
                'family': row.get('family'),
                'selection': row.get('selection'),
                'selection_key': row.get('selection_key'),
                'point': row.get('point'),
                'odds': row.get('odds'),
                'ev_pct': row.get('ev_pct'),
                'edge_pct': row.get('edge_pct'),
                'confidence': row.get('confidence'),
                'publication_score': row.get('publication_score'),
                'books_count': row.get('books_count'),
                'sources_count': row.get('sources_count'),
                'context_source': source_summary.get('context_source'),
                'context_sources': source_summary.get('context_sources'),
                'context_sources_count': source_summary.get('context_sources_count'),
                'market_signal_derived': source_summary.get('market_signal_derived'),
                'market_signal_history_ready': source_summary.get('market_signal_history_ready'),
                'market_movement': source_summary.get('market_movement'),
                'line_move_pp': source_summary.get('line_move_pp'),
                'best_vs_consensus_edge_pct': source_summary.get('best_vs_consensus_edge_pct'),
                'quality_status': quality_status,
                'quality_score': dec.get('quality_score') or source_summary.get('quality_score'),
                'quality_reasons': reasons,
                'calibration': dec.get('calibration') or {},
                'learning_adjustment': dec.get('learning_adjustment') or {},
                'segments': dec.get('segments') or [],
            })
        rows.sort(key=_rank, reverse=True)
        _write({
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'status': 'ok',
            'input_candidates': len(before_rows),
            'passed_quality': len(passed or []),
            'rejected_quality': max(0, len(before_rows) - len(passed or [])),
            'reason_counts': dict(reason_counts.most_common(25)),
            'top_rejected': [r for r in rows if str(r.get('quality_status')) != 'passed_quality'][:25],
            'top_all': rows[:25],
        })
        return passed, rejections, debug

    cls.apply_to_candidates = apply_to_candidates
    cls._harizon_quality_shadow_diagnostics = True
    _INSTALLED = True
    _write({'created_at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'installed'})
    return {'status': 'installed'}


if __name__ == '__main__':
    print(json.dumps(install(), ensure_ascii=False, indent=2))
