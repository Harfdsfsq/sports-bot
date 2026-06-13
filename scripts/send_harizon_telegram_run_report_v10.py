from __future__ import annotations

"""HARIZON Telegram report v10.

Adds post-fix diagnostics for the current blockers:
- day inventory target expansion status;
- raw bookmaker -> normalized inventory backfill gap;
- B-cover -> candidate funnel gap;
- B-cover market-consensus promotion into controlled fallback pool.
"""

import importlib.util
import json
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


def _write_status(payload: dict[str, Any]) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    payload['version'] = 'harizon-telegram-report-v10-inventory-bookmaker-funnel-promotion-diagnostics'
    diag = payload.setdefault('diagnostics', {})
    diag['inventory_target_expand'] = _load_json(EXPORT_DIR / 'latest-day-inventory-target-expand.json')
    diag['inventory_bookmaker_backfill'] = _load_json(EXPORT_DIR / 'latest-inventory-bookmaker-backfill.json')
    diag['b_cover_candidate_gap'] = _load_json(EXPORT_DIR / 'latest-b-cover-candidate-gap-report.json')
    diag['b_cover_value_promotion'] = _load_json(EXPORT_DIR / 'latest-b-cover-value-promotion.json')
    return payload


def _top_promotion_reasons(promotion: dict[str, Any]) -> str:
    reasons = promotion.get('reason_counts') if isinstance(promotion.get('reason_counts'), dict) else {}
    if not reasons:
        return 'n/a'
    parts = []
    for key, value in list(reasons.items())[:4]:
        if key == 'promoted':
            continue
        parts.append(f'{key} {_as_int(value)}')
    return '; '.join(parts) if parts else 'n/a'


def render(payload: dict[str, Any]) -> str:
    text = _base_render(payload)
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    expand = diag.get('inventory_target_expand') if isinstance(diag.get('inventory_target_expand'), dict) else {}
    backfill = diag.get('inventory_bookmaker_backfill') if isinstance(diag.get('inventory_bookmaker_backfill'), dict) else {}
    gap = diag.get('b_cover_candidate_gap') if isinstance(diag.get('b_cover_candidate_gap'), dict) else {}
    promotion = diag.get('b_cover_value_promotion') if isinstance(diag.get('b_cover_value_promotion'), dict) else {}
    lines: list[str] = []
    if expand:
        lines.append(
            f"• Inventory target repair: {expand.get('matches_after', 0)}/{expand.get('target', 300)}; "
            f"shortfall {_as_int(expand.get('target_shortfall'))}; status {expand.get('status') or 'n/a'}."
        )
    if backfill:
        lines.append(
            f"• Bookmaker mapping repair: raw 2+ {_as_int(backfill.get('raw_2plus_matches'))}; "
            f"normalized {_as_int(backfill.get('normalized_2plus_before'))}→{_as_int(backfill.get('normalized_2plus_after'))}; "
            f"gap after {_as_int(backfill.get('mapping_gap_after'))}."
        )
    if promotion:
        inventory_seen = _as_int(promotion.get('inventory_rows_seen'))
        selected_path = ''
        inv_load = promotion.get('inventory_load') if isinstance(promotion.get('inventory_load'), dict) else {}
        if inv_load:
            selected_path = str(inv_load.get('selected_path') or '')
        selected_b_cover = _as_int(inv_load.get('selected_b_cover_rows')) if inv_load else 0
        suffix = f"; inventory rows {inventory_seen}" if inventory_seen or selected_path else ''
        if selected_b_cover:
            suffix += f"; selected B-cover rows {selected_b_cover}"
        if selected_path:
            suffix += f" from {selected_path}"
        status = str(promotion.get('status') or 'ok')
        status_prefix = f"status {status}; " if status and status != 'ok' else ''
        lines.append(
            f"• B-cover promotion: {status_prefix}considered {_as_int(promotion.get('considered_b_cover_rows'))}; "
            f"promoted {_as_int(promotion.get('promoted_count'))}; "
            f"top skips {_top_promotion_reasons(promotion)}{suffix}."
        )
    else:
        lines.append(
            "• B-cover promotion: report missing; script likely failed before writing "
            "latest-b-cover-value-promotion.json."
        )
    if gap:
        reasons = gap.get('reason_counts') if isinstance(gap.get('reason_counts'), dict) else {}
        no_candidate = _as_int(reasons.get('b_cover_no_candidate')) + _as_int(reasons.get('b_cover_no_candidate_missing_xg_like_context'))
        lines.append(
            f"• B-cover funnel: B-cover {_as_int(gap.get('b_cover_rows'))}; candidates seen {_as_int(gap.get('candidate_rows_seen'))}; "
            f"B-covered без кандидата {no_candidate}."
        )
    if lines:
        marker = '📌 Что это значит\n'
        block = '🧯 Диагностика новых стопоров\n' + '\n'.join(lines) + '\n\n'
        if marker in text:
            text = text.replace(marker, block + marker, 1)
        else:
            text += '\n\n' + block.rstrip()
    return text


v9.v8.v7.v5.build_payload = build_payload
v9.v8.v7.v5.render = render
v9.v8.v7.build_payload = build_payload
v9.v8.v7.render = render
_write_status({'status': 'installed', 'renderer': 'v10', 'adds': ['inventory_target_expand', 'inventory_bookmaker_backfill', 'b_cover_candidate_gap', 'b_cover_value_promotion']})


if __name__ == '__main__':
    raise SystemExit(v9.v8.v7.v5.main())
