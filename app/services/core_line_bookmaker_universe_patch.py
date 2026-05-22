from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / '.data' / 'exports' / 'latest-core-line-bookmaker-universe.json'
_INSTALLED = False

CORE_LINE_SOURCES = {'bzzoiro', 'sportlogic'}
PRIMARY_LINE_SOURCES = {'odds_api_io', 'bzzoiro'}
SINGLE_LINE_ENV = 'CORE_LINE_BOOKMAKER_UNIVERSE_ALLOW_SINGLE_SOURCE'
HYBRID_ENV = 'CONTROLLED_FALLBACK_SINGLE_LINE_CONTEXT_MODE_ENABLED'


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _norm_book(factory: Any, value: Any) -> str:
    try:
        return str(factory._norm_book(str(value or '')) or '').strip().lower()
    except Exception:
        return str(value or '').strip().lower()


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _offer_source(offer: Any) -> str:
    return str(getattr(offer, 'source', '') or '').strip().lower()


def _collect_external_books(factory: Any, offers_by_match: dict[str, list[Any]]) -> tuple[set[str], dict[str, Any]]:
    allow_single_source = _truthy(os.getenv(SINGLE_LINE_ENV), _truthy(os.getenv(HYBRID_ENV), False))
    books: set[str] = set()
    stats = {
        'matches_seen': 0,
        'matches_with_external_core_lines': 0,
        'matches_with_2plus_sources': 0,
        'external_books_sample': [],
    }
    sample: list[str] = []
    for _match_key, offers in (offers_by_match or {}).items():
        if not offers:
            continue
        stats['matches_seen'] += 1
        sources = {_offer_source(o) for o in offers if _offer_source(o)}
        has_external = bool(sources & CORE_LINE_SOURCES)
        if has_external:
            stats['matches_with_external_core_lines'] += 1
        if len(sources) >= 2:
            stats['matches_with_2plus_sources'] += 1
        if not has_external:
            continue
        if not allow_single_source and len(sources) < 2:
            continue
        # Prefer adding books only where external source contributes to a multi-source line.
        for offer in offers:
            src = _offer_source(offer)
            if src not in CORE_LINE_SOURCES:
                continue
            book = _norm_book(factory, getattr(offer, 'bookmaker', ''))
            if not book:
                continue
            books.add(book)
            if len(sample) < 30 and book not in sample:
                sample.append(book)
    stats['external_books_sample'] = sample
    return books, stats


def _install_candidate_factory_patch(report: dict[str, Any]) -> None:
    from app.services.model import CandidateFactory

    current = getattr(CandidateFactory, 'build_candidates', None)
    if not callable(current) or getattr(current, '_harizon_core_line_bookmaker_universe', False):
        report['candidate_factory'] = 'already_wrapped_or_missing'
        return

    def build_candidates_with_core_line_books(self: Any, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        extra_books, stats = _collect_external_books(self, offers_by_match or {})
        old_target = set(getattr(self, 'target_books', set()) or set())
        old_consensus = set(getattr(self, 'consensus_books', set()) or set())
        try:
            if extra_books:
                self.target_books = set(old_target) | set(extra_books)
                self.consensus_books = set(old_consensus) | set(extra_books)
            candidates, rejections, debug = current(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=market_signals_by_match)
            debug = dict(debug or {})
            debug['core_line_bookmaker_universe'] = {
                **stats,
                'extra_books_added': len(extra_books),
                'target_books_before': len(old_target),
                'consensus_books_before': len(old_consensus),
                'candidates_after': len(candidates or []),
            }
            _write({'created_at_utc': datetime.now(UTC).isoformat(), 'stage': 'build_candidates', **debug['core_line_bookmaker_universe']})
            return candidates, rejections, debug
        finally:
            try:
                self.target_books = old_target
                self.consensus_books = old_consensus
            except Exception:
                pass

    build_candidates_with_core_line_books._harizon_core_line_bookmaker_universe = True  # type: ignore[attr-defined]
    CandidateFactory.build_candidates = build_candidates_with_core_line_books  # type: ignore[assignment]
    report['candidate_factory'] = 'wrapped'


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    _INSTALLED = True
    report: dict[str, Any] = {
        'status': 'starting',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'allow_single_source': _truthy(os.getenv(SINGLE_LINE_ENV), _truthy(os.getenv(HYBRID_ENV), False)),
        'core_line_sources': sorted(CORE_LINE_SOURCES),
        'note': 'SStats is intentionally excluded from line sources; it is context only.',
    }
    try:
        _install_candidate_factory_patch(report)
        report['status'] = 'installed'
    except Exception as exc:
        report['status'] = 'error'
        report['error'] = f'{type(exc).__name__}: {exc}'
    _write(report)
    return report
