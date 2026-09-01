from __future__ import annotations

"""HARIZON Telegram report v12.

Standalone wrapper over v9. Diagnostic-only: no picks are published and no guards
are changed. It shows effective blockers after the daily cap and explains why
A-tier is absent when coverage exists but no raw publishable candidate survives.
"""

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any

V9_PATH = Path(__file__).with_name('send_harizon_telegram_run_report_v9.py')
EXPORT_DIR = Path('.data/exports')
STATUS_PATH = EXPORT_DIR / 'latest-harizon-telegram-run-report-v12-status.json'


def _load_v9() -> Any:
    spec = importlib.util.spec_from_file_location('harizon_report_v9', V9_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load {V9_PATH}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v9 = _load_v9()
_base_build_payload = v9.build_payload
_base_render = v9.render


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.stat().st_size > 0:
            value = json.loads(path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
    except Exception:
        pass
    return {}


def _as_int(value: Any) -> int:
    try:
        if value in (None, ''):
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value).replace(',', '.')))
    except Exception:
        return 0


def _as_float(value: Any) -> float:
    try:
        if value in (None, ''):
            return 0.0
        return float(str(value).replace(',', '.'))
    except Exception:
        return 0.0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _write_status(payload: dict[str, Any]) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _run_shadow_diagnostics() -> None:
    try:
        from scripts.build_controlled_fallback_shadow_ranking import main as shadow_main
        shadow_main()
    except Exception:
        pass


def _policy_tier(policy: dict[str, Any], tier: str) -> dict[str, Any]:
    contract = policy.get('contract') if isinstance(policy.get('contract'), dict) else {}
    value = contract.get(tier) if isinstance(contract.get(tier), dict) else {}
    return value


def _policy_env(policy: dict[str, Any], key: str) -> Any:
    env = policy.get('env') if isinstance(policy.get('env'), dict) else {}
    return env.get(key)


def build_payload() -> dict[str, Any]:
    _run_shadow_diagnostics()
    payload = _base_build_payload()
    payload['version'] = 'harizon-telegram-report-v12-effective-blockers'
    diag = payload.setdefault('diagnostics', {})
    policy = _load_json(EXPORT_DIR / 'latest-ab-tier-bookmaker-contract-policy.json')
    diag['ab_tier_bookmaker_contract_policy'] = policy
    diag['inventory_target_expand'] = _load_json(EXPORT_DIR / 'latest-day-inventory-target-expand.json')
    diag['inventory_bookmaker_backfill'] = _load_json(EXPORT_DIR / 'latest-inventory-bookmaker-backfill.json')
    diag['fresh_b_cover_diagnostics'] = _load_json(EXPORT_DIR / 'latest-fresh-b-cover-diagnostics.json')
    diag['rescue_xg_confirmation_enrichment'] = _load_json(EXPORT_DIR / 'latest-rescue-xg-confirmation-enrichment.json')
    diag['bzzoiro_overlap_bridge'] = _load_json(EXPORT_DIR / 'latest-bzzoiro-overlap-bridge.json')
    diag['controlled_fallback_shadow_ranking'] = _load_json(EXPORT_DIR / 'latest-controlled-fallback-shadow-ranking.json')
    diag['a_tier_publication_diagnostics'] = _load_json(EXPORT_DIR / 'latest-a-tier-publication-diagnostics.json')
    diag['workflow_env_contract'] = {
        'a_tier_min_books': _as_int(_policy_tier(policy, 'A').get('min_bookmakers')) or 2,
        'b_tier_min_books': _as_int(_policy_tier(policy, 'B').get('min_bookmakers')) or _as_int(_policy_env(policy, 'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS')) or 1,
        'b_tier_min_context': _as_int(_policy_tier(policy, 'B').get('min_context_sources')) or _as_int(_policy_env(policy, 'CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES')) or 2,
        'sportlogic_enabled': _env_bool('SPORTLOGIC_ENABLED', _env_bool('ENABLE_SPORTLOGIC', False)),
        'day_inventory_sportlogic_enabled': _env_bool('DAY_INVENTORY_ENABLE_SPORTLOGIC', False),
    }
    return payload


def _top_reason_items(reasons: Any, limit: int = 5) -> list[tuple[str, int]]:
    if not isinstance(reasons, dict):
        return []
    rows = [(str(k), _as_int(v)) for k, v in reasons.items() if _as_int(v) > 0 and str(k) not in {'ok', 'promoted'}]
    rows.sort(key=lambda item: item[1], reverse=True)
    return rows[:limit]


def _top_reasons(reasons: Any, limit: int = 5) -> str:
    rows = _top_reason_items(reasons, limit)
    return '; '.join(f'{k} {v}' for k, v in rows) if rows else 'n/a'


