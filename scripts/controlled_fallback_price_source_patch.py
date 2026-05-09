from __future__ import annotations

import re
from typing import Any

CONTEXT_SOURCES = {
    'sstats', 'bzzoiro', 'clubelo', 'espn', 'weather', 'weatherapi', 'football_data',
    'football-data', 'thesportsdb', 'openfootball', 'openligadb', 'futrixmetrics',
    'newsapi', 'gnews', 'guardian', 'wikidata', 'highlightly', 'market', 'ensemble',
}

ODDS_SOURCE_FIELDS = (
    'odds_sources', 'odds_source_names', 'price_sources', 'price_source_names',
    'bookmaker_sources', 'selected_odds_sources', 'selected_price_sources',
)
ODDS_COUNT_FIELDS = ('odds_sources_count', 'price_sources_count', 'independent_odds_sources_count')
CONTAINER_FIELDS = ('source_summary', 'market_summary', 'price_summary', 'metrics', 'diagnostics')


def _norm(value: Any) -> str:
    return re.sub(r'\s+', '_', str(value or '').strip().lower().replace('-', '_'))


def _values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, str):
        return [part for part in re.split(r'[,+;/|]+', value) if part.strip()]
    return []


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _containers(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    out = [candidate]
    for key in CONTAINER_FIELDS:
        value = candidate.get(key)
        if isinstance(value, dict):
            out.append(value)
            for nested_key in ('metrics', 'source_summary', 'price_summary', 'market_summary'):
                nested = value.get(nested_key)
                if isinstance(nested, dict):
                    out.append(nested)
    return out


def strict_odds_sources_count(candidate: dict[str, Any]) -> int:
    if not isinstance(candidate, dict):
        return 0
    seen: set[str] = set()
    explicit_counts: list[int] = []
    selected_source_seen = False
    for container in _containers(candidate):
        for field in ODDS_SOURCE_FIELDS:
            for item in _values(container.get(field)):
                text = _norm(item)
                if text and text not in CONTEXT_SOURCES:
                    seen.add(text)
        for field in ODDS_COUNT_FIELDS:
            value = _as_int(container.get(field), 0)
            if value > 0:
                explicit_counts.append(value)
        for field in ('selected_source', 'source', 'odds_source', 'price_source'):
            text = _norm(container.get(field))
            if text and text not in CONTEXT_SOURCES:
                selected_source_seen = True
                seen.add(text)
    count_from_names = len(seen)
    count_from_explicit = max(explicit_counts or [0])
    if count_from_explicit > 0:
        return max(count_from_names, count_from_explicit)
    return max(count_from_names, 1 if selected_source_seen else 0)


def apply(module: Any) -> dict[str, Any]:
    module._odds_sources_count = strict_odds_sources_count
    return {'status': 'installed'}
