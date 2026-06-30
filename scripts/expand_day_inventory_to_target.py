from __future__ import annotations

"""Expand and preserve HARIZON day inventory up to the configured target.

This script is intentionally API-free. It prevents the daily inventory from
being limited by the latest narrow runtime window by merging every already-known
fixture row for the frozen day from cache/export artifacts back into
.data/day_inventory/current.json/latest.json/today.json.

It never invents publishable coverage. It only restores/keeps fixture rows that
already exist somewhere in repository runtime artifacts, so odds/context guards
remain untouched.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path('.').resolve()
DAY_DIR = ROOT / '.data' / 'day_inventory'
CACHE_DAY_DIR = ROOT / '.data' / 'cache' / 'day_inventory'
EXPORT_DIR = ROOT / '.data' / 'exports'
REPORT_PATH = EXPORT_DIR / 'latest-day-inventory-target-expand.json'
HIGHWATER_NAMES = ('best-day-inventory-highwater.json', 'highwater.json', 'largest.json')
CONFLICT_MARKERS = ('<<<<<<<', '=======', '>>>>>>>')


def env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == '':
            return max(minimum, int(default))
        return max(minimum, int(float(str(raw))))
    except Exception:
        return max(minimum, int(default))


def app_time_zone():
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return UTC


def local_date_from_dt(dt: datetime) -> str:
    return dt.astimezone(app_time_zone()).date().isoformat()


def target_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    if explicit:
        return explicit[:10]
    return datetime.now(app_time_zone()).date().isoformat()


def load_json(path: Path, default: Any = None) -> Any:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return default
        text = path.read_text(encoding='utf-8', errors='replace')
        if any(marker in text for marker in CONFLICT_MARKERS):
            return default
        return json.loads(text)
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            text = str(value).strip()
            if text.endswith('Z'):
                text = text[:-1] + '+00:00'
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def row_date(row: dict[str, Any]) -> str:
    for key in ('kickoff_utc', 'commence_time', 'start_time', 'kickoff', 'date'):
        value = row.get(key)
        if not value:
            continue
        if key == 'date' and re.match(r'^20\d{2}-\d{2}-\d{2}$', str(value)[:10]):
            return str(value)[:10]
        dt = parse_dt(value)
        if dt:
            return local_date_from_dt(dt)
    for key in ('match_key', 'canonical_match_id', 'event_key'):
        m = re.search(r'(20\d{2}-\d{2}-\d{2})', str(row.get(key) or ''))
        if m:
            return m.group(1)
    return ''


def row_key(row: dict[str, Any]) -> str:
    for key in ('canonical_match_id', 'match_key', 'event_key', 'id'):
        value = str(row.get(key) or '').strip()
        if value:
            return norm(value)
    home = norm(row.get('home_team') or row.get('home') or row.get('home_name') or row.get('team_home'))
    away = norm(row.get('away_team') or row.get('away') or row.get('away_name') or row.get('team_away'))
    d = row_date(row)
    league = norm(row.get('league_name') or row.get('league') or row.get('competition'))
    return '|'.join(x for x in (d, league, home, away) if x)


def kickoff_sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
    dt = parse_dt(row.get('kickoff_utc') or row.get('commence_time') or row.get('start_time') or row.get('kickoff'))
    ts = int(dt.timestamp()) if dt else 9_999_999_999
    return (ts, norm(row.get('league_name') or row.get('league')), row_key(row))


def score_row(row: dict[str, Any]) -> int:
    score = 0
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        if container.get('odds') or container.get('has_odds') or container.get('with_odds'):
            score += 20
        if container.get('context') or container.get('has_context') or container.get('with_context'):
            score += 20
        for key in ('books', 'bookmakers', 'price_confirmations', 'odds_sources', 'context_sources'):
            value = container.get(key)
            if isinstance(value, (list, tuple, set, dict)):
                score += min(10, len(value))
            elif value not in (None, '', False):
                score += 1
    for key in ('home_team', 'away_team', 'commence_time', 'kickoff_utc', 'league_name'):
        if row.get(key):
            score += 1
    return score


def merge_row(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    # Keep the row with richer coverage as base, then fill missing fields from the other.
    if score_row(new) > score_row(old):
        base, other = dict(new), old
    else:
        base, other = dict(old), new
    for key, value in other.items():
        if key not in base or base.get(key) in (None, '', [], {}):
            base[key] = value
        elif isinstance(base.get(key), dict) and isinstance(value, dict):
            merged = dict(value)
            merged.update(base[key])
            base[key] = merged
        elif isinstance(base.get(key), list) and isinstance(value, list):
            seen = {json.dumps(x, sort_keys=True, ensure_ascii=False, default=str) for x in base[key]}
            for item in value:
                sig = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
                if sig not in seen:
                    base[key].append(item)
                    seen.add(sig)
    return base


def rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ('matches', 'fixtures', 'events', 'rows', 'items'):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend([x for x in value if isinstance(x, dict)])
    # Some artifacts are keyed by match id.
    for key in ('by_match', 'matches_by_key'):
        value = payload.get(key)
        if isinstance(value, dict):
            for k, row in value.items():
                if isinstance(row, dict):
                    clone = dict(row)
                    clone.setdefault('match_key', k)
                    out.append(clone)
    return out


def highwater_paths(day: str) -> list[Path]:
    return [
        *(DAY_DIR / name for name in HIGHWATER_NAMES),
        *(CACHE_DAY_DIR / name for name in HIGHWATER_NAMES),
        DAY_DIR / f'{day}-highwater.json',
        CACHE_DAY_DIR / f'{day}-highwater.json',
        ROOT / '.data' / 'inventory_guard' / 'best-day-inventory.json',
    ]


def candidate_paths(day: str) -> list[Path]:
    explicit = [
        DAY_DIR / f'{day}.json', DAY_DIR / 'current.json', DAY_DIR / 'latest.json', DAY_DIR / 'today.json',
        CACHE_DAY_DIR / f'{day}.json', CACHE_DAY_DIR / 'current.json', CACHE_DAY_DIR / 'latest.json', CACHE_DAY_DIR / 'today.json',
        *highwater_paths(day),
        EXPORT_DIR / 'latest-day-inventory-cumulative-coverage.json',
        EXPORT_DIR / 'latest-day-inventory-coverage-truth.json',
        EXPORT_DIR / 'latest-run-summary.json',
        ROOT / '.logs' / 'debug-last-run.json',
    ]
    # Shallow recursive scan of likely JSON artifacts. This is deliberately
    # capped to avoid walking huge historical exports.
    for root in (DAY_DIR, CACHE_DAY_DIR, EXPORT_DIR, ROOT / 'artifacts' / 'run-bot'):
        if root.exists():
            explicit.extend(sorted(root.glob('*.json'))[:200])
            explicit.extend(sorted(root.glob('*/*.json'))[:200])
    seen: set[Path] = set()
    out: list[Path] = []
    for path in explicit:
        p = path.resolve()
        if p not in seen:
            seen.add(p)
            out.append(path)
    return out


def collect_rows(day: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    parse_errors: list[str] = []
    for path in candidate_paths(day):
        payload = load_json(path, None)
        if payload is None:
            if path.exists():
                parse_errors.append(str(path))
            continue
        rows = rows_from_payload(payload)
        accepted = 0
        for row in rows:
            d = row_date(row)
            if d and d != day:
                continue
            key = row_key(row)
            if not key:
                continue
            if key in by_key:
                by_key[key] = merge_row(by_key[key], row)
            else:
                by_key[key] = dict(row)
            accepted += 1
        if accepted:
            source_counts[str(path)] = accepted
    rows = sorted(by_key.values(), key=kickoff_sort_key)
    return rows, {'source_counts': source_counts, 'parse_errors': parse_errors[:30]}


def best_existing_payload(day: str) -> dict[str, Any]:
    best: dict[str, Any] = {'date_local': day, 'matches': [], 'counts': {}}
    best_count = -1
    for path in candidate_paths(day):
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            continue
        raw_rows = payload.get('matches') if isinstance(payload.get('matches'), list) else []
        rows = [r for r in raw_rows if isinstance(r, dict) and (not row_date(r) or row_date(r) == day)]
        count = len(rows)
        # Only actual rows count for no-shrink. Summary-only artifacts may carry
        # counts.matches_total, but they cannot be copied back into inventory.
        if count > best_count:
            best_count = count
            best = dict(payload)
            best['matches'] = rows
    if not isinstance(best.get('counts'), dict):
        best['counts'] = {}
    best['date_local'] = day
    return best


def write_aliases(payload: dict[str, Any], day: str) -> list[str]:
    changed: list[str] = []
    DAY_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DAY_DIR.mkdir(parents=True, exist_ok=True)
    for path in (
        DAY_DIR / f'{day}.json', DAY_DIR / 'current.json', DAY_DIR / 'latest.json', DAY_DIR / 'today.json',
        CACHE_DAY_DIR / f'{day}.json', CACHE_DAY_DIR / 'today.json', CACHE_DAY_DIR / 'current.json', CACHE_DAY_DIR / 'latest.json',
    ):
        write_json(path, payload)
        changed.append(str(path))
    return changed


def write_highwater(payload: dict[str, Any], day: str) -> list[str]:
    if not isinstance(payload.get('matches'), list) or not payload['matches']:
        return []
    clone = dict(payload)
    clone['highwater_updated_at_utc'] = datetime.now(UTC).isoformat()
    changed: list[str] = []
    for path in highwater_paths(day):
        write_json(path, clone)
        changed.append(str(path))
    return changed


def main() -> int:
    day = target_date()
    target = env_int('DAY_INVENTORY_TARGET_SIZE', env_int('DAY_INVENTORY_MAX_MATCHES', 300, 1), 1)
    rows, diagnostics = collect_rows(day)
    existing_payload = best_existing_payload(day)
    existing_rows = existing_payload.get('matches') if isinstance(existing_payload.get('matches'), list) else []
    before = len(existing_rows)

    # Keep all rows up to target; if fewer than target are known, do not invent rows.
    selected_from_collected = rows[:target] if target > 0 else rows
    if len(selected_from_collected) >= before:
        selected = selected_from_collected
        payload = dict(existing_payload)
        payload['matches'] = selected
    else:
        # Never shrink: keep best existing payload if current artifact scan found
        # fewer actual rows than a persisted high-watermark or alias.
        selected = existing_rows
        payload = dict(existing_payload)
        payload['matches'] = selected

    payload['date_local'] = day
    payload['target_matches'] = target
    counts = payload.setdefault('counts', {})
    counts['matches_total'] = len(selected)
    counts['matches_after_target_expand'] = len(selected)
    counts['target_matches'] = target
    counts['target_shortfall'] = max(0, target - len(selected))
    counts['target_expand_rows_collected'] = len(rows)
    counts['target_expand_existing_before'] = before
    counts['target_expand_no_shrink_applied'] = len(selected_from_collected) < before
    payload['target_expand_updated_at_utc'] = datetime.now(UTC).isoformat()
    payload['target_expand_status'] = 'ok_target_met' if len(selected) >= target else 'partial_known_rows_only'

    changed_paths = write_aliases(payload, day)
    highwater_paths_written = write_highwater(payload, day)

    report = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'target_date': day,
        'target': target,
        'existing_before': before,
        'rows_collected': len(rows),
        'selected_from_collected': len(selected_from_collected),
        'matches_after': len(selected),
        'target_shortfall': max(0, target - len(selected)),
        'target_timezone': str(app_time_zone()),
        'status': payload['target_expand_status'],
        'no_shrink_applied': len(selected_from_collected) < before,
        'changed_paths': changed_paths,
        'highwater_paths': highwater_paths_written,
        **diagnostics,
    }
    write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
