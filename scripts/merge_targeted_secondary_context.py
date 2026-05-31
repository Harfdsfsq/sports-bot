from __future__ import annotations

"""Merge successful targeted secondary-provider probes into context-source index.

This is intentionally conservative: it only adds fixture/context evidence for
explicitly matched candidate rows.  It does not add odds, does not change model
probabilities, and does not bypass xG/value/line-movement guards.

Important: probe rows can arrive with legacy/reversed match_key values from
near-miss/candidate ledgers.  Write context evidence to the original key *and*
canonical home/away/date bridge keys so coverage truth can attach the evidence
to the current day-inventory/runtime-topup row.
"""

import json
import re
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
    if isinstance(value, tuple):
        return [str(x).strip().lower() for x in value if str(x).strip()]
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


def _norm(value: Any) -> str:
    text = str(value or '').lower().strip()
    text = re.sub(r'[^a-z0-9а-яё]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _compact(value: Any) -> str:
    return _norm(value).replace(' ', '_')


def _date_from_any(value: Any) -> str:
    text = str(value or '').strip()
    if len(text) >= 10 and re.match(r'^20\d\d-\d\d-\d\d', text):
        return text[:10]
    return ''


def _canonical_identity(home: Any, away: Any, date: str) -> str:
    h = _compact(home)
    a = _compact(away)
    if not h or not a or not date:
        return ''
    return f'soccer|{h}|{a}|{date}'


def _date_identity(home: Any, away: Any, date: str) -> str:
    h = _compact(home)
    a = _compact(away)
    if not h or not a or not date:
        return ''
    return f'{date}|{h}|{a}'


def context_key_variants(row: dict[str, Any]) -> list[str]:
    """Return conservative key variants for one matched secondary context.

    The primary probe key can be reversed or use old aliases.  Keep it for
    backward compatibility, then add ordered home/away/date variants and a
    reversed variant.  Coverage truth already de-dupes sources per match, so the
    extra bridge keys only help key matching; they do not count as more sources.
    """
    original = str(row.get('match_key') or '').strip()
    home = row.get('home_team') or row.get('home')
    away = row.get('away_team') or row.get('away')
    date = _date_from_any(row.get('kickoff_utc') or row.get('commence_time') or row.get('start_time') or row.get('provider_date'))
    variants: list[str] = []
    for key in (
        original,
        _canonical_identity(home, away, date),
        _canonical_identity(away, home, date),
        _date_identity(home, away, date),
        _date_identity(away, home, date),
    ):
        if key and key not in variants:
            variants.append(key)
    return variants


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
            if match_key or (row.get('home_team') and row.get('away_team')):
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
    bridge_additions = 0
    for row in matched_contexts_from_probe(probe if isinstance(probe, dict) else {}):
        provider = str(row.get('provider') or '').strip().lower()
        if provider not in ALLOWED_CONTEXT_PROVIDERS:
            continue
        keys = context_key_variants(row)
        if not keys:
            continue
        primary_added = False
        for mk in keys:
            sources = by.setdefault(mk, [])
            if provider not in sources:
                sources.append(provider)
                if not primary_added:
                    additions.append({**row, 'match_key': mk, 'bridge_keys': keys})
                    primary_added = True
                else:
                    bridge_additions += 1
    for key, sources in list(by.items()):
        by[key] = sorted(set(normalize_sources(sources)))
    index['by_match'] = by
    index['matches_indexed'] = len(by)
    index['source_counts'] = source_counts(by)
    index['status'] = 'ok'
    notes = index.get('notes') if isinstance(index.get('notes'), list) else []
    note = 'Targeted secondary provider fixture matches are counted as context/alias evidence only, not as odds sources.'
    bridge_note = 'Secondary context is written to original and canonical home/away/date bridge keys to avoid losing evidence on legacy/reversed match_key formats.'
    for item in (note, bridge_note):
        if item not in notes:
            notes.append(item)
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
        'bridge_additions': bridge_additions,
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
