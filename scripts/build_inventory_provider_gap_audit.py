from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
EXPORT_DIR = ROOT / '.data' / 'exports'
DAY_DIR = ROOT / '.data' / 'day_inventory'
OUT = EXPORT_DIR / 'latest-inventory-provider-gap-audit.json'


def load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е').replace('´', "'")
    text = re.sub(r'\b(fc|sc|cf|fk|ac|cd|club|de|la|the|w|women|u19|u20|u21|ii|2)\b', ' ', text)
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def row_date(row: dict[str, Any]) -> str:
    for key in ('kickoff_utc', 'commence_time', 'start_time', 'kickoff', 'date'):
        text = str(row.get(key) or '')
        m = re.search(r'(20\d{2}-\d{2}-\d{2})', text)
        if m:
            return m.group(1)
    text = str(row.get('match_key') or row.get('canonical_match_id') or '')
    m = re.search(r'(20\d{2}-\d{2}-\d{2})', text)
    return m.group(1) if m else ''


def home(row: dict[str, Any]) -> str:
    return str(row.get('home_team') or row.get('home') or row.get('home_name') or '').strip()


def away(row: dict[str, Any]) -> str:
    return str(row.get('away_team') or row.get('away') or row.get('away_name') or '').strip()


def key(row: dict[str, Any]) -> str:
    d = row_date(row)
    h = norm(home(row))
    a = norm(away(row))
    if d and h and a:
        return f'{d}|{h}|{a}'
    return norm(row.get('match_key') or row.get('canonical_match_id'))


def rows_from(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for k in ('matches', 'rows', 'items', 'data', 'canonical_matches', 'contexts', 'observations'):
        v = payload.get(k)
        if isinstance(v, list):
            out.extend(x for x in v if isinstance(x, dict))
    return out


def sources(row: dict[str, Any], name: str) -> set[str]:
    vals: set[str] = set()
    for obj in (row, row.get('coverage') if isinstance(row.get('coverage'), dict) else {}, row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}):
        if not isinstance(obj, dict):
            continue
        value = obj.get(name)
        if isinstance(value, list):
            vals.update(str(x).strip().lower() for x in value if str(x).strip())
        elif isinstance(value, str) and value.strip():
            vals.update(x.strip().lower() for x in re.split(r'[,|;]', value) if x.strip())
    return {x for x in vals if x}


def count_val(row: dict[str, Any], *names: str) -> int:
    for obj in (row, row.get('coverage') if isinstance(row.get('coverage'), dict) else {}, row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}):
        if not isinstance(obj, dict):
            continue
        for name in names:
            value = obj.get(name)
            if isinstance(value, list):
                return len(value)
            try:
                if value not in (None, ''):
                    return int(float(str(value)))
            except Exception:
                pass
    return 0


def build_provider_indexes() -> dict[str, set[str]]:
    indexes: dict[str, set[str]] = defaultdict(set)
    provider_files = {
        'sstats_crosswalk': ['latest-sstats-crosswalk.json'],
        'sstats_deep': ['latest-sstats-deep-inventory-enrichment.json'],
        'bzzoiro_v2': ['latest-bzzoiro-v2-odds-hints-by-match.json', 'latest-context-observations.json'],
        'odds_api_io': ['latest-odds-api-io-offer-snapshot.json'],
        'sportlogic': ['latest-sportlogic-debug.json', 'latest-sportlogic-coverage-probe.json'],
    }
    for provider, files in provider_files.items():
        for fn in files:
            payload = load(EXPORT_DIR / fn, {})
            if isinstance(payload, dict) and isinstance(payload.get('matches'), dict):
                indexes[provider].update(str(k) for k in payload.get('matches', {}).keys())
            for row in rows_from(payload):
                k = key(row)
                if k:
                    indexes[provider].add(k)
                raw = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
                if raw:
                    indexes[provider].add(norm(raw))
    return indexes


def main() -> int:
    inventory = load(DAY_DIR / 'latest.json', {}) or load(DAY_DIR / 'current.json', {})
    rows = rows_from(inventory)
    indexes = build_provider_indexes()
    dup_counter = Counter(key(r) for r in rows if key(r))
    audited = []
    blocker_counts: Counter[str] = Counter()
    provider_hit_counts: Counter[str] = Counter()
    for row in rows:
        k = key(row)
        odds_sources = sources(row, 'odds_sources')
        context_sources = sources(row, 'context_sources')
        books = max(count_val(row, 'books_count', 'bookmaker_count'), len(sources(row, 'bookmakers')), len(sources(row, 'books')), count_val(row, 'price_confirmations'))
        provider_hits = sorted(p for p, keys in indexes.items() if k in keys or norm(row.get('match_key') or row.get('canonical_match_id')) in keys)
        for p in provider_hits:
            provider_hit_counts[p] += 1
        missing = []
        if len(odds_sources) < 2:
            missing.append('odds_source_2plus')
        if books < 2:
            missing.append('bookmaker_2plus')
        if len(context_sources) < 2:
            missing.append('context_source_2plus')
        if dup_counter.get(k, 0) > 1:
            missing.append('semantic_duplicate')
        for m in missing:
            blocker_counts[m] += 1
        audited.append({
            'match_key': row.get('match_key') or row.get('canonical_match_id') or k,
            'semantic_key': k,
            'home_team': home(row),
            'away_team': away(row),
            'kickoff': row.get('kickoff_utc') or row.get('commence_time'),
            'league_name': row.get('league_name'),
            'odds_sources': sorted(odds_sources),
            'context_sources': sorted(context_sources),
            'books_count': books,
            'provider_artifact_hits': provider_hits,
            'missing': missing,
            'next_actions': [
                'target_bzzoiro_v2_context_or_odds' if 'bzzoiro_v2' not in provider_hits and (len(context_sources) < 2 or len(odds_sources) < 2) else '',
                'target_sstats_deep_context' if 'sstats_deep' not in provider_hits and len(context_sources) < 2 else '',
                'target_odds_api_price_backfill' if 'odds_api_io' not in provider_hits and (len(odds_sources) < 2 or books < 2) else '',
                'collapse_semantic_duplicate' if dup_counter.get(k, 0) > 1 else '',
            ],
        })
    for item in audited:
        item['next_actions'] = [x for x in item['next_actions'] if x]
    report = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'ok',
        'inventory_rows': len(rows),
        'semantic_unique_matches': len(dup_counter),
        'semantic_duplicate_rows': sum(v - 1 for v in dup_counter.values() if v > 1),
        'blocker_counts': dict(blocker_counts.most_common()),
        'provider_hit_counts': dict(provider_hit_counts.most_common()),
        'providers_indexed': {k: len(v) for k, v in indexes.items()},
        'worst_gaps': [x for x in audited if x['missing']][:80],
        'rows': audited,
    }
    write(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
