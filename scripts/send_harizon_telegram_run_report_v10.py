from __future__ import annotations

"""HARIZON Telegram report v10.

Adds current blockers diagnostics on top of v9 without changing publication logic:
- fresh B-cover diagnostics instead of legacy-only promotion text;
- explicit policy-aware B-tier/SportLogic wording;
- Bzzoiro overlap-bridge metrics;
- rescue xG/confirmation enrichment summary;
- compact technical status for run-bot/prune/artifact payload.
"""

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any

V9_PATH = Path(__file__).with_name('send_harizon_telegram_run_report_v9.py')
EXPORT_DIR = Path('.data/exports')
STATUS_PATH = EXPORT_DIR / 'latest-harizon-telegram-run-report-v10-status.json'


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
            payload = json.loads(path.read_text(encoding='utf-8'))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def _write_status(payload: dict[str, Any]) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _policy_env_value(policy: dict[str, Any], key: str) -> Any:
    env = policy.get('env') if isinstance(policy.get('env'), dict) else {}
    return env.get(key)


def _contract_tier_payload(policy: dict[str, Any], tier: str) -> dict[str, Any]:
    contract = policy.get('contract') if isinstance(policy.get('contract'), dict) else {}
    payload = contract.get(tier) if isinstance(contract.get(tier), dict) else {}
    return payload


def _contract_min_books(policy: dict[str, Any], tier: str, default: int) -> int:
    return _as_int(_contract_tier_payload(policy, tier).get('min_bookmakers')) or default


def _contract_min_context(policy: dict[str, Any], tier: str, default: int) -> int:
    return _as_int(_contract_tier_payload(policy, tier).get('min_context_sources')) or default


def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    payload['version'] = 'harizon-telegram-report-v10-fresh-diagnostics-and-contract-text'
    diag = payload.setdefault('diagnostics', {})
    policy = _load_json(EXPORT_DIR / 'latest-ab-tier-bookmaker-contract-policy.json')
    diag['inventory_target_expand'] = _load_json(EXPORT_DIR / 'latest-day-inventory-target-expand.json')
    diag['inventory_bookmaker_backfill'] = _load_json(EXPORT_DIR / 'latest-inventory-bookmaker-backfill.json')
    diag['b_cover_candidate_gap'] = _load_json(EXPORT_DIR / 'latest-b-cover-candidate-gap-report.json')
    diag['b_cover_value_promotion'] = _load_json(EXPORT_DIR / 'latest-b-cover-value-promotion.json')
    diag['fresh_b_cover_diagnostics'] = _load_json(EXPORT_DIR / 'latest-fresh-b-cover-diagnostics.json')
    diag['rescue_xg_confirmation_enrichment'] = _load_json(EXPORT_DIR / 'latest-rescue-xg-confirmation-enrichment.json')
    diag['bzzoiro_overlap_bridge'] = _load_json(EXPORT_DIR / 'latest-bzzoiro-overlap-bridge.json')
    diag['run_bot_step_status'] = _load_json(EXPORT_DIR / 'latest-run-bot-step-status.json')
    diag['artifact_prune_status'] = _load_json(EXPORT_DIR / 'latest-artifact-prune-status.json')
    diag['ab_tier_bookmaker_contract_policy'] = policy

    # IMPORTANT: prefer the explicit policy artifact over environment variables.
    # The workflow can still contain older defaults in env, but the apply step
    # writes the source-of-truth policy for the current run. Without this order
    # the Telegram report can display B=2 books while fallback is configured as
    # B=1 book.
    diag['workflow_env_contract'] = {
        'a_tier_min_books': _contract_min_books(policy, 'A', _as_int(_policy_env_value(policy, 'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS')) or _as_int(os.getenv('CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS')) or 2),
        'b_tier_min_books': _contract_min_books(policy, 'B', _as_int(_policy_env_value(policy, 'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS')) or _as_int(os.getenv('CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS')) or 1),
        'b_tier_min_context': _contract_min_context(policy, 'B', _as_int(_policy_env_value(policy, 'CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES')) or _as_int(os.getenv('CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES')) or 1),
        'sportlogic_enabled': _env_bool('SPORTLOGIC_ENABLED', _env_bool('ENABLE_SPORTLOGIC', False)),
        'day_inventory_sportlogic_enabled': _env_bool('DAY_INVENTORY_ENABLE_SPORTLOGIC', False),
    }
    return payload


def _top_reasons(reasons: Any, *, limit: int = 5) -> str:
    if not isinstance(reasons, dict) or not reasons:
        return 'n/a'
    parts: list[str] = []
    for key, value in sorted(reasons.items(), key=lambda item: _as_int(item[1]), reverse=True):
        if key in {'promoted', 'ok'}:
            continue
        parts.append(f'{key} {_as_int(value)}')
        if len(parts) >= limit:
            break
    return '; '.join(parts) if parts else 'n/a'


