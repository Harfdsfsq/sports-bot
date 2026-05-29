from __future__ import annotations

"""Merge successful targeted secondary-provider probes into context-source index.

This is intentionally conservative: it only adds fixture/context evidence for
explicitly matched candidate rows.  It does not add odds, does not change model
probabilities, and does not bypass xG/value/line-movement guards.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
PROBE = EXPORT / 'latest-targeted-secondary-provider-probe.json'
INDEX = EXPORT / 'latest-context-source-index.json'
OUT = EXPORT / 'latest-targeted-secondary-context-merge.json'

ALLOWED_CONTEXT_PROVIDERS = {'highlightly', 'api_football', 'allsportsapi'}


def load_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def normalize_sources(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip().lower() for x in value if str(x).strip()]
    if isinstance(value, set):
        return sorted(str(x).strip().lower() for x in value if str(x).strip())
    if isinstance(value, str) and value.strip():
        return [value.strip().lower()]
    return []


def index_by_match(index: dict[str, Any]) -> dict[str, list[str]]:
    raw = index.get('by_match') or index.get('matches') or {}
    out: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                sources = value.get('sources') or value.get('context_sources') or value.get('providers') or []
            else:
                sources = value
            out[str(key)] = normalize_sources(sources)
    return out


def source_counts(by_match: dict[str, list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sources in by_match.values():
        for source in set(normalize_sources(sources)):
            counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items()))


def matched_contexts_from_probe(probe: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    providers = probe.get('providers') if isinstance(probe.get('providers'), dict) else {}
    for provider, payload in providers.items():
        key = str(provider or '').strip().lower()
        if key not in ALLOWED_CONTEXT_PROVIDERS or not isinstance(payload, dict):
            continue
        for row in payload.get('matched_contexts') or []:
            if not isinstance(row, dict):
                continue
            match_key = str(row.get('match_key') or '').strip()
            if match_key:
                rows.append({**row, 'provider': key})
    return rows


def main() -> int:
    probe = load_json(PROBE, {})
    index = load_json(INDEX, {})
    if not isinstance(index, dict):
        index = {}
    by = index_by_match(index)
    before = {k: list(v) for k, v in by.items()}
    additions: list[dict[str, Any]] = []
    for row in matched_contexts_from_probe(probe if isinstance(probe, dict) else {}):
        mk = str(row.get('match_key') or '').strip()
        provider = str(row.get('provider') or '').strip().lower()
        if not mk or provider not in ALLOWED_CONTEXT_PROVIDERS:
            continue
        sources = by.setdefault(mk, [])
        if provider not in sources:
            sources.append(provider)
            additions.append(row)
    for key, sources in list(by.items()):
        by[key] = sorted(set(normalize_sources(sources)))
    index['by_match'] = by
    index['matches_indexed'] = len(by)
    index['source_counts'] = source_counts(by)
    index['status'] = 'ok'
    notes = index.get('notes') if isinstance(index.get('notes'), list) else []
    note = 'Targeted secondary provider fixture matches are counted as context/alias evidence only, not as odds sources.'
    if note not in notes:
        notes.append(note)
    index['notes'] = notes
    index['updated_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_json(INDEX, index)

    touched = sorted({row['match_key'] for row in additions if row.get('match_key')})
    before_2plus = sum(1 for sources in before.values() if len(set(sources)) >= 2)
    after_2plus = sum(1 for sources in by.values() if len(set(sources)) >= 2)
    report = {
        'status': 'ok',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'probe_status': probe.get('status') if isinstance(probe, dict) else None,
        'additions': len(additions),
        'matches_touched': len(touched),
        'matches_touched_sample': touched[:20],
        'context_2plus_before': before_2plus,
        'context_2plus_after': after_2plus,
        'source_counts': index['source_counts'],
        'allowed_context_providers': sorted(ALLOWED_CONTEXT_PROVIDERS),
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
