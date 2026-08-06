from __future__ import annotations

"""Apply HARIZON A-tier-only v3 cleanup fixes.

This helper is intentionally idempotent and safe to run locally after copying the
archive over the repository.  It does not relax publication guards.  It only:
- aligns workflow defaults with A-tier-only public publication;
- keeps B-tier as watchlist-only;
- removes obvious noisy rescue rows before fallback review;
- prevents A-cover promotion from feeding quarter totals / negative value rows;
- makes Telegram diagnostics say that B-tier is watchlist-only.
"""

import json
import re
from pathlib import Path

ROOT = Path('.').resolve()
REPORT = ROOT / '.data' / 'exports' / 'latest-a-tier-only-v3-local-patch.json'


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _replace_once(text: str, old: str, new: str) -> tuple[str, bool]:
    if old in text:
        return text.replace(old, new, 1), True
    if new in text:
        return text, False
    return text, False


def _set_yaml_env(text: str, key: str, value: str, *, after_key: str | None = None) -> tuple[str, str]:
    pattern = re.compile(rf'^(\s*{re.escape(key)}:\s*)"[^"]*"\s*$', re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(rf'\1"{value}"', text), 'updated'
    if after_key:
        after = re.compile(rf'^(\s*){re.escape(after_key)}:\s*"[^"]*"\s*$', re.MULTILINE)
        m = after.search(text)
        if m:
            indent = m.group(1)
            insert_at = m.end()
            return text[:insert_at] + f'\n{indent}{key}: "{value}"' + text[insert_at:], 'inserted'
    return text, 'missing'


def patch_workflow() -> dict[str, object]:
    path = ROOT / '.github' / 'workflows' / 'run-bot.yml'
    if not path.exists():
        return {'path': str(path), 'status': 'missing'}
    text = _read(path)
    before = text
    changes: dict[str, str] = {}
    env_values = {
        'PUBLISH_ALLOW_B_TIER': 'false',
        'PUBLISH_B_TIER_WATCH_ONLY': 'true',
        'PUBLISH_COVERAGE_TIER_MODE': 'a_only_publish_b_watchlist',
        'MIN_BOOKS_PUBLISH': '2',
        'PUBLISH_MIN_BOOKS': '2',
        'MIN_SOURCES_PUBLISH': '2',
        'PUBLISH_MIN_ODDS_SOURCES': '2',
        'PUBLISH_MIN_CONTEXT_SOURCES': '2',
        'MIN_CONTEXT_SOURCES_PUBLISH': '2',
        'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '2',
        'CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES': '2',
        'CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES': '2',
        'CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES': 'true',
        'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES': '2',
        'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKMAKERS': '2',
        'CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES': '2',
        'CONTROLLED_FALLBACK_TIER_B_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES': 'true',
        'CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B': 'false',
        'CONTROLLED_FALLBACK_TIER_B_WATCH_ONLY': 'true',
        'CONTROLLED_FALLBACK_TIER_B_PUBLISH_ENABLED': 'false',
        # Prepare A-cover candidates earlier, but keep final publish window/line checks.
        'PROMOTE_A_COVER_ONLY_PUBLISH_WINDOW': 'false',
        'PROMOTE_A_COVER_PRUNE_RESCUE_TO_PUBLISH_WINDOW': 'true',
        'PROMOTE_A_COVER_REJECT_NOISE_RESCUE': 'true',
        'PROMOTE_A_COVER_VALUE_CANDIDATE_LIMIT': '18',
    }
    anchor = 'PUBLISH_DRY_RUN'
    for key, value in env_values.items():
        text, status = _set_yaml_env(text, key, value, after_key=anchor)
        changes[key] = status
        anchor = key
    if text != before:
        _write(path, text)
    return {'path': str(path), 'status': 'ok', 'changed': text != before, 'env_changes': changes}


def patch_publication_policy() -> dict[str, object]:
    path = ROOT / 'scripts' / 'apply_publication_family_policy.py'
    if not path.exists():
        return {'path': str(path), 'status': 'missing'}
    text = _read(path)
    before = text
    additions = {
        'PROMOTE_A_COVER_ONLY_PUBLISH_WINDOW': 'false',
        'PROMOTE_A_COVER_PRUNE_RESCUE_TO_PUBLISH_WINDOW': 'true',
        'PROMOTE_A_COVER_REJECT_NOISE_RESCUE': 'true',
        'PROMOTE_A_COVER_VALUE_CANDIDATE_LIMIT': '18',
        'PROMOTE_B_COVER_MIN_BOOKS': '2',
    }
    marker = "    'MARKET_DERIVED_ALLOW_FIRST_SNAPSHOT_CANDIDATES': 'false',\n"
    if marker in text:
        block = ''.join(f"    '{k}': '{v}',\n" for k, v in additions.items() if f"'{k}'" not in text)
        if block:
            text = text.replace(marker, block + marker, 1)
    if text != before:
        _write(path, text)
    return {'path': str(path), 'status': 'ok', 'changed': text != before, 'added_env': [k for k in additions if f"'{k}'" in text]}


PROMOTION_HELPERS = r'''

def _float_or_none_local(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        return float(str(value).replace(',', '.'))
    except Exception:
        return None


def _promotion_point_value(row: dict[str, Any]) -> float | None:
    for key in ('point', 'line', 'total', 'handicap'):
        value = row.get(key)
        if value not in (None, ''):
            return _float_or_none_local(value)
    selection = str(row.get('selection') or '')
    m = __import__('re').search(r'(?<!\d)(\d+(?:[\.,]\d+)?)(?!\d)', selection)
    return _float_or_none_local(m.group(1)) if m else None


def _promotion_public_total_line(point: float | None) -> bool:
    if point is None or point <= 0:
        return False
    doubled = point * 2.0
    return abs(doubled - round(doubled)) <= 1e-6


def _metric_float(row: dict[str, Any], *keys: str) -> float | None:
    nested = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    diag = row.get('diagnostics') if isinstance(row.get('diagnostics'), dict) else {}
    promo = diag.get('promotion') if isinstance(diag.get('promotion'), dict) else {}
    source_summary = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
    for container in (row, nested, promo, source_summary):
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value not in (None, ''):
                parsed = _float_or_none_local(value)
                if parsed is not None:
                    return parsed
    return None


def _promotion_noise_reason(row: dict[str, Any]) -> str | None:
    if not _env_bool('PROMOTE_A_COVER_REJECT_NOISE_RESCUE', True):
        return None
    fam = str(row.get('family') or row.get('market_family') or '').strip().lower()
    selection = str(row.get('selection') or row.get('selection_key') or '').strip().lower()
    if fam == 'totals' or 'тотал' in selection or 'меньше' in selection or 'больше' in selection:
        point = _promotion_point_value(row)
        if point is not None and not _promotion_public_total_line(point):
            return 'dropped_non_public_total_line'
    ev = _metric_float(row, 'canonical_ev_pct', 'ev_pct', 'ev')
    edge = _metric_float(row, 'canonical_edge_pp', 'edge_pct', 'edge_pp', 'edge')
    if ev is not None and ev <= 0:
        return 'dropped_non_positive_value'
    if edge is not None and edge <= 0:
        return 'dropped_non_positive_value'
    deviation = _metric_float(row, 'selected_vs_median_deviation_pct')
    max_deviation = _as_float(
        os.getenv('PROMOTE_A_COVER_MAX_SELECTED_MEDIAN_DEVIATION_PCT')
        or os.getenv('PROMOTE_B_COVER_MAX_SELECTED_MEDIAN_DEVIATION_PCT')
        or os.getenv('CONTROLLED_FALLBACK_TIER_B_MAX_BOOKMAKER_MEDIAN_DEVIATION_PCT'),
        8.0,
    )
    if deviation is not None and deviation > max_deviation:
        return 'dropped_price_outlier'
    return None
'''


def patch_a_cover_promotion() -> dict[str, object]:
    path = ROOT / 'scripts' / 'promote_a_cover_value_candidates.py'
    if not path.exists():
        return {'path': str(path), 'status': 'missing'}
    text = _read(path)
    before = text
    replacements: list[str] = []

    if '_promotion_noise_reason' not in text:
        marker = '\ndef _clear_stale_artifact_rescue() -> bool:\n'
        if marker in text:
            text = text.replace(marker, PROMOTION_HELPERS + marker, 1)
            replacements.append('insert_noise_helpers')

    old_load = """def _load_existing_rescue(now: datetime) -> tuple[list[dict[str, Any]], dict[str, int]]:\n    rows = bcover.rescue_rows_payload()\n    stats = {'loaded': len(rows), 'kept': 0, 'dropped_outside_window': 0}\n    if not _env_bool('PROMOTE_A_COVER_PRUNE_RESCUE_TO_PUBLISH_WINDOW', True):\n        stats['kept'] = len(rows)\n        return rows, stats\n    kept: list[dict[str, Any]] = []\n    for row in rows:\n        if not isinstance(row, dict):\n            continue\n        if _row_in_fallback_window(row, now):\n            kept.append(row)\n        else:\n            stats['dropped_outside_window'] += 1\n    stats['kept'] = len(kept)\n    return kept, stats\n"""
    new_load = """def _load_existing_rescue(now: datetime) -> tuple[list[dict[str, Any]], dict[str, int]]:\n    rows = bcover.rescue_rows_payload()\n    stats = {\n        'loaded': len(rows),\n        'kept': 0,\n        'dropped_outside_window': 0,\n        'dropped_non_public_total_line': 0,\n        'dropped_non_positive_value': 0,\n        'dropped_price_outlier': 0,\n    }\n    prune_to_window = _env_bool('PROMOTE_A_COVER_PRUNE_RESCUE_TO_PUBLISH_WINDOW', True)\n    kept: list[dict[str, Any]] = []\n    for row in rows:\n        if not isinstance(row, dict):\n            continue\n        noise_reason = _promotion_noise_reason(row)\n        if noise_reason:\n            stats[noise_reason] = int(stats.get(noise_reason, 0)) + 1\n            continue\n        if prune_to_window and not _row_in_fallback_window(row, now):\n            stats['dropped_outside_window'] += 1\n            continue\n        kept.append(row)\n    stats['kept'] = len(kept)\n    return kept, stats\n"""
    text, changed = _replace_once(text, old_load, new_load)
    if changed:
        replacements.append('filter_existing_rescue_noise')

    old_tune = """    if ctx_sources:\n        cand['confirmation_sources'] = ctx_sources\n    cand['_candidate_source'] = 'a_cover_market_promotion'\n"""
    new_tune = """    if ctx_sources:\n        cand['confirmation_sources'] = ctx_sources\n    if cand.get('ev_pct') not in (None, '') and cand.get('canonical_ev_pct') in (None, ''):\n        cand['canonical_ev_pct'] = cand.get('ev_pct')\n    if cand.get('edge_pct') not in (None, '') and cand.get('canonical_edge_pp') in (None, ''):\n        cand['canonical_edge_pp'] = cand.get('edge_pct')\n    cand['_candidate_source'] = 'a_cover_market_promotion'\n"""
    text, changed = _replace_once(text, old_tune, new_tune)
    if changed:
        replacements.append('copy_canonical_value_metrics')

    old_loop = """            cand = _tune_candidate(cand, row)\n            sig = bcover.candidate_signature(cand)\n"""
    new_loop = """            cand = _tune_candidate(cand, row)\n            noise_reason = _promotion_noise_reason(cand)\n            if noise_reason:\n                reasons['promotion_skip_' + noise_reason.removeprefix('dropped_')] += 1\n                continue\n            sig = bcover.candidate_signature(cand)\n"""
    text, changed = _replace_once(text, old_loop, new_loop)
    if changed:
        replacements.append('skip_new_noise_candidates')

    if text != before:
        _write(path, text)
    return {'path': str(path), 'status': 'ok', 'changed': text != before, 'replacements': replacements}


def patch_report_v13() -> dict[str, object]:
    path = ROOT / 'scripts' / 'send_harizon_telegram_run_report_v13.py'
    if not path.exists():
        return {'path': str(path), 'status': 'missing'}
    text = _read(path)
    before = text
    replacements: list[str] = []

    old_render = """def render(payload: dict[str, Any]) -> str:\n    text = _base_render(payload)\n    lines = _extra_lines(payload, text)\n"""
    new_render = """def render(payload: dict[str, Any]) -> str:\n    text = _base_render(payload)\n    text = re.sub(\n        r'B-tier watchlist-only = 1\\+ линия/odds-source \\+ 1\\+ букмекер/ценовое подтверждение',\n        'B-tier watchlist-only = 1+ линия/odds-source + 2+ букмекер/ценовое подтверждение',\n        text,\n    )\n    lines = _extra_lines(payload, text)\n"""
    text, changed = _replace_once(text, old_render, new_render)
    if changed:
        replacements.append('fix_b_tier_watchlist_bookmaker_text')

    old_promo_line = """            f\"• A-cover promotion: status {status}; promoted {_as_int(promotion.get('promoted_count'))}; active {_as_int(promotion.get('active_a_cover_rows'))}; in-window {_as_int(promotion.get('in_publish_window_a_cover_rows'))}; considered {_as_int(promotion.get('considered_a_cover_rows'))}; rescue kept {_as_int(existing_stats.get('kept'))}/{_as_int(existing_stats.get('loaded'))}; dropped stale {_as_int(existing_stats.get('dropped_outside_window'))}; top skips {_top_counter_items(promotion.get('reason_counts'), 5)}{suffix}.\"\n"""
    new_promo_line = """            f\"• A-cover promotion: status {status}; promoted {_as_int(promotion.get('promoted_count'))}; active {_as_int(promotion.get('active_a_cover_rows'))}; prep-window {_as_int(promotion.get('in_publish_window_a_cover_rows'))}; considered {_as_int(promotion.get('considered_a_cover_rows'))}; rescue kept {_as_int(existing_stats.get('kept'))}/{_as_int(existing_stats.get('loaded'))}; dropped stale {_as_int(existing_stats.get('dropped_outside_window'))}; dropped noise {_as_int(existing_stats.get('dropped_non_public_total_line')) + _as_int(existing_stats.get('dropped_non_positive_value')) + _as_int(existing_stats.get('dropped_price_outlier'))}; top skips {_top_counter_items(promotion.get('reason_counts'), 5)}{suffix}.\"\n"""
    text, changed = _replace_once(text, old_promo_line, new_promo_line)
    if changed:
        replacements.append('show_promotion_noise_cleanup')

    if text != before:
        _write(path, text)
    return {'path': str(path), 'status': 'ok', 'changed': text != before, 'replacements': replacements}


def patch_report_v12() -> dict[str, object]:
    path = ROOT / 'scripts' / 'send_harizon_telegram_run_report_v12.py'
    if not path.exists():
        return {'path': str(path), 'status': 'missing'}
    text = _read(path)
    before = text
    text = text.replace(
        "f'Контракт публикации сейчас: A-tier = 2 odds-source + 2 букмекера + 2 контекста; B-tier = 1 odds-source + {b_books} букмекер + {b_ctx} контекст;'",
        "f'Контракт публикации сейчас: A-tier = public 2 odds-source + 2 букмекера + 2 контекста; B-tier = watchlist-only 1 odds-source + {b_books} букмекер + {b_ctx} контекст;'",
    )
    if text != before:
        _write(path, text)
    return {'path': str(path), 'status': 'ok', 'changed': text != before}


def main() -> int:
    results = {
        'workflow': patch_workflow(),
        'publication_policy': patch_publication_policy(),
        'a_cover_promotion': patch_a_cover_promotion(),
        'telegram_report_v13': patch_report_v13(),
        'telegram_report_v12': patch_report_v12(),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