def _replace_contract_text(text: str, payload: dict[str, Any]) -> str:
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    env = diag.get('workflow_env_contract') if isinstance(diag.get('workflow_env_contract'), dict) else {}
    b_books = max(1, _as_int(env.get('b_tier_min_books') or 1))
    b_ctx = max(1, _as_int(env.get('b_tier_min_context') or 1))

    text = re.sub(
        r'B-tier 1\+?\s*(?:line|линия)/(?:2|\d+)\+?\s*(?:books?|bookmaker|букмекер(?:а|ов)?)/(?:1|\d+)\+?\s*(?:context|контекст) coverage:',
        f'B-tier 1+ line/{b_books}+ bookmaker/{b_ctx}+ context coverage:',
        text,
    )
    text = re.sub(
        r'B-tier = 1\+ линия/odds-source \+ (?:2\+?|\d+\+?) букмекер(?:а|ов)?/ценов(?:ое|ых) подтверждени(?:е|я) \+ (?:1\+?|\d+\+?) контекст \+ движение линии \+ value\.',
        f'B-tier = 1+ линия/odds-source + {b_books}+ букмекер/ценовое подтверждение + {b_ctx}+ контекст + движение линии + value.',
        text,
    )
    text = re.sub(
        r'B-tier = 1 odds-source \+ (?:2|\d+) букмекер(?:а|ов)? \+ (?:1|\d+) контекст',
        f'B-tier = 1 odds-source + {b_books} букмекер + {b_ctx} контекст',
        text,
    )
    text = re.sub(
        r'B-tier теперь считается по (?:1|2|\d+)\+ букмекеру и (?:1|\d+)\+ контексту\.',
        f'B-tier теперь считается по {b_books}+ букмекеру и {b_ctx}+ контексту.',
        text,
    )
    text = re.sub(
        r'Ценовой контракт сейчас: B-tier (?:1|2|\d+)\+ букмекер; A-tier 2\+ букмекера\.',
        f'Ценовой контракт сейчас: B-tier {b_books}+ букмекер; A-tier 2+ букмекера.',
        text,
    )
    text = re.sub(
        r'Контракт публикации сейчас: A-tier = 2 odds-source \+ 2 букмекера \+ 2 контекста; B-tier = 1 odds-source \+ (?:2|\d+) букмекер(?:а|ов)? \+ (?:1|\d+) контекст;',
        f'Контракт публикации сейчас: A-tier = 2 odds-source + 2 букмекера + 2 контекста; B-tier = 1 odds-source + {b_books} букмекер + {b_ctx} контекст;',
        text,
    )
    return text


def _replace_sportlogic_line(text: str, payload: dict[str, Any]) -> str:
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    env = diag.get('workflow_env_contract') if isinstance(diag.get('workflow_env_contract'), dict) else {}
    if bool(env.get('sportlogic_enabled')) or bool(env.get('day_inventory_sportlogic_enabled')):
        return text
    disabled_line = '• SportLogic: disabled_by_env; запросы 0; fixtures 0; matched 0; odds req 0; offers 0; ошибок 0; diag disabled_by_env.'
    pattern = r'• SportLogic: .*?(?:\n|$)'
    if re.search(pattern, text):
        return re.sub(pattern, disabled_line + '\n', text, count=1)
    return text


def _replace_bzzoiro_line(text: str, payload: dict[str, Any]) -> str:
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    bridge = diag.get('bzzoiro_overlap_bridge') if isinstance(diag.get('bzzoiro_overlap_bridge'), dict) else {}
    bridged_offers = _as_int(bridge.get('bzzoiro_offer_rows'))
    bucket_overlap = _as_int(bridge.get('overlap_same_bucket_rows'))
    if bridged_offers <= 0:
        return text
    pattern = r'(• Bzzoiro: direct req .*?secondary offers )\d+(; overlap odds-api\.io )\d+(; ошибок .*?\.)'
    replacement = rf'\g<1>{bridged_offers}\g<2>{bucket_overlap}\g<3>'
    return re.sub(pattern, replacement, text, count=1) if re.search(pattern, text) else text