def _shadow_top(shadow: dict[str, Any]) -> str:
    rows = shadow.get('top_would_publish_without_daily_cap') if isinstance(shadow.get('top_would_publish_without_daily_cap'), list) else []
    if not rows:
        return 'нет clean-кандидатов после снятия daily cap'
    parts: list[str] = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        point = row.get('point')
        point_text = '' if point in (None, '', 'null') else f' {point}'
        parts.append(
            f"{row.get('home_team') or ''} — {row.get('away_team') or ''} {row.get('selection') or ''}{point_text} "
            f"EV {_as_float(row.get('ev_pct')):+.1f}% edge {_as_float(row.get('edge_pp')):+.1f}pp"
        )
    return '; '.join(parts) if parts else 'нет clean-кандидатов после снятия daily cap'


def _replace_contract_text(text: str, payload: dict[str, Any]) -> str:
    env = (payload.get('diagnostics') or {}).get('workflow_env_contract') if isinstance(payload.get('diagnostics'), dict) else {}
    b_books = max(1, _as_int((env or {}).get('b_tier_min_books') or 1))
    b_ctx = max(1, _as_int((env or {}).get('b_tier_min_context') or 1))
    text = re.sub(r'B-tier 1\+?\s*(?:line|линия)/(?:2|\d+)\+?\s*(?:books?|bookmaker|букмекер(?:а|ов)?)/(?:1|\d+)\+?\s*(?:context|контекст) coverage:', f'B-tier 1+ line/{b_books}+ bookmaker/{b_ctx}+ context coverage:', text)
    text = re.sub(r'B-tier = 1\+ линия/odds-source \+ (?:2\+?|\d+\+?) букмекер(?:а|ов)?/ценов(?:ое|ых) подтверждени(?:е|я) \+ (?:1\+?|\d+\+?) контекст \+ движение линии \+ value\.', f'B-tier = 1+ линия/odds-source + {b_books}+ букмекер/ценовое подтверждение + {b_ctx}+ контекст + движение линии + value.', text)
    text = re.sub(r'Контракт публикации сейчас: A-tier = 2 odds-source \+ 2 букмекера \+ 2 контекста; B-tier = 1 odds-source \+ (?:2|\d+) букмекер(?:а|ов)? \+ (?:1|\d+) контекст;', f'Контракт публикации сейчас: A-tier = 2 odds-source + 2 букмекера + 2 контекста; B-tier = 1 odds-source + {b_books} букмекер + {b_ctx} контекст;', text)
    return text


def _replace_provider_lines(text: str, payload: dict[str, Any]) -> str:
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    bridge = diag.get('bzzoiro_overlap_bridge') if isinstance(diag.get('bzzoiro_overlap_bridge'), dict) else {}
    offers = _as_int(bridge.get('bzzoiro_offer_rows'))
    overlap = _as_int(bridge.get('overlap_same_bucket_rows'))
    if offers > 0:
        text = re.sub(r'(• Bzzoiro: direct req .*?secondary offers )\d+(; overlap odds-api\.io )\d+(; ошибок .*?\.)', rf'\g<1>{offers}\g<2>{overlap}\g<3>', text, count=1)
    env = diag.get('workflow_env_contract') if isinstance(diag.get('workflow_env_contract'), dict) else {}
    if not (env.get('sportlogic_enabled') or env.get('day_inventory_sportlogic_enabled')):
        text = re.sub(r'• SportLogic: .*?(?:\n|$)', '• SportLogic: disabled_by_env; запросы 0; fixtures 0; matched 0; odds req 0; offers 0; ошибок 0; diag disabled_by_env.\n', text, count=1)
    return text


def _replace_headline(text: str, payload: dict[str, Any]) -> str:
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    shadow = diag.get('controlled_fallback_shadow_ranking') if isinstance(diag.get('controlled_fallback_shadow_ranking'), dict) else {}
    if _as_int(shadow.get('blocked_only_by_daily_cap')) > 0:
        return text
    items = _top_reason_items(shadow.get('reason_counts_without_daily_cap'), 1)
    if not items:
        return text
    reason, count = items[0]
    return re.sub(r'(• Главная причина: )controlled fallback daily limit reached:[^\n]*', rf'\1после снятия daily cap — {reason.replace("_", " ")} ({count})', text, count=1)


