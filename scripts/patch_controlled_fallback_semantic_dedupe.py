from __future__ import annotations

from pathlib import Path

PATH = Path('scripts/publish_controlled_fallback.py')

DEDUPE_START = 'def dedupe_key(candidate: dict[str, Any]) -> str:\n'
DEDUPE_END = '\ndef load_sent_index() -> dict[str, Any]:\n'
DUP_START = 'def duplicate_reason(candidate: dict[str, Any], sent_index: dict[str, Any]) -> str | None:\n'
DUP_END = '\ndef candidate_metrics(candidate: dict[str, Any]) -> dict[str, Any]:\n'

DEDUPE_BLOCK = r'''def _dedupe_norm_text(value: Any) -> str:
    text = str(value or '').strip().lower()
    text = text.replace('ё', 'е')
    replacements = {
        'обе забьют': 'btts',
        'обе команды забьют': 'btts',
        'да': 'yes',
        'нет': 'no',
        'больше': 'over',
        'меньше': 'under',
        'тотал': 'total',
        'фора': 'spread',
        'ничья': 'draw',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r'[^a-z0-9а-я]+', '', text)


def _dedupe_kickoff_bucket(candidate: dict[str, Any]) -> str:
    kickoff = parse_dt(candidate.get('commence_time') or candidate.get('start_time') or candidate.get('kickoff'))
    if kickoff is None:
        return str(candidate.get('commence_time') or candidate.get('start_time') or candidate.get('kickoff') or '')[:16]
    return kickoff.astimezone(UTC).strftime('%Y-%m-%dT%H:%M')


def _semantic_match_raw(candidate: dict[str, Any]) -> str:
    home = _dedupe_norm_text(candidate.get('home_team') or candidate.get('home') or '')
    away = _dedupe_norm_text(candidate.get('away_team') or candidate.get('away') or '')
    kickoff = _dedupe_kickoff_bucket(candidate)
    if home and away and kickoff:
        return '|'.join([home, away, kickoff])
    return str(candidate.get('match_key') or '').strip().lower()


def _semantic_dedupe_raw(candidate: dict[str, Any]) -> str:
    return '|'.join([
        _semantic_match_raw(candidate),
        _dedupe_norm_text(candidate.get('family') or ''),
        _dedupe_norm_text(candidate.get('selection') or ''),
        _dedupe_norm_text(candidate.get('selection_key') or ''),
        str(candidate.get('point') or '').strip(),
        _dedupe_norm_text(candidate.get('team_side') or ''),
    ])


def _legacy_dedupe_raw(candidate: dict[str, Any]) -> str:
    return '|'.join([
        str(candidate.get('match_key') or ''),
        str(candidate.get('family') or '').lower(),
        str(candidate.get('selection') or '').lower(),
        str(candidate.get('selection_key') or '').lower(),
        str(candidate.get('point') or ''),
        str(candidate.get('team_side') or '').lower(),
    ])


def all_match_dedupe_keys(candidate: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for raw in (_semantic_match_raw(candidate), str(candidate.get('match_key') or '').strip().lower()):
        if raw.strip('|'):
            value = hashlib.sha1(raw.encode('utf-8')).hexdigest()
            if value not in keys:
                keys.append(value)
    return keys


def all_dedupe_keys(candidate: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for raw in (_semantic_dedupe_raw(candidate), _legacy_dedupe_raw(candidate)):
        if raw.strip('|'):
            value = hashlib.sha1(raw.encode('utf-8')).hexdigest()
            if value not in keys:
                keys.append(value)
    return keys


def dedupe_key(candidate: dict[str, Any]) -> str:
    keys = all_dedupe_keys(candidate)
    return keys[0] if keys else hashlib.sha1(b'').hexdigest()
'''

DUP_BLOCK = r'''def duplicate_reason(candidate: dict[str, Any], sent_index: dict[str, Any]) -> str | None:
    keys = set(all_dedupe_keys(candidate))
    match_keys = set(all_match_dedupe_keys(candidate))
    for key in keys:
        if key in sent_index:
            return 'duplicate_fallback_sent_index'
    for row in sent_index.values():
        if not isinstance(row, dict):
            continue
        if keys & set(all_dedupe_keys(row)):
            return 'duplicate_fallback_sent_index_semantic'
        if match_keys & set(all_match_dedupe_keys(row)):
            return 'duplicate_fallback_same_match_sent_index'

    state = load_json('.data/state.json', {})
    if not isinstance(state, dict):
        return None
    collections: list[str] = []
    if env_bool('CONTROLLED_FALLBACK_DEDUPE_STATE_BETS', True):
        collections.append('bets')
    if env_bool('CONTROLLED_FALLBACK_DEDUPE_STATE_PUBLISHED', True):
        collections.append('published_candidates')
    if env_bool('CONTROLLED_FALLBACK_DEDUPE_STATE_SHADOW', False):
        collections.append('shadow_bets')
    for collection in collections:
        rows = state.get(collection) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if keys & set(all_dedupe_keys(row)):
                return f'duplicate_state:{collection}'
            if match_keys & set(all_match_dedupe_keys(row)):
                return f'duplicate_same_match_state:{collection}'
    return None
'''


def replace_block(src: str, start: str, end: str, replacement: str) -> tuple[str, bool]:
    i = src.find(start)
    if i < 0:
        return src, False
    j = src.find(end, i)
    if j < 0:
        return src, False
    return src[:i] + replacement + src[j:], True


def main() -> int:
    if not PATH.exists():
        print('skip: missing target')
        return 0
    src = PATH.read_text(encoding='utf-8')
    original = src
    if 'def all_match_dedupe_keys(' not in src:
        src, ok = replace_block(src, DEDUPE_START, DEDUPE_END, DEDUPE_BLOCK)
        if not ok:
            print('warn: dedupe_key block not found')
    if 'duplicate_fallback_same_match_sent_index' not in src:
        src, ok = replace_block(src, DUP_START, DUP_END, DUP_BLOCK)
        if not ok:
            print('warn: duplicate_reason block not found')
    if src != original:
        PATH.write_text(src, encoding='utf-8')
        print('patched: same-match controlled-fallback dedupe')
    else:
        print('already patched or no changes')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