def _diagnostics_lines(payload: dict[str, Any]) -> list[str]:
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    expand = diag.get('inventory_target_expand') if isinstance(diag.get('inventory_target_expand'), dict) else {}
    backfill = diag.get('inventory_bookmaker_backfill') if isinstance(diag.get('inventory_bookmaker_backfill'), dict) else {}
    fresh = diag.get('fresh_b_cover_diagnostics') if isinstance(diag.get('fresh_b_cover_diagnostics'), dict) else {}
    enrich = diag.get('rescue_xg_confirmation_enrichment') if isinstance(diag.get('rescue_xg_confirmation_enrichment'), dict) else {}
    bzz_bridge = diag.get('bzzoiro_overlap_bridge') if isinstance(diag.get('bzzoiro_overlap_bridge'), dict) else {}
    step_status = diag.get('run_bot_step_status') if isinstance(diag.get('run_bot_step_status'), dict) else {}
    prune = diag.get('artifact_prune_status') if isinstance(diag.get('artifact_prune_status'), dict) else {}
    contract = diag.get('workflow_env_contract') if isinstance(diag.get('workflow_env_contract'), dict) else {}

    lines: list[str] = []
    if step_status and _as_int(step_status.get('status')) != 0:
        lines.append(f"• Run bot status: non-zero {_as_int(step_status.get('status'))}; отчёт/артефакты собраны post-run.")
    if contract:
        lines.append(
            f"• Active A/B contract: A=2 odds/2 books/2 context; "
            f"B=1 odds/{max(1, _as_int(contract.get('b_tier_min_books') or 1))} book/{max(1, _as_int(contract.get('b_tier_min_context') or 1))} context."
        )
    if expand:
        lines.append(
            f"• Inventory target-expand stage: {expand.get('matches_after', 0)}/{expand.get('target', 300)}; "
            f"shortfall {_as_int(expand.get('target_shortfall'))}; status {expand.get('status') or 'n/a'}."
        )
    if backfill:
        lines.append(
            f"• Bookmaker mapping repair: raw 2+ {_as_int(backfill.get('raw_2plus_matches'))}; "
            f"normalized {_as_int(backfill.get('normalized_2plus_before'))}→{_as_int(backfill.get('normalized_2plus_after'))}; "
            f"gap after {_as_int(backfill.get('mapping_gap_after'))}."
        )
    if bzz_bridge:
        lines.append(
            f"• Bzzoiro overlap bridge: offers {_as_int(bzz_bridge.get('bzzoiro_offer_rows'))}; "
            f"match-overlap {_as_int(bzz_bridge.get('overlap_match_rows'))}; "
            f"same-bucket overlap {_as_int(bzz_bridge.get('overlap_same_bucket_rows'))}."
        )
    if fresh:
        active = _as_int(fresh.get('active_b_cover_rows'))
        active_with = _as_int(fresh.get('active_b_cover_with_any_current_offer_match'))
        lines.append(
            f"• Fresh B-cover diagnostic: rows {_as_int(fresh.get('b_cover_rows'))}; "
            f"with current offer {_as_int(fresh.get('b_cover_with_any_current_offer_match'))}; "
            f"without current offer {_as_int(fresh.get('b_cover_without_current_offer_match'))}; "
            f"active {active}; active with offer {active_with}; "
            f"fresh buckets {_as_int(fresh.get('current_market_buckets_totals_spreads'))}; "
            f"single-source candidates {_as_int(fresh.get('fallback_single_source_candidates'))}; "
            f"missing xG candidates {_as_int(fresh.get('fallback_missing_xg_candidates'))}."
        )
        reasons = fresh.get('fallback_reason_counts') if isinstance(fresh.get('fallback_reason_counts'), dict) else {}
        if reasons:
            lines.append(f"• Fresh fallback blockers: {_top_reasons(reasons, limit=6)}.")
        prom_reasons = fresh.get('promotion_reason_counts') if isinstance(fresh.get('promotion_reason_counts'), dict) else {}
        if prom_reasons:
            lines.append(f"• Fresh promotion: promoted {_as_int(fresh.get('promotion_promoted_count'))}; top skips {_top_reasons(prom_reasons, limit=5)}.")
    else:
        lines.append('• Fresh B-cover diagnostic: report missing; проверь latest-fresh-b-cover-diagnostics.json.')
    if enrich:
        lines.append(
            f"• Rescue xG/confirmation enrichment: candidates {_as_int(enrich.get('candidates_seen'))}; "
            f"xG added {_as_int(enrich.get('xg_added'))}; confirmation added {_as_int(enrich.get('confirmation_added'))}; "
            f"missing context match {_as_int(enrich.get('missing_context_match'))}."
        )
    if prune:
        removed = _as_int(prune.get('removed_files')) + _as_int(prune.get('removed_dirs'))
        if removed:
            lines.append(f"• Artifact prune: removed {removed} heavy artifact entries; upload compact.")
    return lines


def render(payload: dict[str, Any]) -> str:
    text = _base_render(payload)
    text = _replace_contract_text(text, payload)
    text = _replace_sportlogic_line(text, payload)
    text = _replace_bzzoiro_line(text, payload)

    lines = _diagnostics_lines(payload)
    if lines:
        block = '🧯 Диагностика новых стопоров\n' + '\n'.join(lines) + '\n\n'
        pattern = r'🧯 Диагностика новых стопоров\n.*?(?=📌 Что это значит\n)'
        if re.search(pattern, text, flags=re.S):
            text = re.sub(pattern, block, text, count=1, flags=re.S)
        else:
            marker = '📌 Что это значит\n'
            if marker in text:
                text = text.replace(marker, block + marker, 1)
            else:
                text += '\n\n' + block.rstrip()
    return text


v9.v8.v7.v5.build_payload = build_payload
v9.v8.v7.v5.render = render
v9.v8.v7.build_payload = build_payload
v9.v8.v7.render = render
_write_status({
    'status': 'installed',
    'renderer': 'v10',
    'adds': ['fresh_b_cover_diagnostics', 'rescue_xg_confirmation_enrichment', 'policy_first_contract_text', 'sportlogic_disabled_by_env', 'bzzoiro_overlap_bridge', 'one_book_b_tier_contract_text'],
})


if __name__ == '__main__':
    raise SystemExit(v9.v8.v7.v5.main())