def _diagnostic_lines(payload: dict[str, Any]) -> list[str]:
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    contract = diag.get('workflow_env_contract') if isinstance(diag.get('workflow_env_contract'), dict) else {}
    shadow = diag.get('controlled_fallback_shadow_ranking') if isinstance(diag.get('controlled_fallback_shadow_ranking'), dict) else {}
    a_diag = diag.get('a_tier_publication_diagnostics') if isinstance(diag.get('a_tier_publication_diagnostics'), dict) else {}
    fresh = diag.get('fresh_b_cover_diagnostics') if isinstance(diag.get('fresh_b_cover_diagnostics'), dict) else {}
    bzz = diag.get('bzzoiro_overlap_bridge') if isinstance(diag.get('bzzoiro_overlap_bridge'), dict) else {}
    enrich = diag.get('rescue_xg_confirmation_enrichment') if isinstance(diag.get('rescue_xg_confirmation_enrichment'), dict) else {}
    expand = diag.get('inventory_target_expand') if isinstance(diag.get('inventory_target_expand'), dict) else {}
    backfill = diag.get('inventory_bookmaker_backfill') if isinstance(diag.get('inventory_bookmaker_backfill'), dict) else {}
    lines: list[str] = []
    if contract:
        lines.append(f"• Active A/B contract: A=2 odds/2 books/2 context; B=1 odds/{max(1, _as_int(contract.get('b_tier_min_books') or 1))} book/{max(1, _as_int(contract.get('b_tier_min_context') or 1))} context.")
    if shadow:
        lines.append(f"• Shadow ranking after daily cap: clean {_as_int(shadow.get('blocked_only_by_daily_cap'))}; daily+other {_as_int(shadow.get('blocked_by_daily_cap_and_other_guards'))}; duplicates removed {_as_int(shadow.get('duplicates_removed'))}; top: {_shadow_top(shadow)}.")
        if shadow.get('reason_counts_without_daily_cap'):
            lines.append(f"• Effective blockers without daily cap: {_top_reasons(shadow.get('reason_counts_without_daily_cap'), 5)}.")
    if a_diag:
        blockers = a_diag.get('tier_a_blocker_counts') if isinstance(a_diag.get('tier_a_blocker_counts'), dict) else {}
        qsrc = a_diag.get('quality_score_sources') if isinstance(a_diag.get('quality_score_sources'), dict) else {}
        note = '' if blockers else '; note A-tier stops before fallback tier checks: no raw/model publishable candidate passed value/quality/movement.'
        lines.append(f"• A-tier blockers: {_top_reasons(blockers, 5)}; quality sources {_top_reasons(qsrc, 3)}{note}.")
    if expand:
        lines.append(f"• Inventory target-expand stage: {expand.get('matches_after', 0)}/{expand.get('target', 300)}; shortfall {_as_int(expand.get('target_shortfall'))}; status {expand.get('status') or 'n/a'}.")
    if backfill:
        lines.append(f"• Bookmaker mapping repair: raw 2+ {_as_int(backfill.get('raw_2plus_matches'))}; normalized {_as_int(backfill.get('normalized_2plus_before'))}→{_as_int(backfill.get('normalized_2plus_after'))}; gap after {_as_int(backfill.get('mapping_gap_after'))}.")
    if bzz:
        lines.append(f"• Bzzoiro overlap bridge: offers {_as_int(bzz.get('bzzoiro_offer_rows'))}; match-overlap {_as_int(bzz.get('overlap_match_rows'))}; same-bucket overlap {_as_int(bzz.get('overlap_same_bucket_rows'))}.")
    if fresh:
        lines.append(f"• Fresh B-cover diagnostic: rows {_as_int(fresh.get('b_cover_rows'))}; with current offer {_as_int(fresh.get('b_cover_with_any_current_offer_match'))}; without current offer {_as_int(fresh.get('b_cover_without_current_offer_match'))}; active {_as_int(fresh.get('active_b_cover_rows'))}; active with offer {_as_int(fresh.get('active_b_cover_with_any_current_offer_match'))}; fresh buckets {_as_int(fresh.get('current_market_buckets_totals_spreads'))}; single-source candidates {_as_int(fresh.get('fallback_single_source_candidates'))}; missing xG candidates {_as_int(fresh.get('fallback_missing_xg_candidates'))}.")
        if fresh.get('promotion_reason_counts'):
            lines.append(f"• Fresh promotion: promoted {_as_int(fresh.get('promotion_promoted_count'))}; top skips {_top_reasons(fresh.get('promotion_reason_counts'), 5)}.")
    if enrich:
        lines.append(f"• Rescue xG/confirmation enrichment: candidates {_as_int(enrich.get('candidates_seen'))}; xG added {_as_int(enrich.get('xg_added'))}; confirmation added {_as_int(enrich.get('confirmation_added'))}; missing context match {_as_int(enrich.get('missing_context_match'))}.")
    return lines


def render(payload: dict[str, Any]) -> str:
    text = _base_render(payload)
    text = _replace_contract_text(text, payload)
    text = _replace_provider_lines(text, payload)
    text = _replace_headline(text, payload)
    lines = _diagnostic_lines(payload)
    if lines:
        block = '🧯 Диагностика новых стопоров\n' + '\n'.join(lines) + '\n\n'
        pattern = r'🧯 Диагностика новых стопоров\n.*?(?=📌 Что это значит\n)'
        text = re.sub(pattern, block, text, count=1, flags=re.S) if re.search(pattern, text, flags=re.S) else text.replace('📌 Что это значит\n', block + '📌 Что это значит\n', 1)
    return text


v9.v8.v7.v5.build_payload = build_payload
v9.v8.v7.v5.render = render
v9.v8.v7.build_payload = build_payload
v9.v8.v7.render = render
_write_status({'status': 'installed', 'renderer': 'v12', 'adds': ['effective_blockers_without_daily_cap', 'shadow_ranking_after_daily_cap', 'a_tier_blockers']})


if __name__ == '__main__':
    raise SystemExit(v9.v8.v7.v5.main())
