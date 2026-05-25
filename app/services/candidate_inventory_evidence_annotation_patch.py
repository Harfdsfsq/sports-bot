from __future__ import annotations

"""Annotate CandidateFactory rows with day-inventory evidence.

This patch is safe by default: it copies coverage-truth/day-inventory evidence
into candidate diagnostics but does not promote those values into publication
metrics unless CANDIDATE_EVIDENCE_PROMOTE_TO_METRICS=true is explicitly set.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / '.data' / 'exports'
REPORT = EXPORT_DIR / 'latest-candidate-inventory-evidence-annotation.json'
_MARKER = '_harizon_candidate_inventory_evidence_annotation_v1'


def _norm(value: Any) -> str:
    text = str(value or '').lower().strip()
    text = re.sub(r'^soccer\|', '', text)
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return ' '.join(text.split())


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _candidate_key(candidate: Any) -> str:
    key = getattr(candidate, 'match_key', None)
    if key:
        return _norm(key)
    return _norm(f"{getattr(candidate, 'home_team', '')}|{getattr(candidate, 'away_team', '')}|{getattr(candidate, 'commence_time', '')}")


def _truth_index() -> dict[str, dict[str, Any]]:
    truth = _load_json(EXPORT_DIR / 'latest-day-inventory-coverage-truth.json')
    rows = truth.get('rows') if isinstance(truth, dict) else []
    out: dict[str, dict[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            keys = [row.get('match_key'), f"{row.get('home_team')}|{row.get('away_team')}|{row.get('kickoff_utc')}"]
            for key in keys:
                nk = _norm(key)
                if nk:
                    out[nk] = row
    return out


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def install() -> dict[str, Any]:
    try:
        from app.services.model import CandidateFactory
    except Exception as exc:
        return {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}
    current = getattr(CandidateFactory, 'build_candidates', None)
    if getattr(current, _MARKER, False):
        return {'status': 'already_installed'}
    original = current

    def build_candidates_with_inventory_evidence(self: Any, matches: Any, offers_by_match: Any, contexts_by_match: Any, market_signals_by_match: Any = None):
        candidates, rejections, debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=market_signals_by_match)
        idx = _truth_index()
        promote = _bool_env('CANDIDATE_EVIDENCE_PROMOTE_TO_METRICS', False)
        annotated = 0
        promoted = 0
        for cand in list(candidates or []):
            row = idx.get(_candidate_key(cand))
            if not row:
                continue
            annotated += 1
            evidence = {
                'odds_sources_count': row.get('odds_sources_count'),
                'odds_sources': row.get('odds_sources'),
                'price_confirmations': row.get('price_confirmations'),
                'context_sources_count': row.get('context_sources_count'),
                'context_sources': row.get('context_sources'),
                'strict_ready_for_publish': row.get('strict_ready_for_publish'),
                'ready_for_publish': row.get('ready_for_publish'),
            }
            try:
                setattr(cand, 'inventory_evidence', evidence)
                diag = getattr(cand, 'diagnostics', None)
                if not isinstance(diag, dict):
                    diag = {}
                diag['inventory_evidence'] = evidence
                setattr(cand, 'diagnostics', diag)
                if promote:
                    # Explicit opt-in only. Do not silently relax line/context guards.
                    if int(evidence.get('odds_sources_count') or 0) > int(getattr(cand, 'sources_count', 0) or 0):
                        setattr(cand, 'sources_count', int(evidence.get('odds_sources_count') or 0))
                    if int(evidence.get('context_sources_count') or 0) > int(getattr(cand, 'confirmation_sources_count', 0) or 0):
                        setattr(cand, 'confirmation_sources_count', int(evidence.get('context_sources_count') or 0))
                    promoted += 1
            except Exception:
                pass
        report = {
            'created_at_utc': datetime.now(UTC).isoformat(),
            'input_candidates': len(list(candidates or [])),
            'annotated': annotated,
            'promoted_to_metrics': promoted,
            'promotion_enabled': promote,
        }
        debug = dict(debug or {})
        debug['candidate_inventory_evidence_annotation'] = report
        _write_report(report)
        return candidates, rejections, debug

    setattr(build_candidates_with_inventory_evidence, _MARKER, True)
    CandidateFactory.build_candidates = build_candidates_with_inventory_evidence  # type: ignore[assignment]
    return {'status': 'installed', 'marker': _MARKER}
