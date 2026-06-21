from __future__ import annotations

"""HARIZON Telegram report v11.

Diagnostic-only wrapper over v10. It prevents a misleading daily-cap headline when
all daily-cap candidates are also blocked by line recheck/xG/value guards.
"""

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

V10_PATH = Path(__file__).with_name('send_harizon_telegram_run_report_v10.py')
EXPORT_DIR = Path('.data/exports')
STATUS_PATH = EXPORT_DIR / 'latest-harizon-telegram-run-report-v11-status.json'


def _load_v10() -> Any:
    spec = importlib.util.spec_from_file_location('harizon_report_v10', V10_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load {V10_PATH}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v10 = _load_v10()
_base_build_payload = v10.build_payload
_base_render = v10.render


def _as_int(value: Any) -> int:
    try:
        if value in (None, ''):
            return 0
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


def _top_reasons(reasons: Any, limit: int = 5) -> list[tuple[str, int]]:
    if not isinstance(reasons, dict):
        return []
    rows = [(str(k), _as_int(v)) for k, v in reasons.items() if _as_int(v) > 0]
    rows.sort(key=lambda item: item[1], reverse=True)
    return rows[:limit]


def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    payload['version'] = 'harizon-telegram-report-v11-effective-daily-cap-diagnostics'
    return payload


def _daily_cap_shadow(payload: dict[str, Any]) -> dict[str, Any]:
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    shadow = diag.get('controlled_fallback_shadow_ranking') if isinstance(diag.get('controlled_fallback_shadow_ranking'), dict) else {}
    return shadow


def _replace_misleading_daily_cap_headline(text: str, payload: dict[str, Any]) -> str:
    shadow = _daily_cap_shadow(payload)
    if not shadow:
        return text
    clean = _as_int(shadow.get('blocked_only_by_daily_cap'))
    if clean > 0:
        return text
    top = _top_reasons(shadow.get('reason_counts_without_daily_cap'), limit=1)
    if not top:
        return text
    reason, count = top[0]
    readable = reason.replace('_', ' ')
    return re.sub(
        r'(• Главная причина: )controlled fallback daily limit reached:[^\n]*',
        rf'\1после снятия daily cap — {readable} ({count})',
        text,
        count=1,
    )


def _append_effective_daily_cap_line(text: str, payload: dict[str, Any]) -> str:
    shadow = _daily_cap_shadow(payload)
    if not shadow:
        return text
    top = _top_reasons(shadow.get('reason_counts_without_daily_cap'), limit=5)
    if not top:
        return text
    parts = '; '.join(f'{reason} {count}' for reason, count in top)
    line = f'• Effective blockers without daily cap: {parts}.'
    if line in text:
        return text
    marker = '• A-tier blockers:'
    if marker in text:
        return text.replace(marker, line + '\n' + marker, 1)
    marker = '• Inventory target-expand stage:'
    if marker in text:
        return text.replace(marker, line + '\n' + marker, 1)
    return text


def _append_a_tier_note(text: str, payload: dict[str, Any]) -> str:
    shadow = _daily_cap_shadow(payload)
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    a_diag = diag.get('a_tier_publication_diagnostics') if isinstance(diag.get('a_tier_publication_diagnostics'), dict) else {}
    blockers = a_diag.get('tier_a_blocker_counts') if isinstance(a_diag.get('tier_a_blocker_counts'), dict) else {}
    if blockers or 'A-tier blockers: n/a' not in text:
        return text
    note = '• A-tier note: blockers n/a means A-tier stops before fallback tier checks: coverage exists, but no raw model publishable candidate passed value/quality/movement.'
    if note in text:
        return text
    return text.replace('• A-tier blockers: n/a', note + '\n• A-tier blockers: n/a', 1)


def render(payload: dict[str, Any]) -> str:
    text = _base_render(payload)
    text = _replace_misleading_daily_cap_headline(text, payload)
    text = _append_effective_daily_cap_line(text, payload)
    text = _append_a_tier_note(text, payload)
    return text


v10.v9.v8.v7.v5.build_payload = build_payload
v10.v9.v8.v7.v5.render = render
v10.v9.v8.v7.build_payload = build_payload
v10.v9.v8.v7.render = render
_write_status({
    'status': 'installed',
    'renderer': 'v11',
    'adds': ['effective_blockers_without_daily_cap', 'daily_cap_headline_correction', 'a_tier_no_blocker_note'],
})


if __name__ == '__main__':
    raise SystemExit(v10.v9.v8.v7.v5.main())
