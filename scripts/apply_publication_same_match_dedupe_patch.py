from __future__ import annotations

from pathlib import Path

ROOT = Path('.').resolve()
TARGET = ROOT / 'scripts' / 'publish_controlled_fallback.py'
PATCH_VERSION = 'v2-same-match-dedupe-future-safe'
MARKER = 'PUBLICATION_SAME_MATCH_DEDUPE_PATCH_VERSION = "v2-same-match-dedupe-future-safe"'


def insert_marker_future_safe(text: str) -> str:
    if 'PUBLICATION_SAME_MATCH_DEDUPE_PATCH_VERSION' in text:
        return text
    future = 'from __future__ import annotations\n'
    if future in text:
        return text.replace(future, future + MARKER + '\n', 1)
    return MARKER + '\n' + text


def main() -> int:
    text = TARGET.read_text(encoding='utf-8')
    if 'PUBLICATION_SAME_MATCH_DEDUPE_PATCH_VERSION' in text:
        print({'patch': PATCH_VERSION, 'changed': False, 'reason': 'already_applied'})
        return 0
    changed = False

    old_dup = '''def duplicate_reason(candidate: dict[str, Any], sent_index: dict[str, Any]) -> str | None:
    key = dedupe_key(candidate)
    if key in sent_index:
        return "duplicate_fallback_sent_index"
    state = load_json(".data/state.json", {})
    if not isinstance(state, dict):
        return None
    collections: list[str] = []
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_BETS", True):
        collections.append("bets")
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_PUBLISHED", True):
        collections.append("published_candidates")
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_SHADOW", False):
        collections.append("shadow_bets")
    for collection in collections:
        rows = state.get(collection) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and dedupe_key(row) == key:
                return f"duplicate_state:{collection}"
    return None
'''
    new_dup = '''def publication_match_key(candidate: dict[str, Any]) -> str:
    match_key = str(candidate.get("match_key") or "").strip().lower()
    if match_key:
        return match_key
    raw = "|".join([
        str(candidate.get("home_team") or candidate.get("home") or "").strip().lower(),
        str(candidate.get("away_team") or candidate.get("away") or "").strip().lower(),
        str(candidate.get("commence_time") or candidate.get("start_time") or candidate.get("kickoff") or "").strip()[:16],
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() if raw.strip("|") else ""


def same_match_duplicate_reason(candidate: dict[str, Any], rows: list[dict[str, Any]], source: str) -> str | None:
    if not env_bool("CONTROLLED_FALLBACK_DEDUPE_SAME_MATCH", True):
        return None
    target_match = publication_match_key(candidate)
    if not target_match:
        return None
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, env_int("CONTROLLED_FALLBACK_SAME_MATCH_DEDUPE_HOURS", 36)))
    for row in rows:
        if not isinstance(row, dict):
            continue
        if publication_match_key(row) != target_match:
            continue
        ts = parse_dt(row.get("sent_at") or row.get("published_at") or row.get("created_at") or row.get("placed_at") or row.get("timestamp"))
        if ts is None or ts >= cutoff:
            return f"duplicate_same_match:{source}"
    return None


def duplicate_reason(candidate: dict[str, Any], sent_index: dict[str, Any]) -> str | None:
    key = dedupe_key(candidate)
    if key in sent_index:
        return "duplicate_fallback_sent_index"
    sent_rows = [row for row in sent_index.values() if isinstance(row, dict)]
    same_sent = same_match_duplicate_reason(candidate, sent_rows, "fallback_sent_index")
    if same_sent:
        return same_sent
    state = load_json(".data/state.json", {})
    if not isinstance(state, dict):
        return None
    collections: list[str] = []
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_BETS", True):
        collections.append("bets")
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_PUBLISHED", True):
        collections.append("published_candidates")
    if env_bool("CONTROLLED_FALLBACK_DEDUPE_STATE_SHADOW", False):
        collections.append("shadow_bets")
    for collection in collections:
        rows = state.get(collection) or []
        if not isinstance(rows, list):
            continue
        same_state = same_match_duplicate_reason(candidate, [row for row in rows if isinstance(row, dict)], f"state:{collection}")
        if same_state:
            return same_state
        for row in rows:
            if isinstance(row, dict) and dedupe_key(row) == key:
                return f"duplicate_state:{collection}"
    return None
'''
    if old_dup in text:
        text = text.replace(old_dup, new_dup, 1)
        changed = True

    if changed:
        text = insert_marker_future_safe(text)
        TARGET.write_text(text, encoding='utf-8')
    print({'patch': PATCH_VERSION, 'changed': changed, 'target': str(TARGET)})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
